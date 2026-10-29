"""Generate SEO and thumbnail text using the configured hosted text provider."""
import json
import os
import re

from text_completion import complete_json


class YouTubeSEO:
    PROVIDER_NAMES = {"openrouter": "OpenRouter", "groq": "Groq (GroqCloud)", "local": "Local Qwen3 1.7B Q4_K_M"}
    PROVIDER_URLS = {
        "openrouter": "https://openrouter.ai/api/v1/chat/completions",
        "groq": "https://api.groq.com/openai/v1/chat/completions",
    }

    def __init__(self):
        self.configure(
            os.getenv("TEXT_PROVIDER", "openrouter"),
            os.getenv("TEXT_MODEL", "") or os.getenv("OPENROUTER_MODEL", ""),
            os.getenv("OPENROUTER_API_KEY", ""),
            os.getenv("GROQ_API_KEY", ""),
        )
        self.last_source = ""

    def configure(self, provider, model, openrouter_api_key="", groq_api_key=""):
        self.provider = str(provider or "openrouter").strip().lower()
        if self.provider not in {*self.PROVIDER_URLS, "local"}:
            self.provider = "openrouter"
        self.model = str(model or "").strip() or ("qwen3-1.7b-q4_k_m" if self.provider == "local" else "")
        self.provider_name = self.PROVIDER_NAMES[self.provider]
        self.api_key = str(groq_api_key or "").strip() if self.provider == "groq" else str(openrouter_api_key or "").strip()

    def generate_seo_package(self, title: str, script: dict, niche: str) -> dict:
        scene_brief = [
            {
                "visual": str(scene.get("visual_description", ""))[:180],
                "keywords": str(scene.get("search_keywords", ""))[:120],
                "narration": str(scene.get("narration", ""))[:180],
            }
            for scene in script.get("scenes", [])[:10]
            if isinstance(scene, dict)
        ]
        prompt = f'''Create YouTube SEO metadata for title {title!r}, niche {niche!r}, script summary {script.get('description','')!r}, and scene plan {scene_brief!r}. Return only JSON: {{"seo_title":"max 60 characters","seo_description":"500-1000 characters, keywords in first 150 chars, CTA, hashtags at end","tags":["15 relevant tags"],"category_id":"22","hashtag_line":"#tag1 #tag2 #tag3","thumbnail_text":"3-5 punchy words for a bold thumbnail headline, grounded in the script","thumbnail_subtitle":"short supporting phrase of 2-5 words, not a repetition of the headline","thumbnail_panels":["one distinct 1-3 word label per supplied scene, in scene-plan order, up to 10 labels"],"thumbnail_style":"visual description","pinned_comment":"engaging comment"}}. Make thumbnail wording specific to the video's actual subject and scene plan; preserve both named subjects in numbered lists/comparisons, separated with an ampersand; never invent places, people, or claims.'''
        try:
            content, source = complete_json(
                self.provider,
                self.model,
                self.api_key,
                "You create accurate YouTube SEO metadata and thumbnail copy. Return valid JSON only.",
                prompt,
                temperature=0.4,
                max_tokens=3000,
                task="generating video metadata and thumbnail copy",
                validator=lambda payload: self._normalize(payload, title, niche),
            )
            data = self._parse_json(content)
            normalized = self._normalize(data, title, niche)
            self.last_source = source
            return normalized
        except Exception as exc:
            raise RuntimeError(f"Could not generate video metadata ({type(exc).__name__}: {exc}).") from exc

    @staticmethod
    def _parse_json(raw):
        raw = re.sub(r"^\s*```(?:json)?|```\s*$", "", str(raw or "").strip(), flags=re.I)
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end < start:
            raise ValueError("The selected text model did not return JSON metadata.")
        result = json.loads(raw[start:end + 1])
        if not isinstance(result, dict):
            raise ValueError("The selected text model returned an invalid metadata object.")
        return result

    @staticmethod
    def _normalize(data, title, niche):
        required = ("seo_title", "seo_description", "tags", "hashtag_line", "thumbnail_text", "thumbnail_subtitle", "thumbnail_panels", "thumbnail_style", "pinned_comment")
        missing = [key for key in required if not data.get(key)]
        if missing:
            raise ValueError("Selected provider metadata is missing required fields: " + ", ".join(missing))
        if not isinstance(data["tags"], list) or not isinstance(data["thumbnail_panels"], list):
            raise ValueError("Selected provider metadata tags and thumbnail panels must be lists.")
        result = {
            "seo_title": str(data["seo_title"])[:100],
            "seo_description": str(data["seo_description"])[:5000],
            "tags": data["tags"],
            "category_id": str(os.getenv("YOUTUBE_CATEGORY_ID") or data.get("category_id") or "22"),
            "hashtag_line": str(data["hashtag_line"]),
            "thumbnail_text": str(data["thumbnail_text"])[:80],
            "thumbnail_subtitle": str(data["thumbnail_subtitle"])[:80],
            "thumbnail_panels": data["thumbnail_panels"],
            "thumbnail_style": str(data["thumbnail_style"]),
            "pinned_comment": str(data["pinned_comment"]),
        }
        result["tags"] = [str(tag)[:100] for tag in result["tags"][:30]]
        result["thumbnail_panels"] = [str(label)[:80] for label in result["thumbnail_panels"][:10]]
        return result
