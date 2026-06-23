from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "config" / "mainline_lifecycle_rules.json"

LIFECYCLE_STATES = (
    "accelerating",
    "sustained",
    "emerging",
    "single_event_emerging",
    "cooling",
    "legacy_tail",
    "undated_unknown",
    "dormant",
)

LIFECYCLE_PRIORITY = {
    "accelerating": 1,
    "sustained": 2,
    "emerging": 3,
    "single_event_emerging": 4,
    "cooling": 5,
    "legacy_tail": 6,
    "undated_unknown": 7,
    "dormant": 8,
}

LIFECYCLE_LABELS = {
    "accelerating": "升温加速",
    "sustained": "持续有效",
    "emerging": "新出现",
    "single_event_emerging": "单事件新出现",
    "cooling": "降温",
    "legacy_tail": "旧政策尾部",
    "undated_unknown": "日期不足",
    "dormant": "休眠",
}

SOURCE_ALIASES = (
    ("state_council", ("国务院办公厅", "中共中央国务院", "国务院")),
    ("ndrc", ("国家发展改革委", "国家发改委", "发改委")),
    ("csrc", ("中国证监会", "证监会")),
    ("nea", ("国家能源局",)),
    ("miit", ("工业和信息化部", "工信部")),
    ("mof", ("财政部",)),
)


def lifecycle_state_label(state: Any) -> str:
    text = str(state or "")
    return LIFECYCLE_LABELS.get(text, text)


def round4(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return round(number, 4)


def load_lifecycle_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def get_event_activity_date(event_row: dict[str, Any]) -> date | None:
    for field in (
        "publish_date_max",
        "event_publish_date_max",
        "publish_date",
        "published_date",
        "primary_policy_publish_date",
        "publish_date_min",
        "event_publish_date_min",
    ):
        parsed = parse_date(event_row.get(field))
        if parsed is not None:
            return parsed
    return None


def compute_event_age_days(event_row: dict[str, Any], as_of_date: date) -> int | None:
    event_date = get_event_activity_date(event_row)
    if event_date is None:
        return None
    if event_date > as_of_date:
        return 0
    return (as_of_date - event_date).days


def bucket_event_by_age(age_days: int | None) -> str:
    if age_days is None:
        return "undated"
    if age_days <= 7:
        return "recent_7d"
    if age_days <= 30:
        return "recent_30d"
    if age_days <= 60:
        return "prior_31_60d"
    if age_days <= 90:
        return "prior_61_90d"
    return "older"


def _normalize_source_org(value: Any) -> str:
    raw = " ".join(str(value or "").lower().split())
    if not raw:
        return ""
    for normalized, aliases in SOURCE_ALIASES:
        if any(alias.lower() in raw for alias in aliases):
            return normalized
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "_", raw).strip("_")


