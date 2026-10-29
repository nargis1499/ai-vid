"""Offline stock-ranking and fallback tests using synthetic provider metadata."""
import json
import sys
import types
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "stock_ranking_examples.json"
sys.path.insert(0, str(BACKEND_DIR))

try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    requests_stub = types.ModuleType("requests")
    requests_stub.RequestException = Exception
    requests_stub.get = lambda *args, **kwargs: (_ for _ in ()).throw(Exception("HTTP disabled in offline tests"))
    requests_stub.post = requests_stub.get
    sys.modules["requests"] = requests_stub

# Keep VideoFetcher importable in the minimal test sandbox; tests patch all network/media IO.
try:
    import common  # noqa: F401
except ModuleNotFoundError:
    common_stub = types.ModuleType("common")
    common_stub.DIRS = {"clips": Path("/tmp"), "images": Path("/tmp")}
    sys.modules["common"] = common_stub
try:
    import pixabay_client  # noqa: F401
except ModuleNotFoundError:
    pixabay_stub = types.ModuleType("pixabay_client")
    class _PixabayClient:
        def __init__(self, api_key=""):
            self.api_key = api_key
    pixabay_stub.PixabayClient = _PixabayClient
    sys.modules["pixabay_client"] = pixabay_stub

from stock_search import (
    EARLY_STOP_SEMANTIC_RELEVANCE,
    MIN_STOCK_RELEVANCE,
    rank_stock_candidates,
    semantic_score_breakdown,
    stable_candidate_key,
)
from video_fetcher import VideoFetcher
sys.path.insert(0, str(BACKEND_DIR / "scripts"))
from evaluate_stock_ranking import evaluate_cases


CASES = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


