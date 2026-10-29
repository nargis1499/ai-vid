"""Search and rank Pexels/Pixabay video candidates using scene text metadata."""
import math
import os
from urllib.parse import unquote, urlparse

import requests

from common import DIRS
from pixabay_client import PixabayClient
from stock_search import relevance_score, search_queries


class VideoFetcher:
    def __init__(self):
        self.api_key = os.getenv("PEXELS_API_KEY", "").strip()
        self.pixabay = PixabayClient(os.getenv("PIXABAY_API_KEY", ""))
        self.last_source = None

    @staticmethod
    def _duration(video):
        try:
            return float(video.get("duration") or 0)
        except (AttributeError, TypeError, ValueError):
            return 0.0

    @staticmethod
    def _duration_score(duration, target_duration):
        if not target_duration or target_duration <= 0 or duration <= 0:
            return 0.0
        distance = min(abs(math.log(max(duration / target_duration, 0.01))), 2.0)
        return 0.08 * (1.0 - distance / 2.0)

    @staticmethod
    def _best_video_file(files):
        usable = [
            item for item in files
            if isinstance(item, dict) and item.get("url")
        ]
        landscape = []
        for item in usable:
            try:
                width, height = int(item.get("width") or 0), int(item.get("height") or 0)
            except (TypeError, ValueError):
                width, height = 0, 0
            if width and height and width / height >= 1.15:
                landscape.append(item)
        if landscape:
            usable = landscape
        elif any(item.get("width") or item.get("height") for item in usable):
            return None
        minimum_resolution = []
        for item in usable:
            try:
                if int(item.get("width") or 0) >= 640 and int(item.get("height") or 0) >= 360:
                    minimum_resolution.append(item)
            except (TypeError, ValueError):
                continue
        if minimum_resolution:
            usable = minimum_resolution
        if not usable:
            return None

        def quality(item):
            try:
                width, height = int(item.get("width") or 0), int(item.get("height") or 0)
            except (TypeError, ValueError):
                width, height = 0, 0
            if not width or not height:
                return (0, 0, 0, 0)
            ratio_fit = -abs(width / height - 16 / 9)
            preferred_resolution = 1 if 1280 <= width <= 1920 and height >= 720 else 0
            minimum_resolution = 1 if width >= 640 and height >= 360 else 0
            return (minimum_resolution, preferred_resolution, ratio_fit, min(width, 1920))

        return max(usable, key=quality)

    @staticmethod
    def _video_metadata(video, provider):
        if provider == "Pexels":
            url = unquote(str(video.get("url") or ""))
            slug = urlparse(url).path.rsplit("/", 1)[-1]
            user = video.get("user") if isinstance(video.get("user"), dict) else {}
            return " ".join((slug, str(video.get("alt") or ""), str(user.get("name") or "")))
        return " ".join((str(video.get("tags") or ""), str(video.get("type") or ""), str(video.get("user") or "")))

    def _search_pexels(self, query, query_rank, min_duration):
        if not self.api_key:
            return []
        try:
            response = requests.get(
                "https://api.pexels.com/videos/search",
                headers={"Authorization": self.api_key},
                params={"query": query, "orientation": "landscape", "per_page": 20},
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
            videos = payload.get("videos", []) if isinstance(payload, dict) else []
            candidates = []
            for rank, video in enumerate(videos):
                if not isinstance(video, dict) or self._duration(video) < min_duration:
                    continue
                files = [
                    {"url": item.get("link"), "width": item.get("width"), "height": item.get("height")}
                    for item in video.get("video_files", [])
                    if isinstance(item, dict) and item.get("file_type") == "video/mp4" and item.get("link")
                ]
                selected_file = self._best_video_file(files)
                if not selected_file:
                    continue
                identity = str(video.get("id") or video.get("url") or selected_file["url"])
                candidates.append({
                    "provider": "Pexels", "identity": identity, "video": video,
                    "file": selected_file, "query_rank": query_rank, "rank": rank,
                })
            return candidates
        except Exception as exc:
            print(f"[video] Pexels search failed for a scene query: {exc}")
            return []

    def _search_pixabay(self, query, query_rank, min_duration):
        if not self.pixabay.api_key:
            return []
        try:
            videos = self.pixabay.search_videos(query, per_page=20)
            candidates = []
            for rank, video in enumerate(videos):
                if not isinstance(video, dict) or self._duration(video) < min_duration:
                    continue
                variants = video.get("videos", {})
                files = [
                    {"url": item.get("url"), "width": item.get("width"), "height": item.get("height")}
                    for item in variants.values()
                    if isinstance(item, dict) and item.get("url")
                ] if isinstance(variants, dict) else []
                selected_file = self._best_video_file(files)
                if not selected_file:
                    continue
                identity = str(video.get("id") or selected_file["url"])
                candidates.append({
                    "provider": "Pixabay", "identity": identity, "video": video,
                    "file": selected_file, "query_rank": query_rank, "rank": rank,
                })
            return candidates
        except Exception as exc:
            print(f"[video] Pixabay search failed for a scene query: {exc}")
            return []

    def fetch_clip(self, query, index: int, min_dur: float = 5) -> str | None:
        """Search a couple of focused variants, then rank usable candidates by metadata match."""
        self.last_source = None
        queries = search_queries(query, max_queries=2)
        target_duration = self._duration(query)
        if not queries:
            return None
        candidates = {}
        for query_rank, phrase in enumerate(queries):
            found = self._search_pexels(phrase, query_rank, min_dur)
            found.extend(self._search_pixabay(phrase, query_rank, min_dur))
            for candidate in found:
                key = (candidate["provider"], candidate["identity"])
                previous = candidates.get(key)
                if previous is None or (candidate["query_rank"], candidate["rank"]) < (previous["query_rank"], previous["rank"]):
                    candidates[key] = candidate
        if not candidates:
            return None
        adequate_resolution=[]
        for candidate in candidates.values():
            try:
                width=int(candidate["file"].get("width") or 0)
                height=int(candidate["file"].get("height") or 0)
            except (TypeError,ValueError):
                width,height=0,0
            if width>=640 and height>=360:
                adequate_resolution.append(candidate)
        if adequate_resolution:
            candidates={(item["provider"],item["identity"]):item for item in adequate_resolution}

        ranked = []
        for candidate in candidates.values():
            candidate["relevance"] = relevance_score(
                queries, self._video_metadata(candidate["video"], candidate["provider"])
            )
            duration_score = self._duration_score(self._duration(candidate["video"]), target_duration)
            candidate["rank_score"] = candidate["relevance"] + duration_score + (0.04 if candidate["provider"] == "Pexels" else 0.0)
            ranked.append(candidate)
        ranked.sort(key=lambda item: (
            -item["rank_score"],
            0 if item["provider"] == "Pexels" else 1,
            item["query_rank"],
            item["rank"],
        ))
        for selected in ranked:
            try:
                path = self._download_clip(selected["file"]["url"], index)
                self.last_source = selected["provider"]
                return path
            except Exception as exc:
                print(f"[video] Could not download a ranked {selected['provider']} clip: {exc}")
        return None

    @staticmethod
    def _download_clip(url: str, index: int) -> str:
        path = DIRS["clips"] / f"clip_{index}.mp4"
        partial_path = path.with_suffix(".mp4.part")
        try:
            with requests.get(url, stream=True, timeout=60) as download:
                download.raise_for_status()
                with open(partial_path, "wb") as output:
                    for chunk in download.iter_content(8192):
                        if chunk:
                            output.write(chunk)
            os.replace(partial_path, path)
        finally:
            try:
                partial_path.unlink(missing_ok=True)
            except OSError:
                pass
        return str(path)