def build_theme_lifecycle_inputs(theme_summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for theme in theme_summary.get("themes", []) or []:
        item = dict(theme)
        item["lifecycle_events"] = list(theme.get("all_event_contributors") or theme.get("top_event_contributors") or [])
        rows.append(item)
    return rows


def _event_detail(event_row: dict[str, Any], as_of_date: date) -> dict[str, Any]:
    event_date = get_event_activity_date(event_row)
    age_days = compute_event_age_days(event_row, as_of_date)
    detail = {
        "event_cluster_id": event_row.get("event_cluster_id", ""),
        "theme_id": event_row.get("theme_id", ""),
        "event_activity_date": event_date.isoformat() if event_date else "",
        "age_days": age_days,
        "age_bucket": bucket_event_by_age(age_days),
        "source_org_norm": event_row.get("source_org_norm") or _normalize_source_org(event_row.get("source")),
        "allocation_role": event_row.get("allocation_role", ""),
        "allocated_cluster_contribution": round4(event_row.get("allocated_cluster_contribution")),
        "allocation_share": round4(event_row.get("allocation_share")),
        "cluster_policy_score_v2": round4(event_row.get("cluster_policy_score_v2")),
        "cluster_relevance_score_v2": round4(event_row.get("cluster_relevance_score_v2")),
        "cluster_stance_label": event_row.get("cluster_stance_label", ""),
        "direction_multiplier": round4(event_row.get("direction_multiplier")),
        "primary_policy_id": event_row.get("primary_policy_id", ""),
        "primary_policy_title": event_row.get("primary_policy_title", ""),
        **event_row,
    }
    if event_date and event_date > as_of_date:
        detail["date_warning"] = "future_event_date_clamped_to_zero"
    return detail


def _sum(details: list[dict[str, Any]], predicate) -> float:
    return round4(sum(row.get("allocated_cluster_contribution", 0.0) for row in details if predicate(row)))


def _count(details: list[dict[str, Any]], predicate) -> int:
    return sum(1 for row in details if predicate(row))


def compute_theme_lifecycle_v2(theme_row: dict[str, Any], as_of_date: date, rules: dict[str, Any] | None = None) -> dict[str, Any]:
    rules = rules or load_lifecycle_rules()
    threshold = float(rules.get("active_window_score_threshold", 0.05))
    source_events = (
        theme_row.get("lifecycle_events")
        or theme_row.get("all_event_contributors")
        or theme_row.get("top_event_contributors")
        or []
    )
    details = [_event_detail(row, as_of_date) for row in source_events]
    score_7d = _sum(details, lambda row: row.get("age_days") is not None and row["age_days"] <= 7)
    score_30d = _sum(details, lambda row: row.get("age_days") is not None and row["age_days"] <= 30)
    score_31_60d = _sum(details, lambda row: row.get("age_days") is not None and 31 <= row["age_days"] <= 60)
    score_61_90d = _sum(details, lambda row: row.get("age_days") is not None and 61 <= row["age_days"] <= 90)
    score_90d = round4(score_30d + score_31_60d + score_61_90d)
    older_score = _sum(details, lambda row: row.get("age_days") is not None and row["age_days"] > 90)
    undated_score = _sum(details, lambda row: row.get("age_days") is None)
    event_count_total = len(details)
    event_count_7d = _count(details, lambda row: row.get("age_days") is not None and row["age_days"] <= 7)
    event_count_30d = _count(details, lambda row: row.get("age_days") is not None and row["age_days"] <= 30)
    event_count_90d = _count(details, lambda row: row.get("age_days") is not None and row["age_days"] <= 90)
    older_event_count = _count(details, lambda row: row.get("age_days") is not None and row["age_days"] > 90)
    undated_event_count = _count(details, lambda row: row.get("age_days") is None)
    known_date_event_count = event_count_total - undated_event_count
    source_org_count_90d = len(
        {
            row.get("source_org_norm", "")
            for row in details
            if row.get("age_days") is not None and row["age_days"] <= 90 and row.get("source_org_norm")
        }
    )
    role_counts = {
        "primary": _count(details, lambda row: row.get("age_days") is not None and row["age_days"] <= 90 and row.get("allocation_role") == "primary"),
        "co_primary": _count(details, lambda row: row.get("age_days") is not None and row["age_days"] <= 90 and row.get("allocation_role") == "co_primary"),
        "secondary": _count(details, lambda row: row.get("age_days") is not None and row["age_days"] <= 90 and row.get("allocation_role") == "secondary"),
        "peripheral": _count(details, lambda row: row.get("age_days") is not None and row["age_days"] <= 90 and row.get("allocation_role") == "peripheral"),
    }
    active_window_count = int(score_30d >= threshold) + int(score_31_60d >= threshold) + int(score_61_90d >= threshold)
    persistence_score = round4(active_window_count / 3)
    acceleration_delta = round4(score_30d - score_31_60d)
    denominator = max(score_31_60d, threshold)
    acceleration_ratio = round4(max(-1.0, min(5.0, acceleration_delta / denominator)))
    targets = rules.get("breadth_targets", {})
    event_target = float(targets.get("event_count_90d_target", 3))
    source_target = float(targets.get("source_org_count_90d_target", 3))
    event_breadth_score = min(event_count_90d / event_target, 1.0) if event_target > 0 else 0.0
    source_breadth_score = min(source_org_count_90d / source_target, 1.0) if source_target > 0 else 0.0
    breadth_score = round4(0.6 * event_breadth_score + 0.4 * source_breadth_score)
    metrics = {
        "theme_id": theme_row.get("theme_id", ""),
        "theme_name": theme_row.get("theme_name", ""),
        "theme_score_v5": round4(theme_row.get("theme_score_v5")),
        "score_7d": score_7d,
        "score_30d": score_30d,
        "score_31_60d": score_31_60d,
        "score_61_90d": score_61_90d,
        "score_90d": score_90d,
        "older_score": older_score,
        "undated_score": undated_score,
        "event_count_total": event_count_total,
        "event_count_7d": event_count_7d,
        "event_count_30d": event_count_30d,
        "event_count_90d": event_count_90d,
        "older_event_count": older_event_count,
        "undated_event_count": undated_event_count,
        "known_date_event_count": known_date_event_count,
        "source_org_count_90d": source_org_count_90d,
        "primary_event_count_90d": role_counts["primary"],
        "co_primary_event_count_90d": role_counts["co_primary"],
        "secondary_event_count_90d": role_counts["secondary"],
        "peripheral_event_count_90d": role_counts["peripheral"],
        "active_window_count": active_window_count,
        "persistence_score": persistence_score,
        "acceleration_delta_30d": acceleration_delta,
        "acceleration_ratio_30d": acceleration_ratio,
        "breadth_score": breadth_score,
        "lifecycle_event_details": sorted(
            details,
            key=lambda row: (-round4(row.get("allocated_cluster_contribution")), -round4(row.get("allocation_share")), row.get("event_cluster_id", "")),
        ),
    }
    lifecycle_state, reasons = classify_lifecycle_state(metrics, rules)
    state_multiplier = round4((rules.get("state_multipliers", {}) or {}).get(lifecycle_state, 0.0))
    lifecycle_quality_multiplier = compute_lifecycle_quality_multiplier(lifecycle_state, breadth_score, rules)
    mainline_score_v6 = round4(metrics["theme_score_v5"] * lifecycle_quality_multiplier)
    return {
        **metrics,
        "lifecycle_state": lifecycle_state,
        "lifecycle_state_label": lifecycle_state_label(lifecycle_state),
        "state_multiplier": state_multiplier,
        "lifecycle_quality_multiplier": lifecycle_quality_multiplier,
        "mainline_score_v6": min(mainline_score_v6, metrics["theme_score_v5"]),
        "lifecycle_reasons": reasons,
    }


def classify_lifecycle_state(metrics: dict[str, Any], rules: dict[str, Any]) -> tuple[str, list[str]]:
    threshold = float(rules.get("active_window_score_threshold", 0.05))
    reasons: list[str] = []
    if round4(metrics.get("theme_score_v5")) <= 0:
        return "dormant", ["theme_score_v5_zero"]
    if int(metrics.get("event_count_total") or 0) > 0 and int(metrics.get("known_date_event_count") or 0) == 0:
        return "undated_unknown", ["no_known_event_dates"]
    if round4(metrics.get("score_30d")) < threshold and round4(metrics.get("score_90d")) < threshold and round4(metrics.get("older_score")) > 0:
        return "legacy_tail", ["only_older_score_above_zero"]
    if round4(metrics.get("score_31_60d")) >= threshold and round4(metrics.get("acceleration_ratio_30d")) <= float(rules.get("cooling_ratio_threshold", -0.4)):
        return "cooling", ["recent_30d_weaker_than_prior_30d"]
    if (
        round4(metrics.get("score_30d")) >= threshold
        and int(metrics.get("event_count_30d") or 0) >= int(rules.get("min_accelerating_event_count_30d", 2))
        and round4(metrics.get("acceleration_ratio_30d")) >= float(rules.get("acceleration_ratio_threshold", 0.25))
    ):
        return "accelerating", ["recent_30d_score_above_threshold", "event_count_30d_meets_accelerating_min", "acceleration_ratio_above_threshold"]
    if (
        int(metrics.get("active_window_count") or 0) >= int(rules.get("min_sustained_active_window_count", 2))
        and int(metrics.get("event_count_90d") or 0) >= int(rules.get("min_sustained_event_count_90d", 2))
    ):
        return "sustained", ["active_window_count_above_sustained_min", "event_count_90d_above_sustained_min"]
    if round4(metrics.get("score_30d")) >= threshold and int(metrics.get("event_count_90d") or 0) == 1:
        return "single_event_emerging", ["recent_30d_score_above_threshold", "event_count_90d_single"]
    if round4(metrics.get("score_30d")) >= threshold and int(metrics.get("event_count_30d") or 0) >= 1:
        return "emerging", ["recent_30d_score_above_threshold", "event_count_30d_positive"]
    return "legacy_tail", ["fallback_legacy_tail"]


def compute_lifecycle_quality_multiplier(lifecycle_state: str, breadth_score: float, rules: dict[str, Any]) -> float:
    if lifecycle_state == "dormant":
        return 0.0
    state_multiplier = round4((rules.get("state_multipliers", {}) or {}).get(lifecycle_state, 0.0))
    weights = rules.get("quality_multiplier_weights", {})
    state_weight = float(weights.get("state_multiplier", 0.75))
    breadth_weight = float(weights.get("breadth_score", 0.25))
    return round4(max(0.0, min(1.0, state_weight * state_multiplier + breadth_weight * round4(breadth_score))))


def build_mainline_lifecycle_summary(theme_summary: dict[str, Any], as_of_date: date, rules: dict[str, Any] | None = None) -> dict[str, Any]:
    rules = rules or load_lifecycle_rules()
    rows = [compute_theme_lifecycle_v2(row, as_of_date, rules) for row in build_theme_lifecycle_inputs(theme_summary)]
    counts = {state: 0 for state in LIFECYCLE_STATES}
    for row in rows:
        counts[row["lifecycle_state"]] += 1
    return {
        "scoring_version": rules.get("version", "mainline_lifecycle_v2"),
        "as_of_date": as_of_date.isoformat(),
        "event_date_policy": "publish_date_max_then_min_then_primary",
        "active_window_score_threshold": float(rules.get("active_window_score_threshold", 0.05)),
        "theme_count": len(rows),
        "accelerating_count": counts["accelerating"],
        "sustained_count": counts["sustained"],
        "emerging_count": counts["emerging"],
        "single_event_emerging_count": counts["single_event_emerging"],
        "cooling_count": counts["cooling"],
        "legacy_tail_count": counts["legacy_tail"],
        "undated_unknown_count": counts["undated_unknown"],
        "dormant_count": counts["dormant"],
        "state_labels": LIFECYCLE_LABELS,
        "themes": rows,
    }


def sort_lifecycle_adjusted_theme_rows(theme_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        theme_rows,
        key=lambda row: (
            -round4(row.get("mainline_score_v6")),
            LIFECYCLE_PRIORITY.get(row.get("lifecycle_state"), 99),
            -round4(row.get("theme_score_v5")),
            -int(row.get("primary_event_count") or row.get("primary_event_count_90d") or 0),
            -int(row.get("matched_allocated_event_count") or 0),
            -round4(row.get("breadth_score")),
            -round4(row.get("avg_allocation_share")),
            row.get("theme_id", ""),
        ),
    )


def build_lifecycle_adjusted_theme_summary(theme_summary: dict[str, Any], lifecycle_summary: dict[str, Any]) -> dict[str, Any]:
    lifecycle_by_theme = {row.get("theme_id", ""): row for row in lifecycle_summary.get("themes", []) or []}
    rows: list[dict[str, Any]] = []
    for theme in theme_summary.get("themes", []) or []:
        lifecycle = lifecycle_by_theme.get(theme.get("theme_id", ""), {})
        details = lifecycle.get("lifecycle_event_details", [])
        rows.append(
            {
                **theme,
                **{key: value for key, value in lifecycle.items() if key != "lifecycle_event_details"},
                "top_event_contributors": details[:3],
                "all_event_contributors": details,
            }
        )
    return {
        "scoring_version": "mainline_score_v6_lifecycle_adjusted",
        "base_relevance_version": theme_summary.get("base_relevance_version", "theme_relevance_v2"),
        "event_clustering_version": theme_summary.get("event_clustering_version", "policy_event_clustering_v2"),
        "policy_stance_version": theme_summary.get("policy_stance_version", "policy_theme_stance_v2"),
        "event_theme_allocation_version": theme_summary.get("event_theme_allocation_version", "event_theme_allocation_v2"),
        "mainline_lifecycle_version": lifecycle_summary.get("scoring_version", "mainline_lifecycle_v2"),
        "min_relevance_threshold": theme_summary.get("min_relevance_threshold", 0.25),
        "policy_stance_summary": theme_summary.get("policy_stance_summary", {}),
        "event_theme_allocation_summary": theme_summary.get("event_theme_allocation_summary", {}),
        "mainline_lifecycle_summary": lifecycle_summary,
        "themes": sort_lifecycle_adjusted_theme_rows(rows),
    }
