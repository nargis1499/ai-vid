"""Query preparation and provider-neutral stock candidate scoring."""
import re
from urllib.parse import urlsplit, urlunsplit


# This is a semantic evidence gate, not a blended quality score. A candidate must
# pass it before duration, resolution, or orientation can affect its rank.
MIN_STOCK_RELEVANCE = 0.40
EARLY_STOP_SEMANTIC_RELEVANCE = 0.72
ASSET_REUSE_RELEVANCE_MARGIN = 0.12

_STOP_WORDS = {
    "a", "about", "after", "all", "an", "and", "are", "around", "at", "be", "beautiful",
    "by", "can", "cinematic", "clip", "close", "day", "each", "for", "from", "high",
    "in", "into", "is", "it", "landscape", "look", "of", "on", "or", "people", "person",
    "photo", "scene", "shot", "the", "their", "this", "to", "up", "video", "view", "with",
    "quality", "footage", "wide", "camera", "background", "image", "stock", "visual", "showing",
    "com", "http", "https", "pexels", "pixabay", "www", "two", "one", "another", "friendly",
    "nice", "each", "other", "while", "who", "what", "that", "they", "you", "your", "someone",
    "something", "around", "make", "makes", "made",
    "relationship", "relationships", "community", "communication", "communicate", "conversation",
    "talk", "talking", "speak", "speaking", "proven", "ways", "way",
}

_SYNONYMS = {
    "greeted": "greet", "greeting": "greet", "greetings": "greet", "greets": "greet", "hello": "greet",
    "helping": "help", "helps": "help", "helped": "help",
    "waved": "wave", "waving": "wave", "waves": "wave",
    "homes": "home", "house": "home", "houses": "home", "residential": "home", "suburban": "home",
    "neighbors": "neighbor", "homeowners": "homeowner",
    "smartphone": "phone", "mobile": "phone", "cellphone": "phone", "texting": "text",
    "messaging": "text", "message": "text", "messages": "text",
    "fighting": "fight", "fights": "fight", "punching": "fight", "violent": "violence",
    "surveillance": "surveil", "surveilling": "surveil", "spying": "surveil",
    "gardens": "garden", "workers": "worker", "cleaning": "clean", "cleaned": "clean",
    "typing": "type", "typed": "type", "computers": "computer", "laptops": "laptop",
    "calmly": "calm", "politely": "polite", "boundaries": "boundary", "listening": "listen",
    "carrying": "carry", "carried": "carry", "introducing": "introduce", "introduced": "introduce",
    "shaking": "shake", "discussing": "discuss", "discussed": "discuss", "explains": "explain",
    "explaining": "explain", "giving": "give", "gives": "give", "waiting": "wait", "waits": "wait",
    "turning": "turn", "turns": "turn", "lowering": "lower", "sending": "send", "sends": "send",
    "sweeping": "sweep", "swept": "sweep", "removing": "remove", "removes": "remove",
    "keeping": "keep", "keeps": "keep", "passing": "pass", "passes": "pass", "offering": "offer",
    "tidying": "clean", "tidied": "clean", "cleaning": "clean", "cleaned": "clean",
}