class StockRankingTests(unittest.TestCase):
    def test_all_ten_realistic_scenarios_select_the_expected_candidate_or_fallback(self):
        for case in CASES:
            with self.subTest(case=case["name"]):
                ranked = rank_stock_candidates(case["scene"], case["candidates"])
                selected_identity = ranked[0]["identity"] if ranked else None
                self.assertEqual(selected_identity, case["expected_identity"])
                if case.get("expected_unique_candidates") is not None:
                    self.assertEqual(len(ranked), case["expected_unique_candidates"])
                if selected_identity:
                    self.assertGreaterEqual(ranked[0]["semantic_relevance"], MIN_STOCK_RELEVANCE)

    def test_synthetic_positive_negative_pairs_calibrate_the_040_semantic_gate(self):
        rejected = {
            "meeting-1", "warehouse-1", "fight-1", "surveil-1", "computer-1",
            "industrial-clean-1", "hires-office", "blank-1", "poor-office", "poor-city",
        }
        for case in CASES:
            for candidate in case["candidates"]:
                score = semantic_score_breakdown(case["scene"], candidate.get("metadata"))["score"]
                if candidate["identity"] == case["expected_identity"]:
                    self.assertGreaterEqual(score, MIN_STOCK_RELEVANCE, case["name"])
                if candidate["identity"] in rejected:
                    self.assertLess(score, MIN_STOCK_RELEVANCE, candidate["identity"])

    def test_semantic_relevance_is_a_hard_gate_before_technical_quality(self):
        case = CASES[6]
        ranked = rank_stock_candidates(case["scene"], case["candidates"])
        relevant, irrelevant = case["candidates"]
        self.assertEqual(ranked[0]["identity"], "lowres-greet")
        self.assertLess(ranked[0]["technical_suitability"], 0.70)
        irrelevant_score = semantic_score_breakdown(case["scene"], irrelevant["metadata"])["score"]
        self.assertLess(irrelevant_score, MIN_STOCK_RELEVANCE)
        generic_talk_score = semantic_score_breakdown(case["scene"], {"description": "people talking around a table"})["score"]
        self.assertLess(generic_talk_score, MIN_STOCK_RELEVANCE)
        self.assertGreaterEqual(ranked[0]["semantic_relevance"], MIN_STOCK_RELEVANCE)
        image_ranked = rank_stock_candidates(case["scene"], case["candidates"], media_type="image")
        self.assertEqual(image_ranked[0]["identity"], "lowres-greet")

    def test_duration_and_resolution_are_only_tie_breakers_after_semantics(self):
        source_scene = CASES[0]["scene"]
        scene = {**source_scene, "visual_plan": {**source_scene["visual_plan"], "target_duration": 10}}
        evidence = {"title": "neighbors greeting outside home", "description": "two people wave at a suburban house"}
        candidates = [
            {"provider": "Pexels", "identity": "short-low", "metadata": evidence, "url": "https://media.example/short-low.mp4", "width": 640, "height": 360, "duration": 4, "rank": 0},
            {"provider": "Pixabay", "identity": "full-hd", "metadata": evidence, "url": "https://media.example/full-hd.mp4", "width": 1920, "height": 1080, "duration": 12, "rank": 1},
        ]
        ranked = rank_stock_candidates(scene, candidates)
        self.assertEqual(ranked[0]["identity"], "full-hd")
        self.assertEqual(ranked[0]["semantic_relevance"], ranked[1]["semantic_relevance"])
        self.assertGreater(ranked[0]["technical_suitability"], ranked[1]["technical_suitability"])

    def test_missing_title_or_tags_can_still_pass_on_relevant_description(self):
        case = CASES[7]
        candidate = case["candidates"][0]
        self.assertEqual(candidate["metadata"].keys(), {"description"})
        ranked = rank_stock_candidates(case["scene"], [candidate])
        self.assertEqual(ranked[0]["identity"], "listen-1")

    def test_cross_query_duplicate_is_collapsed_and_prior_asset_is_avoided(self):
        case = CASES[8]
        ranked = rank_stock_candidates(case["scene"], case["candidates"])
        self.assertEqual(len(ranked), 2)
        shared_key = stable_candidate_key(case["candidates"][0])
        self.assertEqual(shared_key, stable_candidate_key(case["candidates"][1]))
        unique_ranked = rank_stock_candidates(case["scene"], case["candidates"], used_assets={shared_key})
        self.assertEqual(unique_ranked[0]["identity"], "unique-1")
        strong = case["candidates"][0]
        weaker_unique = {"provider": "Pixabay", "identity": "weak-unique", "metadata": {"tags": "neighbor standing outside home"}, "url": "https://media.example/weak-unique.mp4", "width": 1920, "height": 1080, "duration": 10}
        reuse_if_margin_is_large = rank_stock_candidates(case["scene"], [strong, weaker_unique], used_assets={stable_candidate_key(strong)})
        self.assertEqual(reuse_if_margin_is_large[0]["identity"], "duplicate-1")

    def test_provider_neutral_metadata_gets_same_score_and_no_provider_bonus(self):
        scene = CASES[0]["scene"]
        evidence = {"title": "neighbors greeting outside home", "description": "two people wave at a suburban house"}
        score = semantic_score_breakdown(scene, evidence)["score"]
        self.assertEqual(score, semantic_score_breakdown(scene, dict(reversed(list(evidence.items()))))["score"])
        pexels = {"provider": "Pexels", "identity": "p", "metadata": evidence, "url": "https://media.example/z.mp4", "width": 1280, "height": 720, "duration": 10, "query_rank": 0, "rank": 0}
        pixabay = {"provider": "Pixabay", "identity": "x", "metadata": evidence, "url": "https://media.example/a.mp4", "width": 1280, "height": 720, "duration": 10, "query_rank": 0, "rank": 0}
        ranked = rank_stock_candidates(scene, [pexels, pixabay])
        self.assertEqual(ranked[0]["provider"], "Pixabay")
        self.assertEqual(ranked[0]["semantic_relevance"], ranked[1]["semantic_relevance"])
        self.assertEqual(ranked[0]["technical_suitability"], ranked[1]["technical_suitability"])

    def test_evaluation_report_is_reproducible_and_explicitly_metadata_only(self):
        report = evaluate_cases(CASES)
        self.assertEqual(report["mode"], "deterministic-fixtures")
        self.assertFalse(report["visual_verification_performed"])
        self.assertEqual(len(report["scenes"]), 10)
        first = report["scenes"][0]
        self.assertEqual(first["queries_attempted"][0], CASES[0]["candidates"][0]["query"])
        self.assertEqual(first["selected_candidate"]["provider"], "Pexels")
        self.assertIn("semantic_relevance", first["selected_candidate"])
        self.assertIn("technical_suitability", first["selected_candidate"])
        self.assertTrue(report["scenes"][-1]["fallback_used"])

    def test_video_search_requires_two_queries_and_three_strong_candidates_to_early_stop(self):
        scene = CASES[0]["scene"]
        fetcher = VideoFetcher()
        fetcher._search_pixabay = lambda *args: []
        calls = []
        base_metadata = {"title": "neighbors greeting outside home", "description": "two people wave at a suburban house"}
        def search(query, query_rank, min_duration):
            calls.append(query)
            count = 2 if query_rank == 0 else 1
            return [
                {"provider": "Pexels", "identity": f"strong-{query_rank}-{i}", "metadata": base_metadata,
                 "video": {"duration": 12}, "file": {"url": f"https://media.example/{query_rank}-{i}.mp4", "width": 1280, "height": 720},
                 "query": query, "query_rank": query_rank, "rank": i}
                for i in range(count)
            ]
        fetcher._search_pexels = search
        fetcher._download_clip = lambda url, index: "/tmp/selected.mp4"
        result = fetcher.fetch_clip(scene, 0)
        self.assertEqual(result, "/tmp/selected.mp4")
        self.assertEqual(calls, [scene["visual_plan"]["primary_query"], scene["visual_plan"]["alternative_queries"][0]])
        self.assertEqual(len(calls), 2)
        self.assertGreaterEqual(fetcher.last_selection["semantic_relevance"], EARLY_STOP_SEMANTIC_RELEVANCE)
        self.assertEqual(fetcher.last_selection["query"], calls[0])

    def test_video_fetcher_avoids_reusing_a_good_asset_when_an_alternative_exists(self):
        scene = CASES[0]["scene"]
        fetcher = VideoFetcher()
        candidates = [
            {"provider": "Pexels", "identity": "reusable", "metadata": {"title": "neighbors greeting outside home", "description": "two people wave at house"},
             "video": {"duration": 12}, "file": {"url": "https://media.example/reusable.mp4", "width": 1280, "height": 720}, "rank": 0},
            {"provider": "Pixabay", "identity": "alternative", "metadata": {"tags": "neighbors greet outside suburban house, waving hello"},
             "video": {"duration": 12}, "file": {"url": "https://media.example/alternative.mp4", "width": 1280, "height": 720}, "rank": 1},
        ]
        fetcher._search_pexels = lambda query, query_rank, duration: candidates
        fetcher._search_pixabay = lambda *args: []
        fetcher._download_clip = lambda url, index: f"/tmp/clip-{index}.mp4"
        self.assertEqual(fetcher.fetch_clip(scene, 0), "/tmp/clip-0.mp4")
        self.assertEqual(fetcher.last_selection["identity"], "reusable")
        self.assertEqual(fetcher.fetch_clip(scene, 1), "/tmp/clip-1.mp4")
        self.assertEqual(fetcher.last_selection["identity"], "alternative")

    def test_weak_primary_result_continues_to_alternative_and_unusable_pool_falls_back(self):
        scene = CASES[0]["scene"]
        fetcher = VideoFetcher()
        attempts = []
        def search(query, query_rank, min_duration):
            attempts.append(query)
            if query_rank == 0:
                metadata = {"title": "office meeting", "description": "corporate team talking"}
                identity = "wrong-office"
            else:
                metadata = {"title": "neighbors greeting outside home", "description": "two people wave at a house"}
                identity = "right-greeting"
            return [{"provider": "Pexels", "identity": identity, "metadata": metadata,
                     "video": {"duration": 12}, "file": {"url": f"https://media.example/{identity}.mp4", "width": 1280, "height": 720},
                     "query": query, "query_rank": query_rank, "rank": 0}]
        fetcher._search_pexels = search
        fetcher._search_pixabay = lambda *args: []
        fetcher._download_clip = lambda url, index: "/tmp/alternative.mp4"
        self.assertEqual(fetcher.fetch_clip(scene, 0), "/tmp/alternative.mp4")
        self.assertEqual(attempts[0], scene["visual_plan"]["primary_query"])
        self.assertEqual(attempts[1], scene["visual_plan"]["alternative_queries"][0])
        self.assertEqual(fetcher.last_selection["identity"], "right-greeting")

        poor = VideoFetcher()
        poor._search_pexels = lambda query, query_rank, duration: [
            {"provider": "Pexels", "identity": "poor", "metadata": {"title": "office conference room"},
             "video": {"duration": 10}, "file": {"url": "https://media.example/poor.mp4", "width": 1920, "height": 1080},
             "query": query, "query_rank": query_rank, "rank": 0}
        ]
        poor._search_pixabay = lambda *args: []
        poor._download_clip = lambda *args: self.fail("Poor candidate must not be downloaded")
        self.assertIsNone(poor.fetch_clip(scene, 0))
        self.assertIsNone(poor.last_selection)

    def test_failed_download_moves_to_next_ranked_candidate(self):
        scene = CASES[0]["scene"]
        fetcher = VideoFetcher()
        base = {"provider": "Pexels", "metadata": {"title": "neighbors greeting outside home", "description": "two people wave at house"},
                "video": {"duration": 12}, "width": 1280, "height": 720, "query_rank": 0}
        candidates = [
            {**base, "identity": "top", "file": {"url": "https://media.example/top.mp4", "width": 1280, "height": 720}, "rank": 0},
            {**base, "identity": "next", "metadata": {"title": "neighbors greet outside a suburban home", "description": "two people waving"},
             "file": {"url": "https://media.example/next.mp4", "width": 1280, "height": 720}, "rank": 1},
        ]
        fetcher._search_pexels = lambda query, query_rank, duration: candidates if query_rank == 0 else []
        fetcher._search_pixabay = lambda *args: []
        attempts = []
        def download(url, index):
            attempts.append(url)
            if len(attempts) == 1:
                raise OSError("mock inaccessible")
            return "/tmp/second-choice.mp4"
        fetcher._download_clip = download
        self.assertEqual(fetcher.fetch_clip(scene, 0), "/tmp/second-choice.mp4")
        self.assertEqual(len(attempts), 2)
        self.assertEqual(fetcher.last_selection["identity"], "next")

        inaccessible = VideoFetcher()
        inaccessible._search_pexels = lambda query, query_rank, duration: candidates if query_rank == 0 else []
        inaccessible._search_pixabay = lambda *args: []
        inaccessible._download_clip = lambda *args: (_ for _ in ()).throw(OSError("all downloads fail"))
        self.assertIsNone(inaccessible.fetch_clip(scene, 0))
        self.assertIsNone(inaccessible.last_selection)


if __name__ == "__main__":
    unittest.main()
