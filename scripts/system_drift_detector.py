from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from diff_report_engine import section_presence_diff
    from golden_snapshot_builder import (
        GOLDEN_PATH,
        SCORING_VERSION,
        build_allocation_matrix,
        build_golden_snapshot,
        build_lifecycle_states,
        build_theme_scores,
        latest_report_path,
        load_drift_rules,
        load_report,
    )
except ModuleNotFoundError:
    from scripts.diff_report_engine import section_presence_diff
    from scripts.golden_snapshot_builder import (
        GOLDEN_PATH,
        SCORING_VERSION,
        build_allocation_matrix,
        build_golden_snapshot,
        build_lifecycle_states,
        build_theme_scores,
        latest_report_path,
        load_drift_rules,
        load_report,
    )


ROOT = Path(__file__).resolve().parents[1]


def load_golden_snapshot(path: Path = GOLDEN_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _status_rank(status: str) -> int:
    return {"perfect_match": 0, "warning": 1, "critical": 2}.get(status, 0)


def combine_status(*statuses: str) -> str:
    return max(statuses or ("perfect_match",), key=_status_rank)


def compute_ranking_drift(golden: dict[str, Any], current_report: dict[str, Any], rules: dict[str, Any] | None = None) -> dict[str, Any]:
    active_rules = rules or load_drift_rules()
    golden_rows = golden.get("mainline_ranking") or []
    current_rows = current_report.get("mainline_ranking") or []
    golden_order = [row.get("theme_id") for row in golden_rows if isinstance(row, dict)]
    current_order = [row.get("theme_id") for row in current_rows if isinstance(row, dict)]
    golden_rank = {theme_id: index + 1 for index, theme_id in enumerate(golden_order)}
    current_rank = {theme_id: index + 1 for index, theme_id in enumerate(current_order)}
    changes = []
    for theme_id in sorted(set(golden_rank) | set(current_rank)):
        before = golden_rank.get(theme_id)
        after = current_rank.get(theme_id)
        if before != after:
            changes.append({"theme_id": theme_id, "golden_rank": before, "current_rank": after})
    status = "perfect_match"
    if golden_order[:1] != current_order[:1]:
        status = str(active_rules.get("top1_change_status") or "critical")
    elif golden_order[:3] != current_order[:3]:
        status = str(active_rules.get("top3_swap_status") or "warning")
    elif changes:
        status = "warning"
    return {
        "status": status,
        "golden_top1": golden_order[0] if golden_order else "",
        "current_top1": current_order[0] if current_order else "",
        "ranking_changes": changes,
        "ranking_change_count": len(changes),
    }


def _score_status(delta: float, warning: float, critical: float) -> str:
    if delta > critical:
        return "critical"
    if delta > warning:
        return "warning"
    return "perfect_match"


def compute_score_drift(golden: dict[str, Any], current_report: dict[str, Any], rules: dict[str, Any] | None = None) -> dict[str, Any]:
    active_rules = rules or load_drift_rules()
    warning = float(active_rules.get("score_warning_threshold", 0.001))
    critical = float(active_rules.get("score_critical_threshold", 0.01))
    golden_scores = golden.get("theme_scores") or {}
    current_scores = build_theme_scores(current_report)
    rows = []
    max_delta = 0.0
    status = "perfect_match"
    for theme_id in sorted(set(golden_scores) | set(current_scores)):
        golden_value = float((golden_scores.get(theme_id) or {}).get("mainline_score_v6") or 0.0)
        current_value = float((current_scores.get(theme_id) or {}).get("mainline_score_v6") or 0.0)
        delta = round(abs(current_value - golden_value), 6)
        max_delta = max(max_delta, delta)
        row_status = _score_status(delta, warning, critical)
        status = combine_status(status, row_status)
        if delta > 0:
            rows.append(
                {
                    "theme_id": theme_id,
                    "golden_mainline_score_v6": golden_value,
                    "current_mainline_score_v6": current_value,
                    "abs_delta": delta,
                    "status": row_status,
                }
            )
    return {
        "status": status,
        "max_abs_delta": round(max_delta, 6),
        "score_changes": rows,
        "score_change_count": len(rows),
    }


def compute_allocation_drift(golden: dict[str, Any], current_report: dict[str, Any], rules: dict[str, Any] | None = None) -> dict[str, Any]:
    active_rules = rules or load_drift_rules()
    warning = float(active_rules.get("allocation_warning_threshold", 0.001))
    critical = float(active_rules.get("allocation_critical_threshold", 0.01))
    golden_matrix = golden.get("allocation_matrix") or {}
    current_matrix = build_allocation_matrix(current_report)
    rows = []
    max_delta = 0.0
    status = "perfect_match"
    for event_id in sorted(set(golden_matrix) | set(current_matrix)):
        golden_themes = golden_matrix.get(event_id) or {}
        current_themes = current_matrix.get(event_id) or {}
        for theme_id in sorted(set(golden_themes) | set(current_themes)):
            missing = event_id not in golden_matrix or event_id not in current_matrix or theme_id not in golden_themes or theme_id not in current_themes
            golden_value = float(golden_themes.get(theme_id) or 0.0)
            current_value = float(current_themes.get(theme_id) or 0.0)
            delta = round(abs(current_value - golden_value), 6)
            max_delta = max(max_delta, delta)
            row_status = "critical" if missing else _score_status(delta, warning, critical)
            status = combine_status(status, row_status)
            if delta > 0 or missing:
                rows.append(
                    {
                        "event_cluster_id": event_id,
                        "theme_id": theme_id,
                        "golden_allocated": golden_value,
                        "current_allocated": current_value,
                        "abs_delta": delta,
                        "missing_pair": missing,
                        "status": row_status,
                    }
                )
    return {
        "status": status,
        "max_abs_delta": round(max_delta, 6),
        "allocation_changes": rows,
        "allocation_change_count": len(rows),
    }


def compute_lifecycle_drift(golden: dict[str, Any], current_report: dict[str, Any], rules: dict[str, Any] | None = None) -> dict[str, Any]:
    active_rules = rules or load_drift_rules()
    golden_states = golden.get("lifecycle_states") or {}
    current_states = build_lifecycle_states(current_report)
    changes = []
    for theme_id in sorted(set(golden_states) | set(current_states)):
        before = golden_states.get(theme_id, "")
        after = current_states.get(theme_id, "")
        if before != after:
            changes.append({"theme_id": theme_id, "golden_lifecycle_state": before, "current_lifecycle_state": after})
    return {
        "status": str(active_rules.get("lifecycle_change_status") or "critical") if changes else "perfect_match",
        "lifecycle_changes": changes,
        "lifecycle_change_count": len(changes),
    }


def compute_structural_diff(golden: dict[str, Any], current_report: dict[str, Any], rules: dict[str, Any] | None = None) -> dict[str, Any]:
    active_rules = rules or load_drift_rules()
    required = list(active_rules.get("required_report_sections") or golden.get("report_sections") or [])
    return section_presence_diff(required, current_report)


def build_drift_report(golden: dict[str, Any], current_report: dict[str, Any], rules: dict[str, Any] | None = None) -> dict[str, Any]:
    active_rules = rules or load_drift_rules()
    ranking = compute_ranking_drift(golden, current_report, active_rules)
    score = compute_score_drift(golden, current_report, active_rules)
    allocation = compute_allocation_drift(golden, current_report, active_rules)
    lifecycle = compute_lifecycle_drift(golden, current_report, active_rules)
    structural = compute_structural_diff(golden, current_report, active_rules)
    drift_status = combine_status(
        ranking["status"],
        score["status"],
        allocation["status"],
        lifecycle["status"],
        structural["status"],
    )
    return {
        "scoring_version": SCORING_VERSION,
        "drift_status": drift_status,
        "golden_snapshot_id": golden.get("snapshot_id", ""),
        "golden_source_report_id": golden.get("source_report_id", ""),
        "current_report_id": current_report.get("report_id", ""),
        "basis_date": current_report.get("basis_date", ""),
        "thresholds": {
            "score_warning_threshold": active_rules.get("score_warning_threshold"),
            "score_critical_threshold": active_rules.get("score_critical_threshold"),
            "allocation_warning_threshold": active_rules.get("allocation_warning_threshold"),
            "allocation_critical_threshold": active_rules.get("allocation_critical_threshold"),
        },
        "ranking_drift": ranking,
        "score_drift": score,
        "allocation_drift": allocation,
        "lifecycle_drift": lifecycle,
        "structural_diff": structural,
        "drift_reasons": [
            key
            for key, value in {
                "ranking_drift": ranking["status"] != "perfect_match",
                "score_drift": score["status"] != "perfect_match",
                "allocation_drift": allocation["status"] != "perfect_match",
                "lifecycle_drift": lifecycle["status"] != "perfect_match",
                "structural_diff": structural["status"] != "perfect_match",
            }.items()
            if value
        ],
    }


def compare_report_to_golden(report: dict[str, Any], golden_path: Path = GOLDEN_PATH) -> dict[str, Any]:
    return build_drift_report(load_golden_snapshot(golden_path), report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detect mainline report drift against golden snapshot.")
    parser.add_argument("--latest", action="store_true", help="Compare latest report.")
    parser.add_argument("--path", type=Path, help="Report JSON path.")
    parser.add_argument("--golden", type=Path, default=GOLDEN_PATH)
    args = parser.parse_args(argv)
    path = latest_report_path() if args.latest or not args.path else args.path
    report = load_report(path)
    drift = build_drift_report(load_golden_snapshot(args.golden), report)
    print(json.dumps(drift, ensure_ascii=False, indent=2))
    return 0 if drift["drift_status"] in {"perfect_match", "warning"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