_RESIDENTIAL_TERMS = {"neighbor", "home", "house", "yard", "garden", "porch", "residential", "sidewalk", "hallway", "frontdoor"}
_BUSINESS_TERMS = {"office", "conference", "boardroom", "corporate", "business", "warehouse", "factory", "industrial", "meeting"}
_CALM_TERMS = {"calm", "peaceful", "quiet", "polite", "respect", "listen", "patient", "gentle"}
_VIOLENCE_TERMS = {"fight", "violence", "attack", "combat", "wrestle", "punch", "weapon"}
_PRIVACY_TERMS = {"privacy", "private", "space", "boundary", "personal", "doorway"}
_SURVEILLANCE_TERMS = {"surveil", "cctv", "security", "camera", "monitor", "spying"}
_PHONE_TERMS = {"phone", "text", "smartphone", "mobile"}
_COMPUTER_TERMS = {"computer", "laptop", "desktop", "keyboard", "monitor"}
_INDUSTRIAL_TERMS = {"industrial", "warehouse", "factory", "machinery", "production"}
_GARDEN_TERMS = {"garden", "yard", "flower", "plant", "community"}
_ACTION_TERMS = {
    "greet", "wave", "carry", "help", "listen", "explain", "give", "wait", "turn", "lower",
    "discuss", "raise", "text", "type", "check", "clean", "sweep", "leave", "keep", "share",
    "pass", "introduce", "shake", "send", "offer", "remove", "walk", "open", "close", "coordinate",
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


def _unique_queries(candidates, max_queries):
    queries = []
    seen = set()
    for phrase in candidates:
        clean = re.sub(r"[^\w\s'-]+", " ", str(phrase or ""))
        clean = re.sub(r"\s+", " ", clean).strip(" ,.-")
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


def search_queries(scene_or_queries, max_queries=2):
    """Return planned visual queries first; never replace a supplied plan with narration."""
    if isinstance(scene_or_queries, dict):
        visual_plan = scene_or_queries.get("visual_plan")
        if isinstance(visual_plan, dict):
            candidates = _as_phrases(visual_plan.get("primary_query")) + _as_phrases(visual_plan.get("alternative_queries"))
        else:
            candidates = (
                _as_phrases(scene_or_queries.get("search_queries"))
                + _as_phrases(scene_or_queries.get("search_keywords"))
                + _as_phrases(scene_or_queries.get("visual_description"))
            )
            if not candidates:
                narration_terms = [
                    token for token in re.findall(r"[\w']+", str(scene_or_queries.get("narration") or ""))
                    if len(token) > 2 and token.casefold() not in _STOP_WORDS
                ]
                if narration_terms:
                    candidates = [" ".join(narration_terms[:8])]
    else:
        candidates = _as_phrases(scene_or_queries)
    return _unique_queries(candidates, max_queries)


def _tokens(value):
    result = set()
    for raw in re.findall(r"[\w']+", str(value or "").casefold()):
        if len(raw) <= 2 or raw.isdigit() or raw in _STOP_WORDS:
            continue
        token = _SYNONYMS.get(raw, raw)
        if token.endswith("ies") and len(token) > 4:
            token = token[:-3] + "y"
        elif token.endswith("s") and not token.endswith(("ss", "us")) and len(token) > 4:
            token = token[:-1]
        if token in _STOP_WORDS:
            continue
        result.add(token)
    return result


def _flatten_metadata(metadata):
    if isinstance(metadata, dict):
        return " ".join(str(value or "") for value in metadata.values())
    if isinstance(metadata, (list, tuple, set)):
        return " ".join(str(value or "") for value in metadata)
    return str(metadata or "")


def _contradiction_penalty(scene_terms, metadata_terms):
    penalty = 0.0
    reasons = []
    if (scene_terms & _RESIDENTIAL_TERMS and metadata_terms & _BUSINESS_TERMS
            and not metadata_terms & _RESIDENTIAL_TERMS):
        penalty += 0.25
        reasons.append("business/industrial setting conflicts with residential scene")
    if scene_terms & _CALM_TERMS and metadata_terms & _VIOLENCE_TERMS:
        penalty += 0.40
        reasons.append("violent action conflicts with a calm scene")
    if scene_terms & _PRIVACY_TERMS and metadata_terms & _SURVEILLANCE_TERMS:
        penalty += 0.32
        reasons.append("surveillance conflicts with a privacy-focused scene")
    if scene_terms & _PHONE_TERMS and metadata_terms & _COMPUTER_TERMS and not metadata_terms & _PHONE_TERMS:
        penalty += 0.24
        reasons.append("computer-only activity conflicts with phone messaging")
    if (scene_terms & _GARDEN_TERMS and metadata_terms & _INDUSTRIAL_TERMS
            and not metadata_terms & _GARDEN_TERMS):
        penalty += 0.30
        reasons.append("industrial setting conflicts with garden scene")
    return min(0.60, penalty), reasons


def semantic_score_breakdown(scene, metadata):
    """Score visible scene evidence independently of technical quality/provider."""
    text = _flatten_metadata(metadata)
    metadata_terms = _tokens(text)
    if not metadata_terms:
        return {"score": 0.0, "components": {}, "contradiction_penalty": 0.0, "reasons": ["no usable metadata"]}

    plan = scene.get("visual_plan") if isinstance(scene, dict) else None
    plan = plan if isinstance(plan, dict) else {}
    if plan:
        groups = [
            ("visible_action", plan.get("visible_action") or plan.get("primary_query"), 0.42),
            ("visual_objective", plan.get("visual_objective"), 0.26),
            ("subjects", plan.get("subjects"), 0.18),
            ("setting", plan.get("setting"), 0.14),
        ]
        if not any(_tokens(value) for _, value, _ in groups):
            groups = [("planned_queries", search_queries(scene, max_queries=5), 1.0)]
    else:
        query_cues = search_queries(scene, max_queries=5) if isinstance(scene, dict) else _as_phrases(scene)
        groups = [("scene_queries", query_cues, 1.0)]

    available = [(name, value, weight) for name, value, weight in groups if _tokens(value)]
    if not available:
        return {"score": 0.0, "components": {}, "contradiction_penalty": 0.0, "reasons": ["no concrete scene cues"]}
    total_weight = sum(weight for _, _, weight in available)
    components = {}
    weighted_score = 0.0
    for name, cue, weight in available:
        cue_text = " ".join(str(item) for item in cue) if isinstance(cue, (list, tuple, set)) else str(cue)
        cue_terms = _tokens(cue_text)
        if name == "visible_action":
            action_terms = cue_terms & _ACTION_TERMS
            if action_terms:
                cue_terms = action_terms
        coverage = len(cue_terms & metadata_terms) / len(cue_terms) if cue_terms else 0.0
        components[name] = round(coverage, 4)
        weighted_score += coverage * weight / total_weight

    scene_terms = set()
    for _, cue, _ in groups:
        scene_terms.update(_tokens(cue))
    penalty, penalty_reasons = _contradiction_penalty(scene_terms, metadata_terms)
    score = max(0.0, min(1.0, weighted_score - penalty))
    reasons = [f"{name} overlap {value:.2f}" for name, value in components.items() if value > 0]
    reasons.extend(penalty_reasons)
    return {
        "score": round(score, 4),
        "components": components,
        "contradiction_penalty": round(penalty, 4),
        "reasons": reasons or ["no scene-specific terms matched"],
    }


def semantic_relevance_score(scene, metadata):
    return semantic_score_breakdown(scene, metadata)["score"]


def relevance_score(queries, metadata):
    """Backward-compatible query-only semantic scoring; technical fields are ignored."""
    cues = _as_phrases(queries)
    return semantic_relevance_score({"search_queries": cues}, metadata)


def technical_suitability_score(width=None, height=None, duration=None, target_duration=None, orientation="landscape", accessible=None, media_type="video"):
    """Return technical fit separately; this score can only break semantic ties."""
    try:
        width, height = int(width or 0), int(height or 0)
    except (TypeError, ValueError):
        width, height = 0, 0
    if width > 0 and height > 0:
        ratio = width / height
        if orientation == "landscape":
            orientation_score = 1.0 if ratio >= 1.15 else 0.0
        else:
            orientation_score = 1.0 if ratio <= 0.87 else 0.0
        if width >= 1280 and height >= 720:
            resolution_score = 1.0
        elif width >= 640 and height >= 360:
            resolution_score = 0.70
        else:
            resolution_score = 0.25
    else:
        orientation_score = 0.5
        resolution_score = 0.5

    if media_type == "image":
        duration_score = 1.0
        weights = {"orientation": 0.40, "resolution": 0.60}
    else:
        try:
            duration, target_duration = float(duration or 0), float(target_duration or 0)
        except (TypeError, ValueError):
            duration, target_duration = 0.0, 0.0
        duration_score = min(1.0, duration / target_duration) if duration > 0 and target_duration > 0 else 0.5
        weights = {"duration": 0.40, "orientation": 0.35, "resolution": 0.25}
    access_score = 0.5 if accessible is None else (1.0 if accessible else 0.0)
    weights["accessibility"] = 0.0 if accessible is None else 0.15
    if accessible is not None:
        weights = {key: value * (0.85 if key != "accessibility" else 1.0) for key, value in weights.items()}
    components = {
        "duration": round(duration_score, 4),
        "orientation": round(orientation_score, 4),
        "resolution": round(resolution_score, 4),
        "accessibility": round(access_score, 4),
    }
    score = sum(components[key] * weight for key, weight in weights.items())
    return {"score": round(score, 4), "components": components}


def candidate_identity_keys(candidate):
    """Return URL and provider-ID aliases for within/across-provider deduplication."""
    provider = str(candidate.get("provider") or "unknown").casefold()
    file_info = candidate.get("file") if isinstance(candidate.get("file"), dict) else {}
    raw_url = file_info.get("url") or candidate.get("url") or ""
    keys = []
    if raw_url:
        parts = urlsplit(str(raw_url).strip())
        if parts.scheme and parts.netloc:
            stable_url = urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), parts.path.rstrip("/"), "", ""))
        else:
            stable_url = str(raw_url).split("?", 1)[0].split("#", 1)[0].rstrip("/")
        if stable_url:
            keys.append(f"url:{stable_url}")
    identity = candidate.get("identity") or candidate.get("id")
    if identity is None:
        video = candidate.get("video")
        identity = video.get("id") if isinstance(video, dict) else None
    if identity is not None:
        keys.append(f"{provider}:id:{identity}")
    return list(dict.fromkeys(keys))


