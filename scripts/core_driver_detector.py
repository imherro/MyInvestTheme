from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

try:
    from .counterfactual_simulator import collect_policy_ids, latest_report_path, load_report, round4, simulate_remove_policy
except ImportError:
    try:
        from counterfactual_simulator import collect_policy_ids, latest_report_path, load_report, round4, simulate_remove_policy
    except ModuleNotFoundError:
        from scripts.counterfactual_simulator import collect_policy_ids, latest_report_path, load_report, round4, simulate_remove_policy


SCORING_VERSION = "core_driver_detector_v2"


def _baseline_total_score(report: dict[str, Any]) -> float:
    return round4(sum(round4(row.get("mainline_score_v6")) for row in report.get("mainline_ranking") or [] if isinstance(row, dict)))


def detect_core_drivers(report: dict[str, Any]) -> dict[str, Any]:
    policy_ids = collect_policy_ids(report)
    rows: list[dict[str, Any]] = []
    for policy_id in policy_ids:
        simulation = simulate_remove_policy(report, policy_id)
        total_drop = round4((simulation.get("impact_summary") or {}).get("total_mainline_score_drop"))
        top_theme = (simulation.get("theme_impacts") or [{}])[0]
        rows.append(
            {
                "policy_id": policy_id,
                "total_mainline_score_drop": total_drop,
                "top_impacted_theme_id": top_theme.get("theme_id", ""),
                "top_impacted_theme_drop": top_theme.get("score_drop", 0.0),
                "ranking_changed": simulation.get("ranking_changed", False),
                "top1_changed": simulation.get("top1_changed", False),
            }
        )
    ranked = sorted(rows, key=lambda row: (-round4(row.get("total_mainline_score_drop")), row.get("policy_id", "")))
    total_impact = round4(sum(round4(row.get("total_mainline_score_drop")) for row in ranked))
    cumulative = 0.0
    for index, row in enumerate(ranked, start=1):
        impact_share = round4(round4(row.get("total_mainline_score_drop")) / total_impact) if total_impact else 0.0
        cumulative = round4(cumulative + impact_share)
        row["impact_rank"] = index
        row["impact_share_of_total"] = impact_share
        row["cumulative_impact_share"] = cumulative

    top_one_percent_count = max(1, math.ceil(len(ranked) * 0.01)) if ranked else 0
    core_drivers: list[dict[str, Any]] = []
    cumulative_core_share = 0.0
    for row in ranked:
        if len(core_drivers) < top_one_percent_count or cumulative_core_share < 0.8:
            core_drivers.append(row)
            cumulative_core_share = round4(cumulative_core_share + round4(row.get("impact_share_of_total")))
    return {
        "scoring_version": SCORING_VERSION,
        "status": "pass",
        "baseline_report_id": report.get("report_id", ""),
        "basis_date": report.get("basis_date", ""),
        "baseline_total_mainline_score_v6": _baseline_total_score(report),
        "policy_count": len(policy_ids),
        "top_one_percent_count": top_one_percent_count,
        "total_counterfactual_impact": total_impact,
        "core_driver_count": len(core_drivers),
        "core_driver_cumulative_impact_share": cumulative_core_share,
        "core_drivers": core_drivers,
        "policy_impacts": ranked,
        "overlay_only": True,
        "writes_report": False,
    }


def print_text_summary(result: dict[str, Any]) -> None:
    print(f"REPORT: {result.get('baseline_report_id')}")
    print(f"CORE_DRIVER_COUNT: {result.get('core_driver_count')}")
    print("CORE DRIVERS:")
    for row in result.get("core_drivers") or []:
        print(
            "- "
            f"{row.get('policy_id')}: total_drop={row.get('total_mainline_score_drop')} "
            f"share={row.get('impact_share_of_total')}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detect core policy drivers by counterfactual impact.")
    parser.add_argument("--latest", action="store_true", help="Use latest report.")
    parser.add_argument("--path", type=Path, help="Report JSON path.")
    parser.add_argument("--json", action="store_true", help="Print full JSON.")
    args = parser.parse_args(argv)
    path = latest_report_path() if args.latest or not args.path else args.path
    result = detect_core_drivers(load_report(path))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_text_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
