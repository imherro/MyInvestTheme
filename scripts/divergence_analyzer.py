from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


SCORING_VERSION = "divergence_analyzer_v2"
CRITICAL_LAYERS = {"scoring", "ranking", "allocation", "lifecycle", "provenance", "snapshot"}


def round6(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return round(number, 6)


def _projection(run: dict[str, Any]) -> dict[str, Any]:
    return run.get("projection") or {}


def _ranking_ids(projection: dict[str, Any]) -> list[str]:
    return [str(row.get("theme_id") or "") for row in projection.get("ranking") or [] if isinstance(row, dict)]


def _score_map(projection: dict[str, Any]) -> dict[str, float]:
    scores = projection.get("theme_scores") or {}
    return {
        str(theme_id): round6((row or {}).get("mainline_score_v6"))
        for theme_id, row in scores.items()
        if isinstance(row, dict)
    }


def _allocation_pairs(projection: dict[str, Any]) -> dict[tuple[str, str], float]:
    result: dict[tuple[str, str], float] = {}
    for event_id, themes in (projection.get("allocation_matrix") or {}).items():
        if not isinstance(themes, dict):
            continue
        for theme_id, value in themes.items():
            result[(str(event_id), str(theme_id))] = round6(value)
    return result


def _max_variance(values_by_key: dict[Any, list[float]]) -> float:
    max_delta = 0.0
    for values in values_by_key.values():
        if not values:
            continue
        max_delta = max(max_delta, round6(max(values) - min(values)))
    return round6(max_delta)


def detect_score_drift_across_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    values: dict[str, list[float]] = {}
    for run in runs:
        for theme_id, score in _score_map(_projection(run)).items():
            values.setdefault(theme_id, []).append(score)
    divergences = []
    baseline = _score_map(_projection(runs[0])) if runs else {}
    for run in runs[1:]:
        current = _score_map(_projection(run))
        for theme_id in sorted(set(baseline) | set(current)):
            if round6(baseline.get(theme_id)) != round6(current.get(theme_id)):
                divergences.append(
                    {
                        "run_id": run.get("run_id", ""),
                        "theme_id": theme_id,
                        "baseline_score": round6(baseline.get(theme_id)),
                        "current_score": round6(current.get(theme_id)),
                    }
                )
    return {
        "status": "pass" if not divergences else "fail",
        "score_variance": _max_variance(values),
        "score_divergences": divergences,
        "score_divergence_count": len(divergences),
    }


def detect_ranking_swap(runs: list[dict[str, Any]]) -> dict[str, Any]:
    if not runs:
        return {"status": "pass", "ranking_changes": [], "ranking_change_count": 0}
    baseline = _ranking_ids(_projection(runs[0]))
    changes = []
    for run in runs[1:]:
        current = _ranking_ids(_projection(run))
        if current != baseline:
            changes.append({"run_id": run.get("run_id", ""), "baseline_ranking": baseline, "current_ranking": current})
    return {
        "status": "pass" if not changes else "fail",
        "ranking_changes": changes,
        "ranking_change_count": len(changes),
    }


def detect_allocation_variance(runs: list[dict[str, Any]]) -> dict[str, Any]:
    values: dict[tuple[str, str], list[float]] = {}
    for run in runs:
        for key, value in _allocation_pairs(_projection(run)).items():
            values.setdefault(key, []).append(value)
    baseline = _allocation_pairs(_projection(runs[0])) if runs else {}
    divergences = []
    for run in runs[1:]:
        current = _allocation_pairs(_projection(run))
        for key in sorted(set(baseline) | set(current)):
            before = round6(baseline.get(key))
            after = round6(current.get(key))
            if before != after:
                divergences.append(
                    {
                        "run_id": run.get("run_id", ""),
                        "event_cluster_id": key[0],
                        "theme_id": key[1],
                        "baseline_allocated": before,
                        "current_allocated": after,
                    }
                )
    return {
        "status": "pass" if not divergences else "fail",
        "allocation_variance": _max_variance(values),
        "allocation_divergences": divergences,
        "allocation_divergence_count": len(divergences),
    }


def detect_lifecycle_divergence(runs: list[dict[str, Any]]) -> dict[str, Any]:
    baseline = (_projection(runs[0]).get("lifecycle_states") or {}) if runs else {}
    divergences = []
    for run in runs[1:]:
        current = _projection(run).get("lifecycle_states") or {}
        for theme_id in sorted(set(baseline) | set(current)):
            if baseline.get(theme_id, "") != current.get(theme_id, ""):
                divergences.append(
                    {
                        "run_id": run.get("run_id", ""),
                        "theme_id": theme_id,
                        "baseline_lifecycle_state": baseline.get(theme_id, ""),
                        "current_lifecycle_state": current.get(theme_id, ""),
                    }
                )
    return {
        "status": "pass" if not divergences else "fail",
        "lifecycle_divergences": divergences,
        "lifecycle_divergence_count": len(divergences),
    }


def detect_graph_divergence(runs: list[dict[str, Any]]) -> dict[str, Any]:
    baseline = (_projection(runs[0]).get("explainability_graph_hashes") or {}) if runs else {}
    divergences = []
    for run in runs[1:]:
        current = _projection(run).get("explainability_graph_hashes") or {}
        for theme_id in sorted(set(baseline) | set(current)):
            if baseline.get(theme_id, "") != current.get(theme_id, ""):
                divergences.append(
                    {
                        "run_id": run.get("run_id", ""),
                        "theme_id": theme_id,
                        "baseline_graph_hash": baseline.get(theme_id, ""),
                        "current_graph_hash": current.get(theme_id, ""),
                    }
                )
    return {
        "status": "pass" if not divergences else "fail",
        "graph_divergences": divergences,
        "graph_divergence_count": len(divergences),
    }


def detect_source_layer_divergence(runs: list[dict[str, Any]]) -> dict[str, Any]:
    baseline = _projection(runs[0]) if runs else {}
    divergences = []
    for run in runs[1:]:
        current = _projection(run)
        for field, layer in (
            ("policy_provenance_hash", "provenance"),
            ("policy_snapshot_hash", "snapshot"),
        ):
            if baseline.get(field, "") != current.get(field, ""):
                divergences.append(
                    {
                        "run_id": run.get("run_id", ""),
                        "layer": layer,
                        "field": field,
                        "baseline_hash": baseline.get(field, ""),
                        "current_hash": current.get(field, ""),
                    }
                )
    return {
        "status": "pass" if not divergences else "fail",
        "source_layer_divergences": divergences,
        "source_layer_divergence_count": len(divergences),
    }


def classify_root_cause(analysis: dict[str, Any]) -> dict[str, Any]:
    source_divergences = (analysis.get("source_layer") or {}).get("source_layer_divergences", [])
    checks = [
        ("provenance", [row for row in source_divergences if row.get("layer") == "provenance"], 0.95),
        ("snapshot", [row for row in source_divergences if row.get("layer") == "snapshot"], 0.95),
        ("allocation", (analysis.get("allocation") or {}).get("allocation_divergences", []), 0.9),
        ("lifecycle", (analysis.get("lifecycle") or {}).get("lifecycle_divergences", []), 0.85),
        ("scoring", (analysis.get("score") or {}).get("score_divergences", []), 0.8),
        ("ranking", (analysis.get("ranking") or {}).get("ranking_changes", []), 0.75),
        ("explainability", (analysis.get("graph") or {}).get("graph_divergences", []), 0.7),
    ]
    for layer, rows, confidence in checks:
        if rows:
            return {"layer": layer, "confidence": confidence}
    return {"layer": "none", "confidence": 0.0}


def analyze_run_divergence(runs: list[dict[str, Any]]) -> dict[str, Any]:
    score = detect_score_drift_across_runs(runs)
    ranking = detect_ranking_swap(runs)
    allocation = detect_allocation_variance(runs)
    lifecycle = detect_lifecycle_divergence(runs)
    graph = detect_graph_divergence(runs)
    source_layer = detect_source_layer_divergence(runs)
    analysis = {
        "scoring_version": SCORING_VERSION,
        "run_count": len(runs),
        "score": score,
        "ranking": ranking,
        "allocation": allocation,
        "lifecycle": lifecycle,
        "graph": graph,
        "source_layer": source_layer,
    }
    root_cause = classify_root_cause(analysis)
    divergence = []
    for layer, check in (
        ("score", score),
        ("ranking", ranking),
        ("allocation", allocation),
        ("lifecycle", lifecycle),
        ("graph", graph),
        ("source_layer", source_layer),
    ):
        if check.get("status") != "pass":
            divergence.append(layer)
    if not divergence:
        status = "stable"
    elif any(layer in CRITICAL_LAYERS for layer in [root_cause.get("layer"), *divergence]):
        status = "critical"
    else:
        status = "unstable"
    analysis.update(
        {
            "consistency_status": status,
            "divergence": divergence,
            "score_variance": score["score_variance"],
            "allocation_variance": allocation["allocation_variance"],
            "ranking_changes": ranking["ranking_changes"],
            "root_cause": root_cause,
        }
    )
    return analysis


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze divergence across previously captured run projections.")
    parser.add_argument("path", type=Path, help="JSON file containing a runs list.")
    args = parser.parse_args(argv)
    payload = json.loads(args.path.read_text(encoding="utf-8"))
    runs = payload.get("runs") if isinstance(payload, dict) else payload
    print(json.dumps(analyze_run_divergence(runs), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
