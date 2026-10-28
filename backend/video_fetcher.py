"""Pexels stock-video search that prefers matches but accepts weaker results."""
import os
import re
from urllib.parse import unquote, urlparse

import requests

from common import DIRS
from pixabay_client import PixabayClient


_STOP_WORDS = {
    "a", "about", "after", "all", "an", "and", "are", "at", "be", "beautiful",
    "by", "cinematic", "clip", "close", "day", "for", "from", "high", "in", "into",
    "is", "it", "landscape", "look", "of", "on", "or", "people", "photo", "scene",
    "shot", "the", "their", "this", "to", "up", "video", "view", "with", "quality",
    "footage", "wide", "camera", "background", "image",
}


class VideoFetcher:
    def __init__(self):
        self.api_key = os.getenv("PEXELS_API_KEY", "").strip()
        self.pixabay = PixabayClient(os.getenv("PIXABAY_API_KEY", ""))
        self.last_source = None

    @staticmethod
    def _tokens(text):
        return {
            token for token in re.findall(r"[a-z0-9]+", str(text or "").lower())
            if len(token) > 2 and token not in _STOP_WORDS
        }

    @classmethod
    def _relevance(cls, query, video):
        query_terms = cls._tokens(query)
        if not query_terms:
            return 0.0
        url = unquote(str(video.get("url") or ""))
        slug = urlparse(url).path.rsplit("/", 1)[-1]
        video_terms = cls._tokens(slug)
        matched = query_terms & video_terms
        return len(matched) / len(query_terms)

    def fetch_clip(self, query: str, index: int, min_dur: float = 5) -> str | None:
        """Try Pexels first, then Pixabay; download selected stock footage locally."""
        self.last_source = None
        if self.api_key:
            clip = self._fetch_pexels_clip(query, index, min_dur)
            if clip:
                self.last_source = "Pexels"
                return clip
        if self.pixabay.api_key:
            clip = self._fetch_pixabay_clip(query, index, min_dur)
            if clip:
                self.last_source = "Pixabay"
                return clip
        return None

    def _fetch_pixabay_clip(self, query: str, index: int, min_dur: float) -> str | None:
        try:
            videos = self.pixabay.search_videos(query, per_page=20)
            query_terms = self._tokens(query)
            candidates = []
            for rank, video in enumerate(videos):
                if not isinstance(video, dict) or video.get("duration", 0) < min_dur:
                    continue
                tags = self._tokens(video.get("tags", ""))
                relevance = len(query_terms & tags) / len(query_terms) if query_terms else 0.0
                variants = video.get("videos", {})
                files = [
                    (name, item) for name, item in variants.items()
                    if isinstance(item, dict) and item.get("url")
                ]
                if files:
                    candidates.append((relevance, rank, video, files))
            if not candidates:
                return None
            _, _, video, files = sorted(candidates, key=lambda item: (-item[0], item[1]))[0]
            _, selected = sorted(
                files,
                key=lambda item: (
                    0 if 1280 <= item[1].get("width", 0) <= 1920 else 1,
                    -item[1].get("width", 0),
                    item[0],
                ),
            )[0]
            return self._download_clip(selected["url"], index)
        except Exception as exc:
            print(f"[video] Pixabay fetch failed: {exc}")
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

    def _fetch_pexels_clip(self, query: str, index: int, min_dur: float = 5) -> str | None:
        """Download Pexels' best available search result, or return None when none exists."""
        if not self.api_key:
            return None
        try:
            response = requests.get(
                "https://api.pexels.com/videos/search",
                headers={"Authorization": self.api_key},
                params={"query": query, "orientation": "landscape", "per_page": 10},
                timeout=20,
            )
            response.raise_for_status()
            videos = response.json().get("videos", [])
            candidates = []
            for rank, video in enumerate(videos):
                if not isinstance(video, dict) or video.get("duration", 0) < min_dur:
                    continue
                files = [
                    file for file in video.get("video_files", [])
                    if file.get("file_type") == "video/mp4" and file.get("link")
                ]
                if not files:
                    continue
                relevance = self._relevance(query, video)
                # Prefer stronger slug/keyword overlap, while keeping every usable
                # Pexels-ranked result eligible so a relevant stock match wins over a designed fallback.
                candidates.append((relevance, rank, video, files))

            if not candidates:
                print(f"[video] No usable Pexels clip available for: {query}")
                return None

            # Prefer the strongest keyword match; retain Pexels relevance order for ties.
            _, _, video, files = sorted(candidates, key=lambda item: (-item[0], item[1]))[0]
            file = sorted(
                files,
                key=lambda item: (
                    0 if 1280 <= item.get("width", 0) <= 1920 else 1,
                    -item.get("width", 0),
                ),
            )[0]
            path = DIRS["clips"] / f"clip_{index}.mp4"
            partial_path = path.with_suffix(".mp4.part")
            try:
                with requests.get(file["link"], stream=True, timeout=60) as download:
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
        except Exception as exc:
            print(f"[video] Pexels fetch failed: {exc}")
            return None
