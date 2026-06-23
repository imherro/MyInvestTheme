from __future__ import annotations

import math
from typing import Any

try:
    from mainline_lifecycle import lifecycle_state_label
except ModuleNotFoundError:
    from scripts.mainline_lifecycle import lifecycle_state_label


SCORING_VERSION = "mainline_cycle_stage_v2"
REVIEW_WINDOW_DAYS = 90

CYCLE_STAGE_META = {
    "main_rise_diffusion": {
        "label": "主升扩散期",
        "priority": 1,
        "time_window": "约30-90天",
        "advice": "政策仍有效，市场热度已经确认，更适合作为核心候选；继续观察拥挤和退潮信号。",
    },
    "launch_confirmation": {
        "label": "启动确认期",
        "priority": 2,
        "time_window": "约0-60天",
        "advice": "政策和市场开始同向，适合重点跟踪；等待市场证据继续扩散。",
    },
    "policy_incubation": {
        "label": "政策孕育期",
        "priority": 3,
        "time_window": "约0-30天",
        "advice": "政策信号已出现但市场确认不足，适合列入候选观察，不宜只因政策单独重仓。",
    },
    "crowded_late": {
        "label": "高位拥挤期",
        "priority": 4,
        "time_window": "约60-180天",
        "advice": "市场热度很高但政策边际不再加速，偏向持有观察和控制追高。",
    },
    "cooling_decline": {
        "label": "降温退潮期",
        "priority": 5,
        "time_window": "约60天以后",
        "advice": "政策或市场热度走弱，不适合作为新开核心依据。",
    },
    "legacy_residual": {
        "label": "旧线残余期",
        "priority": 6,
        "time_window": "约90/180天以后",
        "advice": "主要来自旧政策尾部影响，只适合做背景观察。",
    },
    "not_active": {
        "label": "未形成主线",
        "priority": 7,
        "time_window": "无有效周期",
        "advice": "当前证据不足，不能按主线处理。",
    },
    "unknown": {
        "label": "状态不足",
        "priority": 8,
        "time_window": "需继续观察",
        "advice": "关键证据不足，暂不做周期判断。",
    },
}

CYCLE_STAGE_ORDER = tuple(CYCLE_STAGE_META.keys())


