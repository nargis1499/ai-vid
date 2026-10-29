"""Offline tests for narration-preserving visual plans and stock queries."""
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

# The planning tests make no HTTP calls; keep them runnable in minimal dev environments.
try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    requests_stub = types.ModuleType("requests")
    requests_stub.RequestException = Exception
    sys.modules["requests"] = requests_stub

from script_generator import ScriptGenerator
from stock_search import search_queries


NEIGHBOR_SCENES = [
    {
        "narration": "A small friendly greeting can make your neighbors feel comfortable around you.",
        "core_meaning": "A brief warm greeting helps neighbors feel at ease.",
        "visible_action": "Two neighbors greet each other with a friendly wave.",
        "subjects": ["two neighbors"], "setting": "suburban front yard",
        "queries": ["two neighbors greeting outside a suburban home", "friendly man waving across a front yard", "neighbors saying hello on a residential street"],
    },
    {
        "narration": "Introduce yourself and learn your neighbors' names during a brief conversation outside.",
        "core_meaning": "A short introduction starts a neighborly connection.",
        "visible_action": "Two neighbors introduce themselves and shake hands.",
        "subjects": ["two neighbors"], "setting": "front porch",
        "queries": ["neighbors introducing themselves on front porch", "two homeowners shaking hands outside", "neighbors chatting beside a garden fence"],
    },
    {
        "narration": "Choose a calm moment to talk instead of interrupting someone who is rushing.",
        "core_meaning": "Approach a neighbor when they are not in a hurry.",
        "visible_action": "One neighbor waits as another finishes carrying bags indoors.",
        "subjects": ["two neighbors", "grocery bags"], "setting": "home entrance",
        "queries": ["neighbor waiting outside while homeowner carries groceries", "two neighbors talking calmly on porch", "homeowner greeting neighbor after unpacking bags"],
    },
    {
        "narration": "Listen without interrupting while your neighbor explains a concern.",
        "core_meaning": "Give a neighbor full attention while they speak.",
        "visible_action": "One neighbor listens attentively as another explains a concern.",
        "subjects": ["two neighbors"], "setting": "front garden",
        "queries": ["neighbor listening attentively in front garden", "two homeowners talking beside garden fence", "person listening to neighbor on porch"],
    },
    {
        "narration": "Respect your neighbor's privacy by giving them space and avoiding intrusive questions.",
        "core_meaning": "Respect boundaries and give a neighbor personal space.",
        "visible_action": "A neighbor politely gives another person space at the doorway.",
        "subjects": ["two neighbors"], "setting": "residential doorway",
        "queries": ["neighbor giving another person space at doorway", "neighbors ending conversation politely outside", "homeowner waving goodbye from a respectful distance"],
    },
    {
        "narration": "Offering to carry groceries or help with a small task builds trust.",
        "core_meaning": "Practical help makes everyday neighbor interactions easier.",
        "visible_action": "One neighbor helps another carry grocery bags to the door.",
        "subjects": ["two neighbors", "grocery bags"], "setting": "residential front door",
        "queries": ["neighbor helping carry grocery bags to house", "two neighbors carrying a box together outdoors", "neighbor helping with yard work at home"],
    },
    {
        "narration": "Keep music low and quiet down during evening hours.",
        "core_meaning": "Lower evening noise so nearby households can relax.",
        "visible_action": "A resident turns down a stereo in the evening.",
        "subjects": ["resident", "stereo"], "setting": "living room at dusk",
        "queries": ["resident lowering stereo volume in living room", "person turning down music at home at night", "quiet evening in a residential living room"],
    },
    {
        "narration": "Stay calm and talk through a disagreement about a shared fence.",
        "core_meaning": "Discuss a property disagreement calmly and respectfully.",
        "visible_action": "Two neighbors calmly discuss a fence between their yards.",
        "subjects": ["two neighbors", "wooden fence"], "setting": "shared backyard boundary",
        "queries": ["neighbors calmly discussing wooden fence in yard", "two homeowners talking beside property fence", "neighbors reviewing a fence line together"],
    },
    {
        "narration": "Use clear, polite words when discussing a problem with a neighbor.",
        "core_meaning": "Raise a concern clearly and politely.",
        "visible_action": "Two neighbors speak calmly face to face outside.",
        "subjects": ["two neighbors"], "setting": "residential sidewalk",
        "queries": ["two neighbors having a calm talk on sidewalk", "homeowners discussing a concern outside house", "neighbors speaking politely beside front gate"],
    },
    {
        "narration": "Send a brief, friendly text message to coordinate a neighborhood plan.",
        "core_meaning": "Use a concise text to coordinate a local activity.",
        "visible_action": "A person types a short message on a smartphone at home.",
        "subjects": ["person", "smartphone"], "setting": "front porch",
        "queries": ["person texting on smartphone on front porch", "homeowner typing message on phone outdoors", "person checking phone beside front door"],
    },
    {
        "narration": "Keep a shared hallway clean and leave room for others to pass.",
        "core_meaning": "Maintain shared areas and keep walkways clear.",
        "visible_action": "A resident tidies an apartment hallway as a neighbor passes.",
        "subjects": ["resident", "neighbor"], "setting": "shared apartment hallway",
        "queries": ["resident sweeping shared apartment hallway", "neighbor keeping corridor clear for others", "person tidying common hallway as neighbor walks by"],
    },
    {
        "narration": "Build a relationship over time by checking in and sharing a friendly chat.",
        "core_meaning": "Small regular conversations help neighbors get to know each other.",
        "visible_action": "Neighbors greet each other and chat beside a shared garden.",
        "subjects": ["two neighbors"], "setting": "community garden",
        "queries": ["neighbors chatting beside a community garden", "two homeowners greeting each other outside", "neighbors sharing a conversation near garden beds"],
    },
]


