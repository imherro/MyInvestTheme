from __future__ import annotations

import argparse
import json
import math
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any

try:
    from .mainline_lifecycle import (
        build_lifecycle_adjusted_theme_summary,
        build_mainline_lifecycle_summary,
        parse_date,
    )
except ImportError:
    try:
        from mainline_lifecycle import (
            build_lifecycle_adjusted_theme_summary,
            build_mainline_lifecycle_summary,
            parse_date,
        )
    except ModuleNotFoundError:
        from scripts.mainline_lifecycle import (
            build_lifecycle_adjusted_theme_summary,
            build_mainline_lifecycle_summary,
            parse_date,
        )


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "research" / "mainline"
SCORING_VERSION = "counterfactual_mainline_simulator_v2"


class CounterfactualTargetNotFound(KeyError):
    pass


def latest_report_path() -> Path:
    files = sorted(REPORT_DIR.glob("mainline_review_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError("No mainline report JSON files found.")
    return files[0]


def load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def round4(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return round(number, 4)


def round6(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return round(number, 6)


def _theme_id(row: dict[str, Any]) -> str:
    return str(row.get("theme_id") or row.get("theme_name") or row.get("theme") or "")


def _source_themes(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in (report.get("theme_summary") or {}).get("themes") or [] if isinstance(row, dict)]


def _baseline_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [row for row in report.get("mainline_ranking") or [] if isinstance(row, dict)]
    if not rows:
        rows = _source_themes(report)
    return _rank_rows(deepcopy(rows))


def _rank_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sorted_rows = sorted(
        rows,
        key=lambda row: (
            -round4(row.get("mainline_score_v6")),
            -round4(row.get("theme_score_v5")),
            _theme_id(row),
        ),
    )
    for index, row in enumerate(sorted_rows, start=1):
        row["rank"] = index
    return sorted_rows


def _event_rows(theme: dict[str, Any]) -> list[dict[str, Any]]:
    return [deepcopy(row) for row in theme.get("all_event_contributors") or theme.get("top_event_contributors") or [] if isinstance(row, dict)]


def _sort_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        events,
        key=lambda row: (
            -round4(row.get("allocated_cluster_contribution")),
            -round4(row.get("allocation_share")),
            row.get("event_cluster_id", ""),
        ),
    )


def _rebuild_theme_from_events(theme: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    sorted_events = _sort_events(events)
    positive_events = [row for row in sorted_events if round4(row.get("allocated_cluster_contribution")) > 0]
    score = round4(sum(round4(row.get("allocated_cluster_contribution")) for row in positive_events))
    count = len(positive_events)
    role_counts = {"primary": 0, "co_primary": 0, "secondary": 0, "peripheral": 0}
    for row in positive_events:
        role = str(row.get("allocation_role") or "peripheral")
        if role in role_counts:
            role_counts[role] += 1
    rebuilt = deepcopy(theme)
    rebuilt.update(
        {
            "theme_score_v5": score,
            "matched_allocated_event_count": count,
            "primary_event_count": role_counts["primary"],
            "co_primary_event_count": role_counts["co_primary"],
            "secondary_event_count": role_counts["secondary"],
            "peripheral_event_count": role_counts["peripheral"],
            "avg_allocation_share": round4(sum(round4(row.get("allocation_share")) for row in positive_events) / count) if count else 0.0,
            "avg_cluster_relevance_score_v2": round4(sum(round4(row.get("cluster_relevance_score_v2")) for row in positive_events) / count) if count else 0.0,
            "avg_cluster_policy_score_v2": round4(sum(round4(row.get("cluster_policy_score_v2")) for row in positive_events) / count) if count else 0.0,
            "avg_cluster_stance_score_v2": round4(sum(round4(row.get("cluster_stance_score_v2")) for row in positive_events) / count) if count else 0.0,
            "top_event_contributors": sorted_events[:3],
            "all_event_contributors": sorted_events,
        }
    )
    return rebuilt


def _as_of_date(report: dict[str, Any]) -> date:
    parsed = parse_date(report.get("basis_date"))
    if parsed is not None:
        return parsed
    generated = str(report.get("generated_at_iso") or report.get("generated_at") or "")[:10]
    parsed = parse_date(generated)
    return parsed or date.today()


def _recompute_counterfactual_rows(report: dict[str, Any], themes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    theme_summary = deepcopy(report.get("theme_summary") or {})
    theme_summary["themes"] = themes
    lifecycle_summary = build_mainline_lifecycle_summary(theme_summary, _as_of_date(report))
    adjusted_summary = build_lifecycle_adjusted_theme_summary(theme_summary, lifecycle_summary)
    return _rank_rows([row for row in adjusted_summary.get("themes") or [] if isinstance(row, dict)])


def _unique_policy_ids(event: dict[str, Any]) -> list[str]:
    primary = str(event.get("primary_policy_id") or "")
    ids = [str(policy_id) for policy_id in event.get("member_policy_ids") or [] if policy_id]
    if primary and primary not in ids:
        ids.insert(0, primary)
    return [policy_id for index, policy_id in enumerate(ids) if policy_id and policy_id not in ids[:index]]


def collect_policy_ids(report: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for cluster in (report.get("event_cluster_summary") or {}).get("clusters") or []:
        if not isinstance(cluster, dict):
            continue
        for policy_id in _unique_policy_ids(cluster):
            ids.append(policy_id)
    for theme in _source_themes(report):
        for event in _event_rows(theme):
            for policy_id in _unique_policy_ids(event):
                ids.append(policy_id)
    return sorted({policy_id for policy_id in ids if policy_id})


def collect_event_ids(report: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for cluster in (report.get("event_cluster_summary") or {}).get("clusters") or []:
        if isinstance(cluster, dict) and cluster.get("event_cluster_id"):
            ids.append(str(cluster["event_cluster_id"]))
    for theme in _source_themes(report):
        ids.extend(str(event.get("event_cluster_id")) for event in _event_rows(theme) if event.get("event_cluster_id"))
    return sorted({event_id for event_id in ids if event_id})


def _remove_policy_from_event(event: dict[str, Any], policy_id: str) -> tuple[dict[str, Any] | None, float, str]:
    member_ids = _unique_policy_ids(event)
    if policy_id not in member_ids:
        return deepcopy(event), 0.0, "not_matched"
    current_contribution = round4(event.get("allocated_cluster_contribution"))
    if len(member_ids) <= 1:
        return None, current_contribution, "single_policy_event_removed"
    remaining_ids = [member_id for member_id in member_ids if member_id != policy_id]
    removed_share = round6(1 / len(member_ids))
    removed_contribution = round4(current_contribution * removed_share)
    updated = deepcopy(event)
    updated["member_policy_ids"] = remaining_ids
    updated["cluster_size"] = len(remaining_ids)
    updated["allocated_cluster_contribution"] = round4(max(current_contribution - removed_contribution, 0.0))
    updated["counterfactual_removed_policy_id"] = policy_id
    updated["counterfactual_removed_policy_share"] = removed_share
    if updated.get("primary_policy_id") == policy_id and remaining_ids:
        updated["primary_policy_id"] = remaining_ids[0]
        updated["primary_policy_title"] = f"counterfactual remaining policy {remaining_ids[0]}"
    return updated, removed_contribution, "multi_policy_event_reduced"


def _apply_remove_event(report: dict[str, Any], event_cluster_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    removed: list[dict[str, Any]] = []
    themes: list[dict[str, Any]] = []
    for theme in _source_themes(report):
        kept_events = []
        for event in _event_rows(theme):
            if str(event.get("event_cluster_id") or "") == event_cluster_id:
                removed.append(
                    {
                        "theme_id": _theme_id(theme),
                        "theme_name": theme.get("theme_name", ""),
                        "event_cluster_id": event_cluster_id,
                        "removed_contribution": round4(event.get("allocated_cluster_contribution")),
                        "removal_mode": "event_removed",
                    }
                )
                continue
            kept_events.append(event)
        themes.append(_rebuild_theme_from_events(theme, kept_events))
    return themes, removed


def _apply_remove_policy(report: dict[str, Any], policy_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    removed: list[dict[str, Any]] = []
    themes: list[dict[str, Any]] = []
    for theme in _source_themes(report):
        kept_events = []
        for event in _event_rows(theme):
            updated, removed_contribution, mode = _remove_policy_from_event(event, policy_id)
            if removed_contribution > 0:
                removed.append(
                    {
                        "theme_id": _theme_id(theme),
                        "theme_name": theme.get("theme_name", ""),
                        "event_cluster_id": event.get("event_cluster_id", ""),
                        "removed_policy_id": policy_id,
                        "removed_contribution": removed_contribution,
                        "removal_mode": mode,
                    }
                )
            if updated is not None:
                kept_events.append(updated)
        themes.append(_rebuild_theme_from_events(theme, kept_events))
    return themes, removed


def _short_ranking(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "rank": int(row.get("rank") or index + 1),
            "theme_id": _theme_id(row),
            "theme_name": row.get("theme_name", ""),
            "mainline_score_v6": round4(row.get("mainline_score_v6")),
            "theme_score_v5": round4(row.get("theme_score_v5")),
            "lifecycle_state": row.get("lifecycle_state", ""),
        }
        for index, row in enumerate(rows)
    ]


def _theme_impacts(baseline_rows: list[dict[str, Any]], counterfactual_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    baseline_by_theme = {_theme_id(row): row for row in baseline_rows}
    counterfactual_by_theme = {_theme_id(row): row for row in counterfactual_rows}
    rows: list[dict[str, Any]] = []
    for theme_id in sorted(set(baseline_by_theme) | set(counterfactual_by_theme)):
        baseline = baseline_by_theme.get(theme_id, {})
        counterfactual = counterfactual_by_theme.get(theme_id, {})
        baseline_score = round4(baseline.get("mainline_score_v6"))
        counterfactual_score = round4(counterfactual.get("mainline_score_v6"))
        delta = round4(counterfactual_score - baseline_score)
        rows.append(
            {
                "theme_id": theme_id,
                "theme_name": baseline.get("theme_name") or counterfactual.get("theme_name", ""),
                "baseline_rank": baseline.get("rank"),
                "counterfactual_rank": counterfactual.get("rank"),
                "baseline_score_v6": baseline_score,
                "counterfactual_score": counterfactual_score,
                "counterfactual_score_v6": counterfactual_score,
                "delta": delta,
                "score_drop": round4(max(baseline_score - counterfactual_score, 0.0)),
                "baseline_theme_score_v5": round4(baseline.get("theme_score_v5")),
                "counterfactual_theme_score_v5": round4(counterfactual.get("theme_score_v5")),
                "baseline_lifecycle_state": baseline.get("lifecycle_state", ""),
                "counterfactual_lifecycle_state": counterfactual.get("lifecycle_state", ""),
            }
        )
    sorted_rows = sorted(rows, key=lambda row: (-round4(row.get("score_drop")), row.get("theme_id", "")))
    for index, row in enumerate(sorted_rows, start=1):
        row["impact_rank"] = index
    return sorted_rows


def _simulation_result(
    report: dict[str, Any],
    simulation_type: str,
    target_id: str,
    counterfactual_themes: list[dict[str, Any]],
    removed_contributions: list[dict[str, Any]],
) -> dict[str, Any]:
    if not removed_contributions:
        raise CounterfactualTargetNotFound(target_id)
    baseline_rows = _baseline_rows(report)
    counterfactual_rows = _recompute_counterfactual_rows(report, counterfactual_themes)
    baseline_order = [_theme_id(row) for row in baseline_rows]
    counterfactual_order = [_theme_id(row) for row in counterfactual_rows]
    impacts = _theme_impacts(baseline_rows, counterfactual_rows)
    top_impact = impacts[0] if impacts else {}
    target_key = "removed_policy" if simulation_type == "remove_policy" else "removed_event_cluster_id"
    total_drop = round4(sum(round4(row.get("score_drop")) for row in impacts))
    result = {
        "scoring_version": SCORING_VERSION,
        "status": "pass",
        "simulation_type": simulation_type,
        "baseline_report_id": report.get("report_id", ""),
        "basis_date": report.get("basis_date", ""),
        "target": {"type": simulation_type.replace("remove_", ""), "id": target_id},
        target_key: target_id,
        "theme_id": top_impact.get("theme_id", ""),
        "baseline_score_v6": top_impact.get("baseline_score_v6", 0.0),
        "counterfactual_score": top_impact.get("counterfactual_score", 0.0),
        "delta": top_impact.get("delta", 0.0),
        "impact_rank": top_impact.get("impact_rank", 0),
        "ranking_changed": baseline_order != counterfactual_order,
        "top1_changed": (baseline_order[:1] != counterfactual_order[:1]),
        "overlay_only": True,
        "writes_report": False,
        "baseline_ranking": _short_ranking(baseline_rows),
        "counterfactual_ranking": _short_ranking(counterfactual_rows),
        "theme_impacts": impacts,
        "removed_contributions": sorted(
            removed_contributions,
            key=lambda row: (row.get("event_cluster_id", ""), row.get("theme_id", "")),
        ),
        "impact_summary": {
            "affected_theme_count": sum(1 for row in impacts if round4(row.get("score_drop")) > 0),
            "removed_contribution_total": round4(sum(round4(row.get("removed_contribution")) for row in removed_contributions)),
            "total_mainline_score_drop": total_drop,
            "max_theme_score_drop": round4(top_impact.get("score_drop", 0.0)),
        },
    }
    return result


def simulate_remove_event(report: dict[str, Any], event_cluster_id: str) -> dict[str, Any]:
    themes, removed = _apply_remove_event(report, event_cluster_id)
    return _simulation_result(report, "remove_event", event_cluster_id, themes, removed)


def simulate_remove_policy(report: dict[str, Any], policy_id: str) -> dict[str, Any]:
    themes, removed = _apply_remove_policy(report, policy_id)
    return _simulation_result(report, "remove_policy", policy_id, themes, removed)


def print_text_summary(result: dict[str, Any]) -> None:
    target = result.get("target") or {}
    print(f"SIMULATION: {result.get('simulation_type')} {target.get('id')}")
    print(f"REPORT: {result.get('baseline_report_id')}")
    print(
        "TOP IMPACT: "
        f"{result.get('theme_id')} "
        f"{result.get('baseline_score_v6')} -> {result.get('counterfactual_score')} "
        f"delta={result.get('delta')}"
    )
    print(f"RANKING_CHANGED: {result.get('ranking_changed')}")
    print("THEME IMPACTS:")
    for row in (result.get("theme_impacts") or [])[:5]:
        print(
            "- "
            f"{row.get('theme_id')}: {row.get('baseline_score_v6')} -> {row.get('counterfactual_score')} "
            f"drop={row.get('score_drop')}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run counterfactual mainline simulations.")
    parser.add_argument("--latest", action="store_true", help="Use latest report.")
    parser.add_argument("--path", type=Path, help="Report JSON path.")
    parser.add_argument("--remove-policy", help="Policy id to remove.")
    parser.add_argument("--remove-event", help="Event cluster id to remove.")
    parser.add_argument("--json", action="store_true", help="Print full JSON.")
    args = parser.parse_args(argv)
    if bool(args.remove_policy) == bool(args.remove_event):
        parser.error("Pass exactly one of --remove-policy or --remove-event.")
    path = latest_report_path() if args.latest or not args.path else args.path
    report = load_report(path)
    try:
        result = (
            simulate_remove_policy(report, args.remove_policy)
            if args.remove_policy
            else simulate_remove_event(report, args.remove_event)
        )
    except CounterfactualTargetNotFound as exc:
        print(f"Counterfactual target not found: {exc.args[0]}")
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_text_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
