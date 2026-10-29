"""Generate a reproducible metadata-only stock-ranking report.

Default mode evaluates deterministic candidate fixtures offline. ``--live`` uses
configured Pexels/Pixabay keys to collect a small, bounded candidate sample; it
never prints credentials and still does not claim visual verification.
"""
import argparse
import json
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from stock_search import (  # noqa: E402
    EARLY_STOP_SEMANTIC_RELEVANCE,
    MIN_STOCK_RELEVANCE,
    rank_stock_candidates,
    search_queries,
)

DEFAULT_FIXTURE = BACKEND_DIR / "tests" / "fixtures" / "stock_ranking_examples.json"


def _candidate_title(candidate):
    metadata = candidate.get("metadata")
    if isinstance(metadata, dict):
        return str(metadata.get("title") or candidate.get("title") or "").strip()
    return str(candidate.get("title") or "").strip()


def _candidate_report(candidate, selected_key=None):
    return {
        "provider": candidate.get("provider", "unknown"),
        "identity": candidate.get("identity") or candidate.get("id"),
        "title": _candidate_title(candidate) or "(untitled)",
        "query": candidate.get("query") or "",
        "query_rank": candidate.get("query_rank"),
        "semantic_relevance": candidate.get("semantic_relevance", 0.0),
        "technical_suitability": candidate.get("technical_suitability", 0.0),
        "technical_components": candidate.get("technical_breakdown", {}),
        "accessibility": candidate.get("accessible", "not checked"),
        "semantic_evidence": candidate.get("semantic_breakdown", {}).get("reasons", []),
        "final_rank": candidate.get("final_rank"),
        "eligible": candidate.get("semantic_relevance", 0.0) >= MIN_STOCK_RELEVANCE,
        "selected": candidate.get("stable_key") == selected_key,
    }


def evaluate_cases(cases, *, query_attempts=None, used_assets=None):
    """Score candidate fixtures and return a JSON-serializable per-scene report."""
    used_assets = used_assets if used_assets is not None else set()
    scenes = []
    for index, case in enumerate(cases):
        scene = case["scene"]
        candidates = case.get("candidates", [])
        all_ranked = rank_stock_candidates(scene, candidates, min_semantic=0.0)
        eligible = rank_stock_candidates(scene, candidates, used_assets=used_assets)
        selected = eligible[0] if eligible else None
        if selected:
            used_assets.update(selected.get("stable_keys") or ([selected["stable_key"]] if selected.get("stable_key") else []))

        if query_attempts and index < len(query_attempts):
            attempted = list(query_attempts[index])
        else:
            attempted = []
            for candidate in candidates:
                query = str(candidate.get("query") or "").strip()
                if query and query not in attempted:
                    attempted.append(query)

        plan = scene.get("visual_plan") or {}
        scored_rows = [_candidate_report(candidate, selected.get("stable_key") if selected else None) for candidate in all_ranked]
        scenes.append({
            "scene": case.get("name") or f"scene-{index + 1}",
            "narration": scene.get("narration", ""),
            "visual_objective": plan.get("visual_objective", ""),
            "queries_attempted": attempted,
            "candidates": scored_rows,
            "selected_candidate": _candidate_report(selected, selected.get("stable_key")) if selected else None,
            "selection_reason": (
                f"Selected rank {selected['final_rank']} with semantic relevance {selected['semantic_relevance']:.2f} "
                f"(gate {MIN_STOCK_RELEVANCE:.2f}); technical suitability {selected['technical_suitability']:.2f} "
                "was only a tie-breaker after semantic relevance. A comparable unused asset is preferred over repeating footage. "
                + "; ".join(selected.get("semantic_breakdown", {}).get("reasons", [])[:3])
                if selected else "No candidate passed the semantic relevance gate; preserve the configured still/gradient fallback."
            ),
            "fallback_used": selected is None,
        })
    return {
        "mode": "live-metadata-sample" if query_attempts is not None else "deterministic-fixtures",
        "semantic_threshold": MIN_STOCK_RELEVANCE,
        "early_stop_threshold": EARLY_STOP_SEMANTIC_RELEVANCE,
        "visual_verification_performed": False,
        "note": "Metadata ranking is not proof that the footage visually depicts the scene.",
        "scenes": scenes,
    }


def collect_live_candidates(cases, max_scenes=2):
    """Fetch at most five planned queries per sampled scene using configured keys."""
    if not os.getenv("PEXELS_API_KEY", "").strip() and not os.getenv("PIXABAY_API_KEY", "").strip():
        raise RuntimeError("Set PEXELS_API_KEY and/or PIXABAY_API_KEY in the server environment before using --live.")
    from video_fetcher import VideoFetcher

    fetcher = VideoFetcher()
    selected_cases = cases[:max(1, min(int(max_scenes), len(cases)))]
    attempts_per_scene = []
    for case in selected_cases:
        scene = case["scene"]
        candidates = []
        attempted = []
        for query_rank, phrase in enumerate(search_queries(scene, max_queries=5)):
            attempted.append(phrase)
            found = fetcher._search_pexels(phrase, query_rank, 5)
            found.extend(fetcher._search_pixabay(phrase, query_rank, 5))
            candidates.extend(found)
            if query_rank >= 1 and len(rank_stock_candidates(
                scene, candidates, min_semantic=EARLY_STOP_SEMANTIC_RELEVANCE
            )) >= 3:
                break
        case["candidates"] = candidates
        attempts_per_scene.append(attempted)
    return selected_cases, attempts_per_scene


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="collect bounded real metadata using configured API keys")
    parser.add_argument("--max-scenes", type=int, default=2, help="maximum scenes queried in --live mode (default: 2)")
    parser.add_argument("--output", type=Path, help="write the JSON report to this file instead of stdout")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE, help="candidate fixture JSON (offline mode)")
    args = parser.parse_args()

    cases = json.loads(args.fixture.read_text(encoding="utf-8"))
    attempts = None
    if args.live:
        cases, attempts = collect_live_candidates(cases, args.max_scenes)
    report = evaluate_cases(cases, query_attempts=attempts)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
        print(f"Wrote metadata-only ranking report: {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