class VisualPlanningTests(unittest.TestCase):
    def test_twelve_neighbor_scenes_keep_narration_and_make_specific_queries(self):
        for index, example in enumerate(NEIGHBOR_SCENES, 1):
            with self.subTest(scene=index):
                scene = {"narration": example["narration"], "duration": 10, "search_queries": []}
                raw_plan = {
                    "core_meaning": example["core_meaning"],
                    "visual_objective": example["visible_action"],
                    "visible_action": example["visible_action"],
                    "subjects": example["subjects"],
                    "setting": example["setting"],
                    "primary_query": example["queries"][0],
                    "alternative_queries": example["queries"][1:],
                    "avoid_terms": ["office meeting", "business presentation"],
                }
                plan = ScriptGenerator._normalize_visual_plan(scene, f"scene-{index:02d}", raw_plan)
                self.assertEqual(plan["original_narration"], example["narration"])
                self.assertEqual(scene["narration"], example["narration"])
                queries = [plan["primary_query"], *plan["alternative_queries"]]
                self.assertGreaterEqual(len(queries), 3)
                self.assertLessEqual(len(queries), 5)
                self.assertTrue(all(len(query) <= 100 for query in queries))
                api_queries = search_queries({"visual_plan": plan, "narration": scene["narration"]}, max_queries=5)
                self.assertEqual(api_queries, queries)

    def test_generic_model_queries_are_replaced_by_concrete_scene_fallbacks(self):
        narration = "Offering to carry groceries or help with a small task builds trust."
        scene = {"narration": narration, "duration": 10, "search_queries": []}
        raw_plan = {
            "primary_query": "neighbors communication",
            "alternative_queries": ["trust between neighbors", "respectful relationships"],
            "visible_action": "one neighbor helping another carry grocery bags",
            "subjects": ["two neighbors", "grocery bags"],
            "setting": "residential front door",
        }
        plan = ScriptGenerator._normalize_visual_plan(scene, "scene-01", raw_plan)
        self.assertIn("grocery bags", plan["primary_query"])
        self.assertNotIn("communication", plan["primary_query"].lower())
        self.assertNotIn("trust", plan["primary_query"].lower())

    def test_visual_plan_request_maps_ids_and_never_rewrites_narration(self):
        scene = {"narration": "Two neighbors greet each other beside a front gate.", "duration": 8, "search_queries": []}
        generated = {
            "visual_plans": [{
                "scene_id": "scene-01",
                "original_narration": "incorrect paraphrase from model",
                "core_meaning": "A warm greeting opens a conversation.",
                "visual_objective": "Two neighbors greeting outside their homes.",
                "visible_action": "Two neighbors waving and saying hello.",
                "subjects": ["two neighbors"],
                "setting": "residential front gate",
                "primary_query": "two neighbors greeting outside suburban home",
                "alternative_queries": [
                    "friendly neighbor waving across front yard",
                    "neighbors smiling and saying hello on residential street",
                ],
                "avoid_terms": ["office meeting", "conference"],
                "required_orientation": "portrait",
                "target_duration": 99,
            }]
        }
        with patch("script_generator.complete_json", return_value=(json.dumps(generated), "mock visual planner")) as completion:
            planner = ScriptGenerator()
            source = planner._plan_scene_visuals([scene], "Neighbor greetings")
        self.assertEqual(source, "mock visual planner")
        self.assertEqual(scene["narration"], "Two neighbors greet each other beside a front gate.")
        self.assertEqual(scene["visual_plan"]["original_narration"], scene["narration"])
        self.assertEqual(scene["visual_plan"]["required_orientation"], "landscape")
        self.assertEqual(scene["visual_plan"]["target_duration"], 8)
        completion.assert_called_once()

    def test_generation_publishes_narration_while_visual_plan_is_pending(self):
        narration = "Two neighbors greet each other beside a front gate."
        script_payload = {
            "title": "Neighbor greetings", "description": "A short guide to warm greetings.",
            "tags": ["neighbors", "greetings"],
            "scenes": [{"scene_number": 1, "duration": 10, "narration": narration}],
        }
        visual_payload = {"visual_plans": [{
            "scene_id": "scene-01", "core_meaning": "A warm greeting opens a conversation.",
            "visual_objective": "Two neighbors greeting outside their homes.",
            "visible_action": "Two neighbors waving and saying hello.",
            "subjects": ["two neighbors"], "setting": "residential front gate",
            "primary_query": "two neighbors greeting outside suburban home",
            "alternative_queries": [
                "friendly neighbor waving across front yard",
                "neighbors smiling and saying hello on residential street",
            ], "avoid_terms": ["office meeting"],
        }]}
        updates = []
        calls = 0

        def complete_json(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return json.dumps(script_payload), "mock narration provider"
            self.assertEqual(updates[-1]["phase"], "visual_planning")
            self.assertEqual(updates[-1]["scenes"][0]["narration"], narration)
            return json.dumps(visual_payload), "mock visual planner"

        with patch("script_generator.complete_json", side_effect=complete_json):
            script = ScriptGenerator().generate_script("Neighbor greetings", 10, progress_callback=updates.append)

        self.assertEqual(script["scenes"][0]["narration"], narration)
        self.assertEqual([update["phase"] for update in updates], ["visual_planning", "planned"])
        self.assertEqual(updates[-1]["scenes"][0]["primary_query"], visual_payload["visual_plans"][0]["primary_query"])
        self.assertEqual(calls, 2)

    def test_planning_preview_bounds_narration_and_query_fields(self):
        preview = ScriptGenerator._planning_preview([{
            "scene_number": 1, "duration": 10, "narration": "n" * 1000,
            "visual_plan": {"primary_query": "q" * 200, "alternative_queries": ["a" * 200] * 10},
        }], "planned", "ready")
        self.assertEqual(len(preview["scenes"][0]["narration"]), 700)
        self.assertEqual(len(preview["scenes"][0]["primary_query"]), 100)
        self.assertEqual(len(preview["scenes"][0]["alternative_queries"]), 4)
        self.assertTrue(all(len(query) <= 100 for query in preview["scenes"][0]["alternative_queries"]))

    def test_plan_validator_rejects_missing_ids_and_abstract_queries(self):
        with self.assertRaises(ValueError):
            ScriptGenerator._validate_visual_plan_payload({"visual_plans": []}, ["scene-01"])
        payload = {"visual_plans": [{
            "scene_id": "scene-01", "primary_query": "neighbors communication",
            "alternative_queries": ["trust between neighbors", "respectful relationships"],
        }]}
        with self.assertRaises(ValueError):
            ScriptGenerator._validate_visual_plan_payload(payload, ["scene-01"])

    def test_planner_failure_uses_short_fallback_and_preserves_narration(self):
        narration = "Offering practical help to a neighbor can make everyday life easier and create a stronger sense of community over time."
        scene = {"narration": narration, "duration": 10, "search_queries": []}
        updates = []
        with patch("script_generator.complete_json", side_effect=RuntimeError("offline")):
            planner = ScriptGenerator()
            source = planner._plan_scene_visuals([scene], "Helping neighbors", progress_callback=updates.append)
        self.assertTrue(source.startswith("structured-plan fallback"))
        self.assertEqual([update["phase"] for update in updates], ["visual_planning", "fallback"])
        self.assertEqual(scene["narration"], narration)
        self.assertNotEqual(scene["visual_plan"]["primary_query"], narration)
        self.assertLessEqual(len(scene["visual_plan"]["primary_query"]), 100)

    def test_api_queries_use_plan_only_not_full_narration(self):
        plan = {"primary_query": "neighbor helping carry groceries to house", "alternative_queries": ["neighbors carrying a box outdoors", "person helping with yard work"]}
        scene = {"visual_plan": plan, "search_keywords": "neighbor grocery bags", "visual_description": "A person brings groceries to a front door", "narration": "Offering practical help strengthens a relationship between neighbors."}
        queries = search_queries(scene, max_queries=5)
        self.assertEqual(len(queries), 3)
        self.assertEqual(queries[0], plan["primary_query"])
        self.assertNotIn(scene["narration"], queries)
        self.assertNotIn(scene["visual_description"], queries)

        legacy_scene = {"narration": "Offering practical help strengthens a relationship between neighbors and builds lasting trust."}
        legacy_queries = search_queries(legacy_scene, max_queries=5)
        self.assertEqual(len(legacy_queries), 1)
        self.assertLess(len(legacy_queries[0]), len(legacy_scene["narration"]))


if __name__ == "__main__":
    unittest.main()
