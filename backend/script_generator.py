"""Ollama-backed scene script generation with a reliable offline fallback."""
import json, re, os
import requests
from common import env_bool

class ScriptGenerator:
    def __init__(self):
        self.enabled = env_bool("USE_OLLAMA", True)
        self.url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
        self.model = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

    def generate_script(self, title: str, duration: int) -> dict:
        count = max(3, min(24, int(duration) // 10))
        if self.enabled:
            prompt = f'''Create a compelling YouTube narration plan for the title: {title!r}. Target total duration {duration} seconds. Return ONLY valid JSON, no markdown, matching: {{"title":"...","description":"...","scenes":[{{"scene_number":1,"duration":8,"narration":"2-3 spoken sentences","visual_description":"specific cinematic visual","search_keywords":"2-3 concrete search words","text_overlay":"short phrase"}}],"tags":["tag"]}}. Make exactly {count} scenes; scene durations should sum to about {duration} seconds. Use engaging accurate language.'''
            try:
                response = requests.post(self.url + "/api/generate", json={"model": self.model, "prompt": prompt, "stream": False, "keep_alive": -1 if env_bool("OLLAMA_KEEP_ALIVE", True) else 0, "options": {"temperature": .7}}, timeout=120)
                response.raise_for_status()
                raw = response.json().get("response", "")
                raw = re.sub(r"^\s*```(?:json)?|```\s*$", "", raw.strip(), flags=re.I)
                data = json.loads(raw[raw.find("{"):raw.rfind("}")+1])
                scenes = data.get("scenes", [])
                if scenes:
                    return self._normalize(data, title, duration, count)
            except Exception as exc:
                print(f"[script] Ollama unavailable; using template: {exc}")
        return self._fallback(title, duration, count)

    def generate_custom_script(self, title: str, custom_text: str, duration: int) -> dict:
        """Keep creator narration verbatim while planning visuals scene by scene."""
        text = str(custom_text or "").strip()
        if not text:
            raise ValueError("A custom narration script is required.")
        if len(text) > 40000:
            raise ValueError("A custom narration script must be 40,000 characters or fewer.")

        pieces = []
        sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", text) if part.strip()]
        for sentence in sentences:
            if len(sentence.split()) <= 48:
                pieces.append(sentence)
                continue
            clauses = [part.strip() for part in re.split(r"(?<=[,;:])\s+", sentence) if part.strip()]
            for clause in clauses:
                words = clause.split()
                if len(words) <= 48:
                    pieces.append(clause)
                else:
                    pieces.extend(" ".join(words[i:i+42]) for i in range(0, len(words), 42))
        if not pieces:
            pieces = [text]

        word_count = sum(len(piece.split()) for piece in pieces)
        target_scene_count = max(1, min(24, (word_count + 41) // 42))
        scene_count = min(target_scene_count, len(pieces))
        groups = []
        piece_index = 0
        remaining_words = word_count
        for group_index in range(scene_count):
            groups_left = scene_count - group_index
            if groups_left == 1:
                groups.append(" ".join(pieces[piece_index:]))
                break
            target_words = max(1, (remaining_words + groups_left - 1) // groups_left)
            group = []
            group_words = 0
            while piece_index < len(pieces) - (groups_left - 1) and (not group or group_words < target_words):
                piece = pieces[piece_index]
                group.append(piece)
                piece_words = len(piece.split())
                group_words += piece_words
                remaining_words -= piece_words
                piece_index += 1
            groups.append(" ".join(group))

        scene_duration = max(5, int(duration) // max(1, len(groups)))
        scenes = []
        for index, narration in enumerate(groups, 1):
            keywords = self._script_keywords(title, narration)
            scenes.append({
                "scene_number": index,
                "duration": scene_duration,
                "narration": narration,
                "visual_description": f"Cinematic visual illustrating this narration: {narration[:240]}",
                "search_keywords": keywords,
                "text_overlay": " ".join(re.findall(r"[\w'-]+", narration)[:6])[:38] or str(title)[:38],
            })
        self._enrich_custom_scenes(title, scenes)
        return {
            "title": str(title),
            "description": f"Creator-provided narration for {title}: {text[:700]}",
            "scenes": scenes,
            "tags": re.findall(r"[\w]+", str(title).lower())[:8] or ["youtube"],
        }

    def _enrich_custom_scenes(self, title, scenes):
        """Ask Ollama for matching visuals only; never let it rewrite the voiceover."""
        if not self.enabled:
            return
        scene_briefs = [{"scene_number": scene["scene_number"], "narration": scene["narration"][:900]} for scene in scenes]
        prompt = (
            f"Plan concise video visuals for the title {title!r}. For each supplied scene, return only JSON "
            "with a scenes array containing visual_description, search_keywords, and text_overlay. "
            "Do not write, edit, shorten, or return narration. Keep the same scene count and order. "
            f"Input scene narration: {json.dumps(scene_briefs, ensure_ascii=False)}"
        )
        try:
            response = requests.post(
                self.url + "/api/generate",
                json={"model": self.model, "prompt": prompt, "stream": False,
                      "keep_alive": -1 if env_bool("OLLAMA_KEEP_ALIVE", True) else 0,
                      "options": {"temperature": .35}},
                timeout=120,
            )
            response.raise_for_status()
            raw = response.json().get("response", "")
            raw = re.sub(r"^\s*```(?:json)?|```\s*$", "", raw.strip(), flags=re.I)
            data = json.loads(raw[raw.find("{"):raw.rfind("}")+1])
            for scene, visual in zip(scenes, data.get("scenes", [])):
                for key in ("visual_description", "search_keywords", "text_overlay"):
                    value = visual.get(key)
                    if isinstance(value, str) and value.strip():
                        scene[key] = value.strip()
        except Exception as exc:
            print(f"[script] Could not enrich custom-script visuals; using narration-based visuals: {exc}")

    @staticmethod
    def _script_keywords(title, narration):
        stop = {"about", "after", "again", "against", "also", "because", "before", "being", "between", "could", "during", "from", "have", "into", "just", "more", "most", "over", "same", "some", "such", "than", "that", "their", "there", "these", "they", "this", "those", "through", "under", "very", "were", "what", "when", "where", "which", "while", "with", "would", "your", "you", "the", "and", "for", "are", "was", "but", "not", "our", "its", "then", "them", "will", "can", "how", "why"}
        words = [word for word in re.findall(r"[\w'-]+", narration.lower()) if len(word) > 3 and word not in stop]
        return ", ".join([str(title).strip()] + list(dict.fromkeys(words[:5]))).strip(", ")

    @staticmethod
    def _normalize(data, title, duration, count):
        scenes = data.get("scenes", [])[:count]
        while len(scenes) < count:
            scenes.append({"scene_number": len(scenes)+1, "duration": max(5, duration//count), "narration": f"Let's explore another fascinating part of {title}.", "visual_description": title, "search_keywords": title, "text_overlay": "Discover more"})
        each, rem = divmod(duration, len(scenes))
        for i, scene in enumerate(scenes):
            scene.update(scene_number=i+1, duration=max(5, each+(1 if i < rem else 0)))
            for key in ("narration", "visual_description", "search_keywords", "text_overlay"):
                scene[key] = str(scene.get(key) or title)
        data.update(title=str(data.get("title") or title), description=str(data.get("description") or title), scenes=scenes, tags=data.get("tags") or [title])
        return data

    @staticmethod
    def _fallback(title, duration, count):
        intro = [f"What if everything you thought you knew about {title} was only the beginning?", f"Today, we're taking a closer look at {title} and the ideas behind it."]
        close = [f"The story of {title} is still unfolding, and there is always more to discover.", f"If you enjoyed this exploration of {title}, subscribe for more fascinating stories."]
        scenes=[]
        each, rem = divmod(duration, count)
        for i in range(count):
            text = intro[i] if i == 0 else close[-1] if i == count-1 else f"One surprising detail about {title} is how much it connects to our everyday lives. Here's what makes it worth understanding."
            scenes.append({"scene_number":i+1,"duration":max(5,each+(i<rem)),"narration":text,"visual_description":f"Cinematic illustrative scene about {title}, scene {i+1}","search_keywords":title,"text_overlay":title[:38]})
        return {"title":title,"description":f"Discover the key ideas, surprising details, and bigger story behind {title}.","scenes":scenes,"tags":re.findall(r"[\w]+", title.lower())[:8] or ["youtube"]}
