from __future__ import annotations

import math
from typing import Any

try:
    from mainline_lifecycle import LIFECYCLE_PRIORITY, lifecycle_state_label
except ModuleNotFoundError:
    from scripts.mainline_lifecycle import LIFECYCLE_PRIORITY, lifecycle_state_label
try:
    from mainline_cycle_stage import (
        SCORING_VERSION as CYCLE_STAGE_VERSION,
        build_cycle_stage_summary,
        classify_mainline_cycle_stage,
    )
except ModuleNotFoundError:
    from scripts.mainline_cycle_stage import (
        SCORING_VERSION as CYCLE_STAGE_VERSION,
        build_cycle_stage_summary,
        classify_mainline_cycle_stage,
    )


SCORING_VERSION = "canonical_mainline_output_v2"
DEFAULT_SCORE_FIELD = "mainline_score_v6"
SOURCE_SCORING_VERSION = "mainline_score_v6_lifecycle_adjusted"
LEGACY_STATUS = "market_context_not_default_mainline_rank"


def round4(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return round(number, 4)


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _theme_name(row: dict[str, Any]) -> str:
    return str(row.get("theme_name") or row.get("theme") or "")


def _top_event_ids(row: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for event in row.get("top_event_contributors") or []:
        event_id = str(event.get("event_cluster_id") or "")
        if event_id:
            ids.append(event_id)
    return ids


def _mainline_row(row: dict[str, Any]) -> dict[str, Any]:
    item = {
        "theme_id": row.get("theme_id", ""),
        "theme_name": _theme_name(row),
        "mainline_score_v6": round4(row.get("mainline_score_v6")),
        "theme_score_v5": round4(row.get("theme_score_v5")),
        "theme_score_v4_stance_adjusted": round4(row.get("theme_score_v4_stance_adjusted") or row.get("theme_score_v4")),
        "theme_score_v4": round4(row.get("theme_score_v4")),
        "theme_score_v3_dedup": round4(row.get("theme_score_v3_dedup") or row.get("theme_score_v3")),
        "theme_score_v3": round4(row.get("theme_score_v3")),
        "theme_score_v2_raw": round4(row.get("theme_score_v2_raw")),
        "lifecycle_state": row.get("lifecycle_state", ""),
        "lifecycle_state_label": row.get("lifecycle_state_label")
        or lifecycle_state_label(row.get("lifecycle_state")),
        "lifecycle_quality_multiplier": round4(row.get("lifecycle_quality_multiplier")),
        "state_multiplier": round4(row.get("state_multiplier")),
        "breadth_score": round4(row.get("breadth_score")),
        "score_7d": round4(row.get("score_7d")),
        "score_30d": round4(row.get("score_30d")),
        "score_31_60d": round4(row.get("score_31_60d")),
        "score_61_90d": round4(row.get("score_61_90d")),
        "score_90d": round4(row.get("score_90d")),
        "event_count_30d": _int(row.get("event_count_30d")),
        "event_count_90d": _int(row.get("event_count_90d")),
        "source_org_count_90d": _int(row.get("source_org_count_90d")),
        "active_window_count": _int(row.get("active_window_count")),
        "persistence_score": round4(row.get("persistence_score")),
        "acceleration_delta_30d": round4(row.get("acceleration_delta_30d")),
        "acceleration_ratio_30d": round4(row.get("acceleration_ratio_30d")),
        "matched_allocated_event_count": _int(row.get("matched_allocated_event_count")),
        "matched_event_cluster_count": _int(row.get("matched_event_cluster_count")),
        "matched_policy_count_raw": _int(row.get("matched_policy_count_raw")),
        "primary_event_count": _int(row.get("primary_event_count")),
        "co_primary_event_count": _int(row.get("co_primary_event_count")),
        "secondary_event_count": _int(row.get("secondary_event_count")),
        "peripheral_event_count": _int(row.get("peripheral_event_count")),
        "avg_allocation_share": round4(row.get("avg_allocation_share")),
        "avg_cluster_relevance_score_v2": round4(row.get("avg_cluster_relevance_score_v2")),
        "avg_cluster_policy_score_v2": round4(row.get("avg_cluster_policy_score_v2")),
        "avg_cluster_stance_score_v2": round4(row.get("avg_cluster_stance_score_v2")),
        "deduplication_effect": round4(row.get("deduplication_effect")),
        "stance_adjustment_effect": round4(row.get("stance_adjustment_effect")),
        "allocation_adjustment_effect": round4(row.get("allocation_adjustment_effect")),
        "lifecycle_reasons": list(row.get("lifecycle_reasons") or []),
        "top_event_ids": _top_event_ids(row),
        "top_event_contributors": list(row.get("top_event_contributors") or []),
        "_cycle_event_contributors": list(
            row.get("all_event_contributors")
            or row.get("lifecycle_event_details")
            or row.get("top_event_contributors")
            or []
        ),
    }
    item.update(classify_mainline_cycle_stage(item))
    item.pop("_cycle_event_contributors", None)
    return item


def sort_mainline_ranking(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            -round4(row.get(DEFAULT_SCORE_FIELD)),
            LIFECYCLE_PRIORITY.get(row.get("lifecycle_state"), 99),
            -round4(row.get("theme_score_v5")),
            -_int(row.get("primary_event_count")),
            -_int(row.get("matched_allocated_event_count")),
            -round4(row.get("breadth_score")),
            -round4(row.get("avg_allocation_share")),
            row.get("theme_id", ""),
        ),
    )


def build_mainline_ranking(theme_summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [_mainline_row(row) for row in theme_summary.get("themes", []) or []]
    ranked = []
    for rank, row in enumerate(sort_mainline_ranking(rows), start=1):
        ranked.append({"rank": rank, **row})
    return ranked


def build_canonical_mainline_summary(theme_summary: dict[str, Any]) -> dict[str, Any]:
    ranking = build_mainline_ranking(theme_summary)
    cycle_stage_summary = build_cycle_stage_summary(ranking)
    state_counts: dict[str, int] = {
        "accelerating": 0,
        "sustained": 0,
        "emerging": 0,
        "single_event_emerging": 0,
        "cooling": 0,
        "legacy_tail": 0,
        "undated_unknown": 0,
        "dormant": 0,
    }
    for row in ranking:
        state = str(row.get("lifecycle_state") or "")
        if state in state_counts:
            state_counts[state] += 1
    top = ranking[0] if ranking else {}
    top_mainline = (
        {
            "rank": top.get("rank"),
            "theme_id": top.get("theme_id", ""),
            "theme_name": top.get("theme_name", ""),
            "mainline_score_v6": top.get("mainline_score_v6"),
            "theme_score_v5": top.get("theme_score_v5"),
            "lifecycle_state": top.get("lifecycle_state", ""),
            "lifecycle_state_label": top.get("lifecycle_state_label", ""),
            "lifecycle_quality_multiplier": top.get("lifecycle_quality_multiplier"),
            "cycle_stage": top.get("cycle_stage", ""),
            "cycle_stage_label": top.get("cycle_stage_label", ""),
            "cycle_time_window": top.get("cycle_time_window", ""),
            "cycle_reference_window": top.get("cycle_reference_window", ""),
            "cycle_review_window_days": top.get("cycle_review_window_days"),
            "cycle_elapsed_days": top.get("cycle_elapsed_days"),
            "cycle_recent_reinforcement_days": top.get("cycle_recent_reinforcement_days"),
            "cycle_review_remaining_days": top.get("cycle_review_remaining_days"),
            "cycle_timing_label": top.get("cycle_timing_label", ""),
            "score_30d": top.get("score_30d"),
            "score_90d": top.get("score_90d"),
            "matched_allocated_event_count": top.get("matched_allocated_event_count"),
            "top_event_ids": list(top.get("top_event_ids") or []),
        }
        if top
        else {}
    )
    return {
        "scoring_version": SCORING_VERSION,
        "default_score_field": DEFAULT_SCORE_FIELD,
        "source_summary": "theme_summary",
        "source_scoring_version": theme_summary.get("scoring_version", SOURCE_SCORING_VERSION),
        "theme_count": len(ranking),
        "top_mainline": top_mainline,
        "state_counts": state_counts,
        "mainline_cycle_stage_version": CYCLE_STAGE_VERSION,
        "cycle_stage_summary": cycle_stage_summary,
    }


def build_legacy_theme_ranking(theme_ranking: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for rank, row in enumerate(theme_ranking or [], start=1):
        item = dict(row)
        item["rank"] = rank
        item["legacy_status"] = LEGACY_STATUS
        rows.append(item)
    return rows


def assert_canonical_mainline_contract(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    theme_summary = report.get("theme_summary") or {}
    summary_themes = theme_summary.get("themes") or []
    mainline_ranking = report.get("mainline_ranking") or []
    canonical_summary = report.get("canonical_mainline_summary") or {}

    if canonical_summary.get("scoring_version") != SCORING_VERSION:
        errors.append("canonical_mainline_summary.scoring_version_mismatch")
    if canonical_summary.get("default_score_field") != DEFAULT_SCORE_FIELD:
        errors.append("canonical_mainline_summary.default_score_field_mismatch")
    if summary_themes and mainline_ranking:
        if summary_themes[0].get("theme_id") != mainline_ranking[0].get("theme_id"):
            errors.append("mainline_ranking_top_not_equal_theme_summary_top")
    if mainline_ranking:
        sorted_ids = [row.get("theme_id") for row in sort_mainline_ranking(mainline_ranking)]
        ranked_ids = [row.get("theme_id") for row in mainline_ranking]
        if sorted_ids != ranked_ids:
            errors.append("mainline_ranking_not_sorted_by_canonical_score")
        top_summary = canonical_summary.get("top_mainline") or {}
        if top_summary.get("theme_id") != mainline_ranking[0].get("theme_id"):
            errors.append("canonical_summary_top_not_equal_mainline_ranking_top")
    if report.get("theme_ranking") and not report.get("legacy_theme_ranking"):
        errors.append("legacy_theme_ranking_missing")
    return errors
