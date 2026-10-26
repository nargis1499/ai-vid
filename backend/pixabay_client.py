"""Small, 24-hour-cached client for Pixabay's official image and video API."""
import hashlib
import json
import os
import threading
import time
from pathlib import Path

import requests

from settings_store import APP_DATA_DIR


CACHE_TTL_SECONDS = 24 * 60 * 60
CACHE_DIR = APP_DATA_DIR / "pixabay-api-cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
_CACHE_LOCK = threading.RLock()


class PixabayClient:
    BASE_URL = "https://pixabay.com"

    def __init__(self, api_key=""):
        self.api_key = str(api_key or "").strip()

    def set_api_key(self, api_key):
        self.api_key = str(api_key or "").strip()

    def search_images(self, query, per_page=12):
        payload = self._search(
            "/api/",
            {
                "q": str(query or "")[:100],
                "image_type": "photo",
                "orientation": "horizontal",
                "safesearch": "true",
                "order": "popular",
                "per_page": max(3, min(200, int(per_page))),
            },
        )
        return payload.get("hits", [])

    def search_videos(self, query, per_page=12):
        payload = self._search(
            "/api/videos/",
            {
                "q": str(query or "")[:100],
                "video_type": "all",
                "safesearch": "true",
                "order": "popular",
                "per_page": max(3, min(200, int(per_page))),
            },
        )
        return payload.get("hits", [])

    def _search(self, endpoint, params):
        if not self.api_key:
            return {"hits": []}

        cache_material = json.dumps(
            {"endpoint": endpoint, "params": params},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        cache_path = CACHE_DIR / (hashlib.sha256(cache_material).hexdigest() + ".json")
        now = time.time()
        with _CACHE_LOCK:
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if now - float(cached.get("cached_at", 0)) < CACHE_TTL_SECONDS:
                    payload = cached.get("payload")
                    if isinstance(payload, dict) and isinstance(payload.get("hits"), list):
                        return payload
            except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, ValueError):
                pass

            try:
                response = requests.get(
                    self.BASE_URL + endpoint,
                    params={**params, "key": self.api_key},
                    timeout=20,
                )
            except requests.RequestException as exc:
                raise RuntimeError(f"Could not reach Pixabay ({type(exc).__name__}).") from exc
            if not response.ok:
                raise RuntimeError(f"Pixabay API returned HTTP {response.status_code}.")
            try:
                payload = response.json()
            except ValueError as exc:
                raise RuntimeError("Pixabay returned an invalid response.") from exc
            if not isinstance(payload, dict) or not isinstance(payload.get("hits"), list):
                raise RuntimeError("Pixabay returned an invalid search result.")

            temporary = cache_path.with_suffix(".tmp")
            try:
                temporary.write_text(
                    json.dumps({"cached_at": now, "payload": payload}, ensure_ascii=False),
                    encoding="utf-8",
                )
                os.replace(temporary, cache_path)
            except OSError as exc:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
                # The API response is still usable if local caching fails.
                print(f"[pixabay] Could not cache search response: {type(exc).__name__}")
            return payload