def stable_candidate_key(candidate):
    """Return the canonical URL key, or a provider-ID fallback, for asset reuse tracking."""
    keys = candidate_identity_keys(candidate)
    return keys[0] if keys else None


def _candidate_metadata(candidate):
    metadata = candidate.get("metadata")
    if metadata is not None:
        return metadata
    video = candidate.get("video")
    source = video if isinstance(video, dict) else candidate
    # Never score the search query, download URL, uploader, or IDs as evidence.
    fields = ("title", "description", "tags", "type", "alt")
    return {key: source.get(key) for key in fields if source.get(key)}


def _candidate_technical(candidate, scene, media_type):
    file_info = candidate.get("file") if isinstance(candidate.get("file"), dict) else candidate
    video = candidate.get("video") if isinstance(candidate.get("video"), dict) else {}
    plan = scene.get("visual_plan") if isinstance(scene, dict) else {}
    target_duration = None
    if isinstance(scene, dict):
        target_duration = (plan or {}).get("target_duration") or scene.get("duration")
    return technical_suitability_score(
        width=file_info.get("width"), height=file_info.get("height"),
        duration=video.get("duration", candidate.get("duration")), target_duration=target_duration,
        orientation=(plan or {}).get("required_orientation", "landscape"),
        accessible=candidate.get("accessible"), media_type=media_type,
    )


