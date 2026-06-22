from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from .counterfactual_simulator import (
        CounterfactualTargetNotFound,
        latest_report_path,
        load_report,
        round4,
        simulate_remove_event,
        simulate_remove_policy,
    )
except ImportError:
    try:
        from counterfactual_simulator import (
            CounterfactualTargetNotFound,
            latest_report_path,
            load_report,
            round4,
            simulate_remove_event,
            simulate_remove_policy,
        )
    except ModuleNotFoundError:
        from scripts.counterfactual_simulator import (
            CounterfactualTargetNotFound,
            latest_report_path,
            load_report,
            round4,
            simulate_remove_event,
            simulate_remove_policy,
        )


SCORING_VERSION = "mainline_sensitivity_engine_v2"


def _theme_id(row: dict[str, Any]) -> str:
    return str(row.get("theme_id") or row.get("theme_name") or row.get("theme") or "")


def _find_theme(report: dict[str, Any], theme_id_or_name: str) -> dict[str, Any]:
    for theme in (report.get("theme_summary") or {}).get("themes") or []:
        if not isinstance(theme, dict):
            continue
        keys = {_theme_id(theme), str(theme.get("theme_name") or ""), str(theme.get("theme") or "")}
        if theme_id_or_name in keys:
            return theme
    raise CounterfactualTargetNotFound(theme_id_or_name)


def _event_rows(theme: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in theme.get("all_event_contributors") or theme.get("top_event_contributors") or [] if isinstance(row, dict)]


def _policy_ids_for_theme(theme: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for event in _event_rows(theme):
        primary = str(event.get("primary_policy_id") or "")
        if primary:
            ids.append(primary)
        ids.extend(str(policy_id) for policy_id in event.get("member_policy_ids") or [] if policy_id)
    return sorted({policy_id for policy_id in ids if policy_id})


def _event_ids_for_theme(theme: dict[str, Any]) -> list[str]:
    return sorted({str(event.get("event_cluster_id")) for event in _event_rows(theme) if event.get("event_cluster_id")})


def _impact_for_theme(simulation: dict[str, Any], theme_id: str) -> dict[str, Any]:
    for row in simulation.get("theme_impacts") or []:
        if row.get("theme_id") == theme_id:
            return row
    return {
        "theme_id": theme_id,
        "baseline_score_v6": 0.0,
        "counterfactual_score": 0.0,
        "delta": 0.0,
        "score_drop": 0.0,
    }


def _policy_impacts(report: dict[str, Any], theme_id: str, policy_ids: list[str]) -> list[dict[str, Any]]:
    rows = []
    for policy_id in policy_ids:
        simulation = simulate_remove_policy(report, policy_id)
        impact = _impact_for_theme(simulation, theme_id)
        rows.append(
            {
                "policy_id": policy_id,
                "baseline_score_v6": impact.get("baseline_score_v6", 0.0),
                "counterfactual_score": impact.get("counterfactual_score", 0.0),
                "delta": impact.get("delta", 0.0),
                "score_drop": impact.get("score_drop", 0.0),
                "ranking_changed": simulation.get("ranking_changed", False),
                "top1_changed": simulation.get("top1_changed", False),
            }
        )
    return _rank_impacts(rows, "policy_id")


def _event_impacts(report: dict[str, Any], theme_id: str, event_ids: list[str]) -> list[dict[str, Any]]:
    rows = []
    for event_id in event_ids:
        simulation = simulate_remove_event(report, event_id)
        impact = _impact_for_theme(simulation, theme_id)
        rows.append(
            {
                "event_cluster_id": event_id,
                "baseline_score_v6": impact.get("baseline_score_v6", 0.0),
                "counterfactual_score": impact.get("counterfactual_score", 0.0),
                "delta": impact.get("delta", 0.0),
                "score_drop": impact.get("score_drop", 0.0),
                "ranking_changed": simulation.get("ranking_changed", False),
                "top1_changed": simulation.get("top1_changed", False),
            }
        )
    return _rank_impacts(rows, "event_cluster_id")


def _rank_impacts(rows: list[dict[str, Any]], id_field: str) -> list[dict[str, Any]]:
    ranked = sorted(rows, key=lambda row: (-round4(row.get("score_drop")), row.get(id_field, "")))
    for index, row in enumerate(ranked, start=1):
        row["impact_rank"] = index
    return ranked


def build_theme_sensitivity(report: dict[str, Any], theme_id_or_name: str) -> dict[str, Any]:
    theme = _find_theme(report, theme_id_or_name)
    theme_id = _theme_id(theme)
    baseline_score = round4(theme.get("mainline_score_v6"))
    policy_ids = _policy_ids_for_theme(theme)
    event_ids = _event_ids_for_theme(theme)
    policy_impacts = _policy_impacts(report, theme_id, policy_ids) if policy_ids else []
    event_impacts = _event_impacts(report, theme_id, event_ids) if event_ids else []
    top_policy = policy_impacts[0] if policy_impacts else {}
    top_event = event_impacts[0] if event_impacts else {}
    policy_count = len(policy_impacts)
    event_count = len(event_impacts)
    total_policy_drop = round4(sum(round4(row.get("score_drop")) for row in policy_impacts))
    total_event_drop = round4(sum(round4(row.get("score_drop")) for row in event_impacts))
    return {
        "scoring_version": SCORING_VERSION,
        "status": "pass",
        "baseline_report_id": report.get("report_id", ""),
        "basis_date": report.get("basis_date", ""),
        "theme_id": theme_id,
        "theme_name": theme.get("theme_name", ""),
        "baseline_score_v6": baseline_score,
        "policy_count": policy_count,
        "event_count": event_count,
        "sensitivity_index": round4(total_policy_drop / policy_count) if policy_count else 0.0,
        "event_sensitivity_index": round4(total_event_drop / event_count) if event_count else 0.0,
        "normalized_sensitivity_index": round4(round4(top_policy.get("score_drop")) / baseline_score) if baseline_score else 0.0,
        "top_policy_driver": top_policy,
        "top_event_driver": top_event,
        "policy_impacts": policy_impacts,
        "event_impacts": event_impacts,
        "overlay_only": True,
        "writes_report": False,
    }


def print_text_summary(result: dict[str, Any]) -> None:
    print(f"THEME: {result.get('theme_name')} ({result.get('theme_id')})")
    print(f"SENSITIVITY_INDEX: {result.get('sensitivity_index')}")
    print("TOP POLICY DRIVER:")
    top_policy = result.get("top_policy_driver") or {}
    print(f"- {top_policy.get('policy_id')}: drop={top_policy.get('score_drop')}")
    print("TOP EVENT DRIVER:")
    top_event = result.get("top_event_driver") or {}
    print(f"- {top_event.get('event_cluster_id')}: drop={top_event.get('score_drop')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Calculate counterfactual sensitivity for a mainline theme.")
    parser.add_argument("--theme", required=True, help="Theme id or theme name.")
    parser.add_argument("--latest", action="store_true", help="Use latest report.")
    parser.add_argument("--path", type=Path, help="Report JSON path.")
    parser.add_argument("--json", action="store_true", help="Print full JSON.")
    args = parser.parse_args(argv)
    path = latest_report_path() if args.latest or not args.path else args.path
    report = load_report(path)
    try:
        result = build_theme_sensitivity(report, args.theme)
    except CounterfactualTargetNotFound:
        print(f"Theme not found: {args.theme}")
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_text_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
