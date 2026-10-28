"""Generate SEO and thumbnail text using the configured hosted text provider."""
import json
import os
import re

import requests


class YouTubeSEO:
    PROVIDER_NAMES = {"openrouter": "OpenRouter", "grok": "Grok (xAI)"}
    PROVIDER_URLS = {
        "openrouter": "https://openrouter.ai/api/v1/chat/completions",
        "grok": "https://api.x.ai/v1/chat/completions",
    }

    def __init__(self):
        self.configure(
            os.getenv("TEXT_PROVIDER", "openrouter"),
            os.getenv("TEXT_MODEL", "") or os.getenv("OPENROUTER_MODEL", ""),
            os.getenv("OPENROUTER_API_KEY", ""),
            os.getenv("GROK_API_KEY", ""),
        )
        self.last_source = ""

    def configure(self, provider, model, openrouter_api_key="", grok_api_key=""):
        self.provider = str(provider or "openrouter").strip().lower()
        if self.provider not in self.PROVIDER_URLS:
            self.provider = "openrouter"
        self.model = str(model or "").strip()
        self.provider_name = self.PROVIDER_NAMES[self.provider]
        self.api_key = str(grok_api_key or "").strip() if self.provider == "grok" else str(openrouter_api_key or "").strip()

    def generate_seo_package(self, title: str, script: dict, niche: str) -> dict:
        if not self.api_key or not self.model:
            raise RuntimeError(f"{self.provider_name} API key and text model are required to generate video metadata.")
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
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            if self.provider == "openrouter":
                headers["X-Title"] = "CreatorFlow"
            response = requests.post(
                self.PROVIDER_URLS[self.provider],
                headers=headers,
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": "You create accurate YouTube SEO metadata and thumbnail copy. Return valid JSON only."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.4,
                    "max_tokens": 3000,
                },
                timeout=120,
            )
            if not response.ok:
                try:
                    payload = response.json()
                    detail = (payload.get("error") or {}).get("message") if isinstance(payload, dict) else None
                except (ValueError, TypeError):
                    detail = None
                detail = str(detail or response.text or "").strip()[:500]
                raise RuntimeError(f"{self.provider_name} returned HTTP {response.status_code}: {detail or 'request failed'}")
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
            data = self._parse_json(content)
            self.last_source = f"{self.provider_name} · {self.model}"
            return self._normalize(data, title, niche)
        except (requests.RequestException, ValueError, TypeError, KeyError, IndexError) as exc:
            raise RuntimeError(f"Could not generate video metadata with {self.provider_name} ({type(exc).__name__}: {exc}). Check the selected model and account quota, then retry.") from exc

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
