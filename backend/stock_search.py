"""Search-query preparation and lightweight stock metadata relevance ranking."""
import re


MIN_STOCK_RELEVANCE = 0.40


_STOP_WORDS = {
    "a", "about", "after", "all", "an", "and", "are", "at", "be", "beautiful",
    "by", "cinematic", "clip", "close", "day", "for", "from", "high", "in", "into",
    "is", "it", "landscape", "look", "of", "on", "or", "people", "photo", "scene",
    "shot", "the", "their", "this", "to", "up", "video", "view", "with", "quality",
    "footage", "wide", "camera", "background", "image", "stock", "visual", "showing",
    "com", "http", "https", "pexels", "pixabay", "www",
}


def _as_phrases(value):
    if isinstance(value, (list, tuple, set)):
        values = value
    elif value is None:
        values = []
    else:
        values = [value]
    phrases = []
    for item in values:
        if isinstance(item, str):
            phrases.extend(re.split(r"[\r\n;|]+", item))
    return phrases


def search_queries(scene_or_queries, max_queries=2):
    """Return a few focused, unique search phrases from model-authored scene fields."""
    if isinstance(scene_or_queries, dict):
        candidates = (
            _as_phrases(scene_or_queries.get("search_queries"))
            + _as_phrases(scene_or_queries.get("search_keywords"))
            + _as_phrases(scene_or_queries.get("visual_description"))
            + _as_phrases(scene_or_queries.get("narration"))
        )
    else:
        candidates = _as_phrases(scene_or_queries)

    queries = []
    seen = set()
    for phrase in candidates:
        clean = re.sub(r"\s+", " ", str(phrase or "")).strip(" ,.-")
        if not clean:
            continue
        clean = clean[:100].rsplit(" ", 1)[0] if len(clean) > 100 else clean
        key = " ".join(re.findall(r"[\w'-]+", clean.casefold()))
        if not key or key in seen:
            continue
        seen.add(key)
        queries.append(clean)
        if len(queries) >= max(1, int(max_queries)):
            break
    return queries


def relevance_score(queries, metadata):
    """Score a result's textual metadata against the best of a scene's search phrases."""
    if isinstance(metadata, (list, tuple, set)):
        metadata_text = " ".join(str(value or "") for value in metadata)
    else:
        metadata_text = str(metadata or "")
    metadata_text = re.sub(r"[^\w']+", " ", metadata_text.casefold())
    metadata_terms = {
        token for token in re.findall(r"[\w']+", metadata_text)
        if len(token) > 2 and not token.isdigit() and token not in _STOP_WORDS
    }
    if not metadata_terms:
        return 0.0

    normalized_metadata = f" {metadata_text} "
    best = 0.0
    for query in queries:
        query_text = re.sub(r"[^\w']+", " ", str(query or "").casefold()).strip()
        query_terms = {
            token for token in re.findall(r"[\w']+", query_text)
            if len(token) > 2 and not token.isdigit() and token not in _STOP_WORDS
        }
        if not query_terms:
            continue
        matched = query_terms & metadata_terms
        coverage = len(matched) / len(query_terms)
        precision = len(matched) / len(metadata_terms)
        phrase_match = bool(query_text and f" {query_text} " in normalized_metadata)
        score = 0.78 * coverage + 0.14 * precision + 0.08 * phrase_match
        best = max(best, score)
    return min(1.0, best)


def image_quality_score(width, height):
    """Prefer landscape images large enough for a 720p video and close to 16:9."""
    try:
        width, height = int(width or 0), int(height or 0)
    except (TypeError, ValueError):
        return 0.0
    if width <= 0 or height <= 0:
        return 0.0
    ratio = width / height
    landscape = 1.0 if ratio >= 1.15 else 0.0
    enough_pixels = 1.0 if width >= 1280 and height >= 720 else 0.55 if width >= 640 and height >= 360 else 0.0
    ratio_fit = max(0.0, 1.0 - abs(ratio - 16 / 9) / (16 / 9))
    return 0.55 * landscape + 0.25 * enough_pixels + 0.20 * ratio_fit
