from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "config" / "theme_allocation_rules.json"


def round4(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return round(number, 4)


def load_allocation_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def compute_raw_event_theme_contribution(row: dict[str, Any]) -> float:
    if row.get("stance_adjusted_cluster_contribution") is not None:
        return round4(row.get("stance_adjusted_cluster_contribution"))
    if row.get("raw_stance_adjusted_cluster_contribution") is not None:
        return round4(row.get("raw_stance_adjusted_cluster_contribution"))
    policy_score = round4(row.get("cluster_policy_score_v2", 0.5))
    relevance_score = round4(row.get("cluster_relevance_score_v2", 0.0))
    direction_multiplier = round4(row.get("direction_multiplier", 0.5))
    return round4(policy_score * relevance_score * direction_multiplier)


def build_event_theme_claim_rows(theme_summary: dict[str, Any]) -> list[dict[str, Any]]:
    claim_rows: list[dict[str, Any]] = []
    for theme in theme_summary.get("themes", []) or []:
        contributors = theme.get("all_event_contributors") or theme.get("top_event_contributors") or []
        for contributor in contributors:
            raw_contribution = compute_raw_event_theme_contribution(contributor)
            claim_rows.append(
                {
                    "event_cluster_id": contributor.get("event_cluster_id", ""),
                    "theme_id": contributor.get("theme_id") or theme.get("theme_id", ""),
                    "theme_name": contributor.get("theme_name") or theme.get("theme_name", ""),
                    "cluster_policy_score_v2": round4(contributor.get("cluster_policy_score_v2", 0.5)),
                    "cluster_relevance_score_v2": round4(contributor.get("cluster_relevance_score_v2", 0.0)),
                    "cluster_stance_score_v2": round4(contributor.get("cluster_stance_score_v2", 0.0)),
                    "cluster_stance_label": contributor.get("cluster_stance_label", ""),
                    "direction_multiplier": round4(contributor.get("direction_multiplier", 0.5)),
                    "pre_stance_cluster_contribution": round4(contributor.get("pre_stance_cluster_contribution", 0.0)),
                    "raw_stance_adjusted_cluster_contribution": raw_contribution,
                    "stance_adjusted_cluster_contribution": raw_contribution,
                    "primary_policy_id": contributor.get("primary_policy_id", ""),
                    "primary_policy_title": contributor.get("primary_policy_title", ""),
                    "selected_relevance_policy_id": contributor.get("selected_relevance_policy_id", ""),
                    "selected_stance_policy_id": contributor.get("selected_stance_policy_id", ""),
                    "member_policy_ids": contributor.get("member_policy_ids", []),
                    "cluster_size": contributor.get("cluster_size", 0),
                    "source": contributor.get("source", ""),
                    "published_date": contributor.get("published_date", ""),
                    "url": contributor.get("url", ""),
                    "cluster_reason": contributor.get("cluster_reason", []),
                    "metrics": contributor.get("metrics", {}),
                    "top_matched_evidence": contributor.get("top_matched_evidence", []),
                    "top_stance_evidence": contributor.get("top_stance_evidence", []),
                }
            )
    return sorted(claim_rows, key=lambda row: (row["event_cluster_id"], row["theme_id"]))


def assign_allocation_roles(allocated_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rules = load_allocation_rules()
    thresholds = rules.get("allocation_role_thresholds", {})
    co_primary_min_share = float(thresholds.get("co_primary_min_share", 0.3))
    secondary_min_share = float(thresholds.get("secondary_min_share", 0.15))
    sorted_rows = sorted(
        allocated_rows,
        key=lambda row: (
            -round4(row.get("allocated_cluster_contribution")),
            -round4(row.get("allocation_share")),
            -round4(row.get("cluster_relevance_score_v2")),
            row.get("theme_id", ""),
        ),
    )
    result: list[dict[str, Any]] = []
    for index, row in enumerate(sorted_rows, start=1):
        item = dict(row)
        share = round4(item.get("allocation_share"))
        if index == 1:
            role = "primary"
        elif share >= co_primary_min_share:
            role = "co_primary"
        elif share >= secondary_min_share:
            role = "secondary"
        else:
            role = "peripheral"
        item["allocation_rank"] = index
        item["allocation_role"] = role
        result.append(item)
    return result


def _apply_rounding_cap(rows: list[dict[str, Any]], budget: float) -> list[dict[str, Any]]:
    total = round4(sum(row.get("allocated_cluster_contribution", 0.0) for row in rows))
    excess = round4(total - budget)
    if excess <= 0:
        return rows
    positive_rows = [row for row in rows if round4(row.get("allocated_cluster_contribution")) > 0]
    if not positive_rows:
        return rows
    target = sorted(
        positive_rows,
        key=lambda row: (
            round4(row.get("allocated_cluster_contribution")),
            round4(row.get("allocation_share")),
            row.get("theme_id", ""),
        ),
    )[0]
    target["allocated_cluster_contribution"] = round4(round4(target.get("allocated_cluster_contribution")) - excess)
    target["theme_allocation_reduction_effect"] = round4(
        round4(target.get("raw_stance_adjusted_cluster_contribution")) - round4(target.get("allocated_cluster_contribution"))
    )
    return rows


def allocate_single_event_theme_contributions(event_claim_rows: list[dict[str, Any]]) -> dict[str, Any]:
    rules = load_allocation_rules()
    cap_ratio = float(rules.get("event_budget_cap_ratio", 1.0))
    min_threshold = float(rules.get("min_allocated_contribution_threshold", 0.0001))
    rows = sorted(event_claim_rows, key=lambda row: (row.get("theme_id", ""), row.get("event_cluster_id", "")))
    if not rows:
        return {
            "event_cluster_id": "",
            "cluster_policy_score_v2": 0.0,
            "matched_theme_count": 0,
            "raw_contribution_sum_v4": 0.0,
            "event_contribution_budget": 0.0,
            "allocation_budget_used": 0.0,
            "allocation_reduction_effect": 0.0,
            "allocation_capped": False,
            "primary_theme_id": "",
            "primary_theme_name": "",
            "allocated_themes": [],
        }

    event_cluster_id = str(rows[0].get("event_cluster_id", ""))
    cluster_policy_score = round4(max(round4(row.get("cluster_policy_score_v2", 0.5)) for row in rows))
    raw_sum = round4(sum(round4(row.get("raw_stance_adjusted_cluster_contribution")) for row in rows))
    budget = round4(cluster_policy_score * cap_ratio)
    allocation_capped = raw_sum > budget and raw_sum > 0
    allocation_budget_used = budget if allocation_capped else raw_sum

    allocated_rows: list[dict[str, Any]] = []
    for row in rows:
        raw = round4(row.get("raw_stance_adjusted_cluster_contribution"))
        share = round4(raw / raw_sum) if raw_sum > 0 else 0.0
        allocated = round4(allocation_budget_used * share) if allocation_capped else raw
        if allocated < min_threshold:
            allocated = 0.0
        allocated_rows.append(
            {
                **row,
                "allocation_share": share,
                "allocated_cluster_contribution": allocated,
                "theme_allocation_reduction_effect": round4(max(raw - allocated, 0.0)),
                "allocation_capped": allocation_capped,
            }
        )

    if allocation_capped:
        allocated_rows = _apply_rounding_cap(allocated_rows, allocation_budget_used)
    allocated_rows = assign_allocation_roles(allocated_rows)
    primary = allocated_rows[0] if allocated_rows else {}
    allocated_total = round4(sum(row.get("allocated_cluster_contribution", 0.0) for row in allocated_rows))
    return {
        "event_cluster_id": event_cluster_id,
        "cluster_policy_score_v2": cluster_policy_score,
        "matched_theme_count": len(rows),
        "raw_contribution_sum_v4": raw_sum,
        "event_contribution_budget": budget,
        "allocation_budget_used": allocated_total,
        "allocation_reduction_effect": round4(max(raw_sum - allocated_total, 0.0)),
        "allocation_capped": allocation_capped,
        "primary_theme_id": primary.get("theme_id", ""),
        "primary_theme_name": primary.get("theme_name", ""),
        "allocated_themes": allocated_rows,
    }


def allocate_event_theme_contributions(claim_rows: list[dict[str, Any]]) -> dict[str, Any]:
    rules = load_allocation_rules()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in claim_rows:
        grouped.setdefault(str(row.get("event_cluster_id", "")), []).append(row)
    events = [
        allocate_single_event_theme_contributions(grouped[event_id])
        for event_id in sorted(grouped)
        if event_id
    ]
    raw_total = round4(sum(event.get("raw_contribution_sum_v4", 0.0) for event in events))
    allocated_total = round4(sum(event.get("allocation_budget_used", 0.0) for event in events))
    event_count = len(events)
    claim_count = len(claim_rows)
    return {
        "scoring_version": rules.get("version", "event_theme_allocation_v2"),
        "allocation_method": rules.get("allocation_method", "proportional_budget_cap"),
        "event_budget_cap_ratio": float(rules.get("event_budget_cap_ratio", 1.0)),
        "min_allocated_contribution_threshold": float(rules.get("min_allocated_contribution_threshold", 0.0001)),
        "event_cluster_count": event_count,
        "event_theme_claim_count": claim_count,
        "multi_theme_event_count": sum(1 for event in events if int(event.get("matched_theme_count") or 0) > 1),
        "capped_event_count": sum(1 for event in events if event.get("allocation_capped") is True),
        "raw_contribution_total_v4": raw_total,
        "allocated_contribution_total_v5": allocated_total,
        "allocation_reduction_effect": round4(max(raw_total - allocated_total, 0.0)),
        "avg_matched_theme_count_per_event": round4(claim_count / event_count) if event_count else 0.0,
        "events": events,
    }


def sort_allocated_theme_rows(theme_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        theme_rows,
        key=lambda row: (
            -round4(row.get("theme_score_v5")),
            -int(row.get("primary_event_count") or 0),
            -int(row.get("matched_allocated_event_count") or 0),
            -round4(row.get("avg_allocation_share")),
            -round4(row.get("avg_cluster_relevance_score_v2")),
            -round4(row.get("avg_cluster_policy_score_v2")),
            row.get("theme_id", ""),
        ),
    )


def _allocated_rows_by_theme(event_theme_allocation_summary: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for event in event_theme_allocation_summary.get("events", []) or []:
        for row in event.get("allocated_themes", []) or []:
            result.setdefault(row.get("theme_id", ""), []).append(row)
    return result


def build_allocated_theme_summary(
    previous_theme_summary: dict[str, Any],
    event_theme_allocation_summary: dict[str, Any],
) -> dict[str, Any]:
    rows_by_theme = _allocated_rows_by_theme(event_theme_allocation_summary)
    theme_rows: list[dict[str, Any]] = []
    for previous in previous_theme_summary.get("themes", []) or []:
        theme_id = previous.get("theme_id", "")
        allocated_rows = sorted(
            rows_by_theme.get(theme_id, []),
            key=lambda row: (
                -round4(row.get("allocated_cluster_contribution")),
                -round4(row.get("allocation_share")),
                -round4(row.get("cluster_relevance_score_v2")),
                row.get("event_cluster_id", ""),
            ),
        )
        positive_rows = [row for row in allocated_rows if round4(row.get("allocated_cluster_contribution")) > 0]
        theme_score_v5 = round4(sum(row.get("allocated_cluster_contribution", 0.0) for row in allocated_rows))
        theme_score_v4 = round4(
            previous.get("theme_score_v4_stance_adjusted", previous.get("theme_score_v4", 0.0))
        )
        count = len(positive_rows)
        role_counts = {"primary": 0, "co_primary": 0, "secondary": 0, "peripheral": 0}
        for row in positive_rows:
            role = row.get("allocation_role", "peripheral")
            if role in role_counts:
                role_counts[role] += 1
        avg_allocation_share = round4(sum(row.get("allocation_share", 0.0) for row in positive_rows) / count) if count else 0.0
        avg_relevance = round4(sum(row.get("cluster_relevance_score_v2", 0.0) for row in positive_rows) / count) if count else 0.0
        avg_policy = round4(sum(row.get("cluster_policy_score_v2", 0.0) for row in positive_rows) / count) if count else 0.0
        avg_stance = round4(sum(row.get("cluster_stance_score_v2", 0.0) for row in positive_rows) / count) if count else 0.0
        theme_rows.append(
            {
                **previous,
                "theme_score_v5": theme_score_v5,
                "theme_score_v4_stance_adjusted": theme_score_v4,
                "theme_score_v4": theme_score_v4,
                "allocation_adjustment_effect": round4(max(theme_score_v4 - theme_score_v5, 0.0)),
                "matched_allocated_event_count": count,
                "primary_event_count": role_counts["primary"],
                "co_primary_event_count": role_counts["co_primary"],
                "secondary_event_count": role_counts["secondary"],
                "peripheral_event_count": role_counts["peripheral"],
                "avg_allocation_share": avg_allocation_share,
                "avg_cluster_relevance_score_v2": avg_relevance,
                "avg_cluster_policy_score_v2": avg_policy,
                "avg_cluster_stance_score_v2": avg_stance,
                "top_event_contributors": allocated_rows[:3],
                "all_event_contributors": allocated_rows,
            }
        )

    return {
        "scoring_version": "theme_score_v5_allocated",
        "base_relevance_version": previous_theme_summary.get("base_relevance_version", "theme_relevance_v2"),
        "event_clustering_version": previous_theme_summary.get("event_clustering_version", "policy_event_clustering_v2"),
        "policy_stance_version": previous_theme_summary.get("policy_stance_version", "policy_theme_stance_v2"),
        "event_theme_allocation_version": event_theme_allocation_summary.get("scoring_version", "event_theme_allocation_v2"),
        "min_relevance_threshold": previous_theme_summary.get("min_relevance_threshold", 0.25),
        "policy_stance_summary": previous_theme_summary.get("policy_stance_summary", {}),
        "event_theme_allocation_summary": event_theme_allocation_summary,
        "themes": sort_allocated_theme_rows(theme_rows),
    }