def round4(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return round(number, 4)


def _float_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _theme_keys(row: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for key in (row.get("theme_id"), row.get("theme_name"), row.get("theme")):
        text = str(key or "").strip()
        if text and text not in keys:
            keys.append(text)
    return keys


def _market_field(market_row: dict[str, Any] | None, field: str) -> float | None:
    if not market_row:
        return None
    return _float_or_none(market_row.get(field))


def _event_age_details(row: dict[str, Any]) -> list[dict[str, Any]]:
    source = (
        row.get("_cycle_event_contributors")
        or row.get("all_event_contributors")
        or row.get("lifecycle_event_details")
        or row.get("cycle_event_age_details")
        or row.get("top_event_contributors")
        or []
    )
    details = []
    for item in source:
        if not isinstance(item, dict):
            continue
        age = _float_or_none(item.get("age_days"))
        if age is None or age < 0:
            continue
        details.append(
            {
                "event_cluster_id": item.get("event_cluster_id", ""),
                "event_activity_date": item.get("event_activity_date", ""),
                "age_days": int(round(age)),
                "age_bucket": item.get("age_bucket", ""),
                "allocation_role": item.get("allocation_role", ""),
                "allocated_cluster_contribution": round4(item.get("allocated_cluster_contribution")),
            }
        )
    return sorted(details, key=lambda item: (item["age_days"], -round4(item.get("allocated_cluster_contribution"))))


def _cycle_timing_fields(row: dict[str, Any], reference_window: str) -> dict[str, Any]:
    details = _event_age_details(row)
    ages = [item["age_days"] for item in details]
    effective_ages = [age for age in ages if age <= REVIEW_WINDOW_DAYS]
    elapsed_days = max(effective_ages) if effective_ages else (max(ages) if ages else None)
    recent_days = min(ages) if ages else None
    remaining_days = None
    if elapsed_days is not None:
        remaining_days = max(REVIEW_WINDOW_DAYS - elapsed_days, 0)

    parts = []
    if elapsed_days is not None:
        prefix = "有效政策" if elapsed_days <= REVIEW_WINDOW_DAYS else "旧政策"
        parts.append(f"{prefix}第{elapsed_days}天")
        if remaining_days and remaining_days > 0:
            parts.append(f"距{REVIEW_WINDOW_DAYS}天复核约{remaining_days}天")
        else:
            parts.append(f"已到{REVIEW_WINDOW_DAYS}天复核线")
    if recent_days is not None:
        parts.append(f"最近强化第{recent_days}天")

    timing_label = "｜".join(parts) if parts else reference_window
    return {
        "cycle_reference_window": reference_window,
        "cycle_review_window_days": REVIEW_WINDOW_DAYS,
        "cycle_elapsed_days": elapsed_days,
        "cycle_recent_reinforcement_days": recent_days,
        "cycle_review_remaining_days": remaining_days,
        "cycle_timing_label": timing_label,
        "cycle_event_age_details": details,
    }


def _stage_fields(
    stage: str,
    reasons: list[str],
    market_row: dict[str, Any] | None,
    mainline_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = CYCLE_STAGE_META.get(stage, CYCLE_STAGE_META["unknown"])
    market_score = _market_field(market_row, "market_score")
    evidence_score = _market_field(market_row, "evidence_score")
    timing = _cycle_timing_fields(mainline_row or {}, meta["time_window"])
    return {
        "cycle_stage": stage,
        "cycle_stage_label": meta["label"],
        "cycle_stage_priority": meta["priority"],
        "cycle_time_window": timing["cycle_timing_label"],
        "cycle_stage_advice": meta["advice"],
        "cycle_stage_reason": "；".join(reasons),
        "cycle_stage_reasons": reasons,
        "cycle_stage_scoring_version": SCORING_VERSION,
        "cycle_market_score": round(market_score, 2) if market_score is not None else None,
        "cycle_evidence_score": round(evidence_score, 2) if evidence_score is not None else None,
        **timing,
    }


def classify_mainline_cycle_stage(
    mainline_row: dict[str, Any], market_row: dict[str, Any] | None = None
) -> dict[str, Any]:
    lifecycle = str(mainline_row.get("lifecycle_state") or "")
    mainline_score = round4(mainline_row.get("mainline_score_v6"))
    score_30d = round4(mainline_row.get("score_30d"))
    score_31_60d = round4(mainline_row.get("score_31_60d"))
    score_90d = round4(mainline_row.get("score_90d"))
    acceleration_delta = round4(mainline_row.get("acceleration_delta_30d"))
    event_count_30d = _int(mainline_row.get("event_count_30d"))
    event_count_90d = _int(mainline_row.get("event_count_90d"))
    active_window_count = _int(mainline_row.get("active_window_count"))
    source_org_count_90d = _int(mainline_row.get("source_org_count_90d"))

    market_score = _market_field(market_row, "market_score")
    evidence_score = _market_field(market_row, "evidence_score")
    ths_score = _market_field(market_row, "ths_score")
    etf_score = _market_field(market_row, "etf_score")
    market_proxy = market_score if market_score is not None else evidence_score

    market_confirmed = market_proxy is not None and market_proxy >= 50
    market_strong = market_proxy is not None and market_proxy >= 72
    market_crowded = any(score is not None and score >= 85 for score in (market_score, evidence_score, ths_score, etf_score))
    market_missing = market_proxy is None
    policy_active = mainline_score > 0 and lifecycle not in {"dormant", "undated_unknown"}
    policy_breadth_confirmed = event_count_90d >= 2 or source_org_count_90d >= 2 or active_window_count >= 2
    recent_policy_positive = score_30d > 0 or event_count_30d > 0
    policy_not_accelerating = lifecycle != "accelerating" or acceleration_delta <= 0 or score_30d <= score_31_60d

    if lifecycle in {"dormant", "undated_unknown"} or (mainline_score <= 0 and score_90d <= 0):
        return _stage_fields("not_active", [f"生命周期为{lifecycle or '空'}，有效政策主线分不足"], market_row, mainline_row)

    if lifecycle == "legacy_tail":
        return _stage_fields("legacy_residual", ["主要贡献来自90日以外旧政策，近期政策事件不足"], market_row, mainline_row)

    if lifecycle == "cooling" or (score_31_60d > 0 and score_30d < score_31_60d * 0.6 and not market_strong):
        return _stage_fields("cooling_decline", ["30日政策贡献弱于31-60日，政策边际降温"], market_row, mainline_row)

    if market_crowded and policy_not_accelerating:
        return _stage_fields("crowded_late", ["市场热度处在高位，但政策边际没有继续加速"], market_row, mainline_row)

    if lifecycle in {"accelerating", "sustained"} and market_strong and policy_breadth_confirmed:
        return _stage_fields("main_rise_diffusion", ["政策仍处有效状态，市场热度已确认，事件或来源广度达标"], market_row, mainline_row)

    if lifecycle in {"accelerating", "sustained", "emerging", "single_event_emerging"} and market_confirmed:
        return _stage_fields("launch_confirmation", ["政策信号有效，市场热度开始确认"], market_row, mainline_row)

    if lifecycle in {"accelerating", "sustained", "emerging", "single_event_emerging"} and recent_policy_positive:
        reason = "政策信号有效，但市场热度缺失" if market_missing else "政策信号有效，但市场热度尚未确认"
        return _stage_fields("policy_incubation", [reason], market_row, mainline_row)

    if policy_active:
        return _stage_fields("unknown", ["政策主线分存在，但周期证据未形成稳定组合"], market_row, mainline_row)

    return _stage_fields("not_active", ["当前证据不足，不能按主线处理"], market_row, mainline_row)


def market_rows_by_theme(market_rows: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in market_rows or []:
        if not isinstance(row, dict):
            continue
        for key in _theme_keys(row):
            result[key] = row
    return result


def enrich_mainline_rows_with_cycle_stage(
    rows: list[dict[str, Any]], market_rows: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    market_by_theme = market_rows_by_theme(market_rows)
    enriched = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        item["lifecycle_state_label"] = item.get("lifecycle_state_label") or lifecycle_state_label(
            item.get("lifecycle_state")
        )
        market_row: dict[str, Any] | None = None
        for key in _theme_keys(item):
            market_row = market_by_theme.get(key)
            if market_row:
                break
        item.update(classify_mainline_cycle_stage(item, market_row))
        item.pop("_cycle_event_contributors", None)
        enriched.append(item)
    return enriched


def build_cycle_stage_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {stage: 0 for stage in CYCLE_STAGE_ORDER}
    labels = {stage: meta["label"] for stage, meta in CYCLE_STAGE_META.items()}
    for row in rows or []:
        stage = str(row.get("cycle_stage") or "unknown")
        if stage not in counts:
            stage = "unknown"
        counts[stage] += 1
    return {
        "scoring_version": SCORING_VERSION,
        "stage_order": list(CYCLE_STAGE_ORDER),
        "stage_labels": labels,
        "stage_counts": counts,
        "theme_count": sum(counts.values()),
    }
