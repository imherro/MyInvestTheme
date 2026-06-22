from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from .divergence_analyzer import analyze_run_divergence
    from .multi_run_executor import execute_multi_run, latest_report_path, load_report
except ImportError:
    try:
        from divergence_analyzer import analyze_run_divergence
        from multi_run_executor import execute_multi_run, latest_report_path, load_report
    except ModuleNotFoundError:
        from scripts.divergence_analyzer import analyze_run_divergence
        from scripts.multi_run_executor import execute_multi_run, latest_report_path, load_report


SCORING_VERSION = "system_consistency_oracle_v2"


def build_consistency_oracle(report: dict[str, Any], run_count: int = 10) -> dict[str, Any]:
    runs = execute_multi_run(report, run_count)
    analysis = analyze_run_divergence(runs)
    return {
        "scoring_version": SCORING_VERSION,
        "consistency_status": analysis["consistency_status"],
        "baseline_report_id": report.get("report_id", ""),
        "basis_date": report.get("basis_date", ""),
        "run_count": analysis["run_count"],
        "score_variance": analysis["score_variance"],
        "ranking_changes": analysis["ranking_changes"],
        "allocation_variance": analysis["allocation_variance"],
        "root_cause": analysis["root_cause"],
        "divergence": analysis["divergence"],
        "analysis": analysis,
        "run_hashes": [{"run_id": run.get("run_id", ""), "output_hash": run.get("output_hash", "")} for run in runs],
        "overlay_only": True,
        "writes_report": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic system consistency oracle.")
    parser.add_argument("--latest", action="store_true", help="Use latest report.")
    parser.add_argument("--path", type=Path, help="Report JSON path.")
    parser.add_argument("--runs", type=int, default=10)
    args = parser.parse_args(argv)
    path = latest_report_path() if args.latest or not args.path else args.path
    result = build_consistency_oracle(load_report(path), args.runs)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["consistency_status"] == "stable" else 1


if __name__ == "__main__":
    raise SystemExit(main())
