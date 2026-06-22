from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from .golden_snapshot_builder import build_allocation_matrix, build_lifecycle_states, build_theme_scores
    from .reproducibility_manifest import stable_json_hash
    from .theme_explanation_engine import build_theme_explanation
except ImportError:
    try:
        from golden_snapshot_builder import build_allocation_matrix, build_lifecycle_states, build_theme_scores
        from reproducibility_manifest import stable_json_hash
        from theme_explanation_engine import build_theme_explanation
    except ModuleNotFoundError:
        from scripts.golden_snapshot_builder import build_allocation_matrix, build_lifecycle_states, build_theme_scores
        from scripts.reproducibility_manifest import stable_json_hash
        from scripts.theme_explanation_engine import build_theme_explanation


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "research" / "mainline"
SCORING_VERSION = "multi_run_executor_v2"


def latest_report_path() -> Path:
    files = sorted(REPORT_DIR.glob("mainline_review_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError("No mainline report JSON files found.")
    return files[0]


def load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _mainline_ranking(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for index, row in enumerate(report.get("mainline_ranking") or [], start=1):
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "rank": int(row.get("rank") or index),
                "theme_id": row.get("theme_id", ""),
                "theme_name": row.get("theme_name", ""),
                "mainline_score_v6": row.get("mainline_score_v6"),
                "theme_score_v5": row.get("theme_score_v5"),
                "lifecycle_state": row.get("lifecycle_state", ""),
            }
        )
    return rows


def _theme_ids(report: dict[str, Any]) -> list[str]:
    ids = [str(row.get("theme_id") or "") for row in report.get("mainline_ranking") or [] if isinstance(row, dict)]
    if ids:
        return [theme_id for theme_id in ids if theme_id]
    return [
        str(row.get("theme_id") or "")
        for row in (report.get("theme_summary") or {}).get("themes") or []
        if isinstance(row, dict) and row.get("theme_id")
    ]


def _explainability_graph_hashes(report: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for theme_id in _theme_ids(report):
        explanation = build_theme_explanation(report, theme_id)
        result[theme_id] = stable_json_hash(explanation.get("trace_graph") or {})
    return dict(sorted(result.items()))


def build_consistency_projection(report: dict[str, Any]) -> dict[str, Any]:
    projection = {
        "scoring_version": SCORING_VERSION,
        "report_id": report.get("report_id", ""),
        "basis_date": report.get("basis_date", ""),
        "policy_provenance_hash": stable_json_hash(report.get("policy_provenance_summary") or {}),
        "policy_snapshot_hash": stable_json_hash(report.get("policy_snapshot_summary") or {}),
        "theme_scores": build_theme_scores(report),
        "ranking": _mainline_ranking(report),
        "allocation_matrix": build_allocation_matrix(report),
        "lifecycle_states": build_lifecycle_states(report),
        "explainability_graph_hashes": _explainability_graph_hashes(report),
    }
    projection["projection_hash"] = stable_json_hash(projection)
    return projection


def execute_multi_run(report: dict[str, Any], run_count: int = 10) -> list[dict[str, Any]]:
    count = max(2, min(int(run_count), 50))
    runs = []
    for index in range(count):
        projection = build_consistency_projection(report)
        runs.append(
            {
                "run_id": f"run_{index + 1:03d}",
                "projection": projection,
                "output_hash": projection["projection_hash"],
            }
        )
    return runs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Execute deterministic multi-run projections for one report.")
    parser.add_argument("--latest", action="store_true", help="Use latest report.")
    parser.add_argument("--path", type=Path, help="Report JSON path.")
    parser.add_argument("--runs", type=int, default=10)
    args = parser.parse_args(argv)
    path = latest_report_path() if args.latest or not args.path else args.path
    runs = execute_multi_run(load_report(path), args.runs)
    print(json.dumps({"scoring_version": SCORING_VERSION, "run_count": len(runs), "runs": runs}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