def rank_stock_candidates(scene, candidates, media_type="video", min_semantic=MIN_STOCK_RELEVANCE, used_assets=None):
    """Score/deduplicate candidate pool; semantic relevance always sorts before technical fit."""
    used_assets = used_assets or set()
    scored_by_key = {}
    alias_to_key = {}
    for original in candidates:
        if not isinstance(original, dict):
            continue
        candidate = dict(original)
        semantic = semantic_score_breakdown(scene, _candidate_metadata(candidate))
        technical = _candidate_technical(candidate, scene, media_type)
        candidate["semantic_relevance"] = semantic["score"]
        candidate["relevance"] = semantic["score"]  # compatibility for existing status/debug consumers
        candidate["semantic_breakdown"] = semantic
        candidate["technical_suitability"] = technical["score"]
        candidate["technical_breakdown"] = technical["components"]
        aliases = candidate_identity_keys(candidate)
        candidate["stable_keys"] = aliases
        candidate["stable_key"] = aliases[0] if aliases else None
        if candidate.get("accessible") is False or semantic["score"] < float(min_semantic):
            continue
        known_key = next((alias_to_key[alias] for alias in aliases if alias in alias_to_key), None)
        key = known_key or candidate["stable_key"] or f"anonymous:{candidate.get('provider')}:{candidate.get('rank')}"
        prior = scored_by_key.get(key)
        sort_key = (candidate["semantic_relevance"], candidate["technical_suitability"], -int(candidate.get("query_rank", 0)), -int(candidate.get("rank", 0)))
        if prior is None or sort_key > prior[0]:
            if prior is not None:
                aliases = list(dict.fromkeys((prior[1].get("stable_keys") or []) + aliases))
                candidate["stable_keys"] = aliases
                candidate["stable_key"] = aliases[0] if aliases else None
            scored_by_key[key] = (sort_key, candidate)
        elif prior is not None:
            prior[1]["stable_keys"] = list(dict.fromkeys((prior[1].get("stable_keys") or []) + aliases))
        for alias in aliases:
            alias_to_key[alias] = key

    ranked = [item[1] for item in scored_by_key.values()]
    # Avoid repeats when there is a similarly relevant unused alternative, but do
    # not sacrifice a large semantic gap solely for variety.
    reused = [item for item in ranked if set(item.get("stable_keys") or []) & used_assets]
    unused = [item for item in ranked if not (set(item.get("stable_keys") or []) & used_assets)]
    if reused and unused:
        best_reused = max(item["semantic_relevance"] for item in reused)
        diverse = [
            item for item in unused
            if item["semantic_relevance"] >= best_reused - ASSET_REUSE_RELEVANCE_MARGIN
        ]
        if diverse:
            ranked = diverse
    ranked.sort(key=lambda item: (
        -item["semantic_relevance"],
        -item["technical_suitability"],
        int(item.get("query_rank", 0)),
        int(item.get("rank", 0)),
        str(item.get("stable_key") or ""),
    ))
    for rank, item in enumerate(ranked, 1):
        item["final_rank"] = rank
    return ranked


def image_quality_score(width, height):
    """Legacy quality metric retained for existing callers and thumbnails."""
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
