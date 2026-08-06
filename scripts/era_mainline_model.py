from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from era_lifecycle_engine import enrich_lifecycle, lifecycle_dates
    from industry_validation import build_industry_dimension
    from market_confirmation import build_market_dimensions
    from narrative_momentum import build_narrative_dimension
except ModuleNotFoundError:
    from scripts.era_lifecycle_engine import enrich_lifecycle, lifecycle_dates
    from scripts.industry_validation import build_industry_dimension
    from scripts.market_confirmation import build_market_dimensions
    from scripts.narrative_momentum import build_narrative_dimension


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "config" / "era_mainline_rules.json"
EVENT_RULES_PATH = ROOT / "config" / "policy_event_type_rules.json"
TAXONOMY_PATH = ROOT / "config" / "era_theme_taxonomy.json"
VERSION = "era_mainline_model_v1"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    return _load(path)


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _first_level_rows(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = report.get("theme_summary", {}).get("themes") or report.get("mainline_ranking") or []
    return {str(row.get("theme_id") or ""): row for row in rows if row.get("theme_id")}


def _mainline_rows(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row.get("theme_id") or ""): row for row in report.get("mainline_ranking") or [] if row.get("theme_id")}


def classify_policy_event(event: dict[str, Any], *, rules: dict[str, Any] | None = None) -> dict[str, Any]:
    active = rules or _load(EVENT_RULES_PATH)
    text = " ".join(str(event.get(field) or "") for field in ("primary_policy_title", "cluster_reason", "top_stance_evidence"))
    event_type = str(active.get("default_type") or "regulatory_support")
    spec = (active.get("types") or {}).get(event_type, {})
    for candidate, candidate_spec in (active.get("types") or {}).items():
        if any(keyword in text for keyword in candidate_spec.get("keywords") or []):
            event_type, spec = candidate, candidate_spec
            break
    stance = str(event.get("cluster_stance_label") or "neutral_or_mixed")
    direction = "restrictive" if "restrictive" in stance or event_type in {"restrictive_policy", "policy_exit"} else ("supportive" if "supportive" in stance else "neutral")
    cluster_size = int(event.get("cluster_size") or 1)
    return {
        "event_id": event.get("event_cluster_id", ""),
        "event_type": event_type,
        "strength": float(spec.get("strength") or 50),
        "execution_level": float(spec.get("execution_level") or 50),
        "novelty_score": round(100.0 / max(1, cluster_size), 2),
        "continuity_score": min(100.0, 35.0 + cluster_size * 15.0),
        "theme_relevance": round(_number(event.get("cluster_relevance_score_v2")) * 100, 2),
        "direction": direction,
        "event_date": event.get("event_activity_date") or event.get("published_date") or "",
        "source_org": event.get("source_org_norm") or event.get("source") or "",
        "title": event.get("primary_policy_title") or "",
        "url": event.get("url") or "",
    }


def build_policy_dimension(theme: dict[str, Any], mainline: dict[str, Any]) -> dict[str, Any]:
    events = [classify_policy_event(item) for item in theme.get("all_event_contributors") or theme.get("top_event_contributors") or []]
    supportive = [item for item in events if item["direction"] == "supportive"]
    restrictive = [item for item in events if item["direction"] == "restrictive"]
    strategic = [item for item in events if item["event_type"] in {"strategic_declaration", "national_plan", "implementation_plan", "funding_support", "major_project"}]
    policy_base = _number(mainline.get("policy_theme_conviction_score", mainline.get("mainline_score_v6"))) * 100
    long_term = sum(item["strength"] for item in strategic) / len(strategic) if strategic else min(45.0, policy_base)
    execution = sum(item["execution_level"] for item in events) / len(events) if events else 0.0
    sources = {item["source_org"] for item in events if item["source_org"]}
    cross_department = min(100.0, len(sources) * 25.0)
    recent = _number(theme.get("score_30d"))
    older = _number(theme.get("score_31_60d"))
    reinforcement = 80.0 if recent > older and recent > 0 else (45.0 if recent > 0 else 10.0)
    novelty = sum(item["novelty_score"] for item in events) / len(events) if events else 0.0
    restriction = min(100.0, len(restrictive) / max(1, len(events)) * 100)
    conviction = max(0.0, min(100.0, 0.55 * policy_base + 0.15 * long_term + 0.12 * execution + 0.08 * cross_department + 0.07 * reinforcement + 0.03 * novelty - 0.20 * restriction))
    return {
        "scoring_version": "policy_conviction_v1",
        "policy_conviction_score": round(conviction, 2),
        "policy_long_term_score": round(long_term, 2),
        "policy_execution_score": round(execution, 2),
        "policy_cross_department_score": round(cross_department, 2),
        "policy_reinforcement_score": round(reinforcement, 2),
        "policy_novelty_score": round(novelty, 2),
        "policy_restriction_score": round(restriction, 2),
        "event_count": len(events),
        "supportive_event_count": len(supportive),
        "restrictive_event_count": len(restrictive),
        "source_org_count": len(sources),
        "events": events,
    }


def _dimension_score(dimension: dict[str, Any], field: str) -> float | None:
    value = dimension.get(field)
    return round(float(value), 2) if value is not None else None


def _weighted_score(dimensions: dict[str, float | None], rules: dict[str, Any]) -> tuple[float, float]:
    weights = rules.get("dimension_weights") or {}
    available = [(name, score) for name, score in dimensions.items() if score is not None]
    denominator = sum(float(weights.get(name) or 0) for name, _ in available)
    score = sum(float(score) * float(weights.get(name) or 0) for name, score in available) / denominator if denominator else 0.0
    coverage = sum(float(weights.get(name) or 0) for name, _ in available)
    return round(score, 2), round(coverage, 4)


def _conflicts(policy: float, industry: float | None, market: float, narrative: float) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    if policy >= 60 and market < 40:
        result.append({"type": "policy_strong_market_weak", "message": "政策强、市场弱：政策方向明确，但资本市场尚未形成持续确认。"})
    if policy >= 60 and (industry is None or industry < 40):
        result.append({"type": "policy_strong_industry_weak", "message": "政策强、产业弱或未知：长期方向明确，但产业兑现证据仍不足。"})
    if market >= 65 and policy < 35:
        result.append({"type": "market_strong_policy_weak", "message": "市场强、政策弱：当前更接近市场主题，不能据此确认时代主线。"})
    if narrative >= 65 and policy < 40 and (industry is None or industry < 40):
        result.append({"type": "narrative_strong_fundamentals_weak", "message": "叙事较强但政策与产业基础不足，存在叙事先行风险。"})
    return result


def _theme_name_map() -> dict[str, str]:
    taxonomy = _load(TAXONOMY_PATH)
    return {item["theme_id"]: item["theme_name"] for item in taxonomy.get("primary_themes") or []}


def build_report_snapshot(report_id: str, report: dict[str, Any], *, rules: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    active = rules or load_rules()
    themes = _first_level_rows(report)
    mainlines = _mainline_rows(report)
    markets = build_market_dimensions(report_id, report)
    names = _theme_name_map()
    rows: list[dict[str, Any]] = []
    for theme_id, theme in themes.items():
        policy_dimension = build_policy_dimension(theme, mainlines.get(theme_id, theme))
        market_dimension = markets.get(theme_id) or {
            "market_confirmation_score": 0.0,
            "market_stage": "unconfirmed",
            "secondary_themes": [],
            "data_coverage": 0.0,
        }
        industry_dimension = build_industry_dimension(theme_id)
        narrative_dimension = build_narrative_dimension(theme, market_dimension.get("secondary_themes") or [])
        scores = {
            "policy": _dimension_score(policy_dimension, "policy_conviction_score"),
            "industry": _dimension_score(industry_dimension, "industry_validation_score"),
            "market": _dimension_score(market_dimension, "market_confirmation_score"),
            "narrative": _dimension_score(narrative_dimension, "narrative_momentum_score"),
        }
        era_score, weighted_coverage = _weighted_score(scores, active)
        conflicts = _conflicts(scores["policy"] or 0, scores["industry"], scores["market"] or 0, scores["narrative"] or 0)
        source_coverage = [policy_dimension.get("event_count", 0) > 0, industry_dimension.get("observed_indicator_count", 0) > 0, market_dimension.get("data_coverage", 0) > 0, narrative_dimension.get("data_coverage", 0) > 0]
        confidence = max(0.0, min(100.0, 45 + weighted_coverage * 35 + sum(source_coverage) * 5 - len(conflicts) * 8))
        rows.append(
            {
                "theme_id": theme_id,
                "theme_name": names.get(theme_id, theme.get("theme_name", theme_id)),
                "source_theme_name": theme.get("theme_name", ""),
                "date": report.get("basis_date", ""),
                "observation_id": report_id,
                "policy_dimension": policy_dimension,
                "industry_dimension": industry_dimension,
                "market_dimension": market_dimension,
                "narrative_dimension": narrative_dimension,
                "policy_score": scores["policy"],
                "industry_score": scores["industry"],
                "market_score": scores["market"],
                "narrative_score": scores["narrative"],
                "source_lifecycle_state": str(mainlines.get(theme_id, theme).get("lifecycle_state") or theme.get("lifecycle_state") or ""),
                "era_mainline_score": era_score,
                "data_coverage": weighted_coverage,
                "era_mainline_confidence": round(confidence, 2),
                "confidence": round(confidence, 2),
                "conflicts": conflicts,
            }
        )
    return rows


def _invalidating_conditions(theme_id: str) -> list[str]:
    common = [
        "核心政策方向发生逆转或关键投入机制退出",
        "产业代理指标连续两个季度明显下降",
        "主题市场长期相对强度连续120日落后且广度收缩",
        "主线叙事被新的国家级战略持续替代",
    ]
    if theme_id == "ai_compute_communications":
        common.insert(1, "算力与云资本开支持续下降，产业应用无法兑现")
    if theme_id == "new_energy_power_equipment":
        common.insert(1, "电网、储能和新能源消纳投资持续弱于规划")
    return common


def _status(row: dict[str, Any], thresholds: dict[str, Any]) -> str:
    policy = float(row.get("policy_score") or 0)
    market = float(row.get("market_score") or 0)
    industry = row.get("industry_score")
    score = float(row.get("era_mainline_score") or 0)
    stage = row.get("lifecycle_stage")
    confirmed = policy >= float(thresholds["policy_minimum"]) and (market >= float(thresholds["market_confirmation"]) or (industry is not None and float(industry) >= float(thresholds["industry_confirmation"])))
    if row.get("source_lifecycle_state") == "legacy_tail" and policy >= 25:
        return "legacy_mainline"
    if stage in {"cooling", "declining", "ended"} and score >= float(thresholds["declining_score"]):
        return "declining_mainline"
    if confirmed and score >= float(thresholds["absolute_mainline_score"]):
        return "confirmed_candidate"
    if policy >= float(thresholds["policy_minimum"]) and market < float(thresholds["market_confirmation"]):
        return "policy_theme_only"
    if market >= 60 and policy < float(thresholds["policy_minimum"]):
        return "market_theme_only"
    if score >= float(thresholds["emerging_score"]):
        return "emerging_candidate"
    return "not_a_mainline"


def build_era_mainline_report(
    report_id: str,
    report: dict[str, Any],
    historical_reports: list[tuple[str, dict[str, Any]]],
    *,
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active = rules or load_rules()
    snapshots_by_theme: dict[str, list[dict[str, Any]]] = {}
    for source_id, source_report in historical_reports:
        try:
            snapshots = build_report_snapshot(source_id, source_report, rules=active)
        except (KeyError, TypeError, ValueError):
            continue
        for snapshot in snapshots:
            snapshots_by_theme.setdefault(snapshot["theme_id"], []).append(snapshot)
    if report_id not in {source_id for source_id, _ in historical_reports}:
        for snapshot in build_report_snapshot(report_id, report, rules=active):
            snapshots_by_theme.setdefault(snapshot["theme_id"], []).append(snapshot)

    states: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    for theme_id, history in snapshots_by_theme.items():
        history.sort(key=lambda item: (item.get("date", ""), item.get("observation_id", "")))
        previous_scores: dict[str, Any] | None = None
        for item in history:
            if previous_scores:
                item["dimension_changes"] = {
                    key: round(_number(item.get(key)) - _number(previous_scores.get(key)), 2)
                    for key in ("policy_score", "market_score", "narrative_score", "era_mainline_score")
                }
            else:
                item["dimension_changes"] = {}
            previous_scores = item
        lifecycle_history, theme_transitions = enrich_lifecycle(history)
        latest = deepcopy(lifecycle_history[-1])
        events = latest["policy_dimension"].get("events") or []
        dates = lifecycle_dates(lifecycle_history, events, report.get("basis_date", ""))
        latest.update(dates)
        latest["lifecycle_confidence"] = latest.pop("confidence")
        latest["stage_confidence"] = latest["lifecycle_confidence"]
        latest["score_history"] = [
            {
                "date": item["date"], "report_id": item["observation_id"], "era_mainline_score": item["era_mainline_score"],
                "policy_score": item["policy_score"], "industry_score": item["industry_score"], "market_score": item["market_score"],
                "narrative_score": item["narrative_score"], "confidence": item["era_mainline_confidence"], "lifecycle_stage": item["lifecycle_stage"],
            }
            for item in lifecycle_history
        ]
        latest["stage_history"] = theme_transitions
        latest["supporting_evidence"] = [
            f"政策持续性 {latest['policy_score']:.1f}",
            f"市场持续确认 {latest['market_score']:.1f}",
            f"官方叙事扩散 {latest['narrative_score']:.1f}",
        ] + ([f"产业验证 {latest['industry_score']:.1f}"] if latest.get("industry_score") is not None else [])
        latest["contradicting_evidence"] = [item["message"] for item in latest.get("conflicts") or []]
        if latest.get("industry_score") is None:
            latest["contradicting_evidence"].append("可靠产业代理数据缺失，产业兑现尚不能独立验证。")
        if latest.get("narrative_dimension", {}).get("narrative_stage") == "fading":
            latest["contradicting_evidence"].append("官方叙事强化频率较前期下降，存在叙事疲劳迹象。")
        latest["invalidating_conditions"] = _invalidating_conditions(theme_id)
        latest["core_drivers"] = latest["supporting_evidence"][:3]
        latest["start_reasons"] = ["政策信号持续并出现市场或产业响应"] if latest.get("estimated_start_date") else []
        latest["reinforcement_reasons"] = ["出现新的可识别政策事件"] if latest.get("latest_reinforcement_date") else []
        latest["weakening_reasons"] = latest.get("stage_reasons") if latest.get("lifecycle_stage") in {"cooling", "declining"} else []
        latest["end_reasons"] = latest.get("stage_reasons") if latest.get("lifecycle_stage") == "ended" else []
        latest["era_mainline_status"] = _status(latest, active["thresholds"])
        transitions.extend(theme_transitions)
        states.append(latest)

    states.sort(key=lambda item: (-float(item.get("era_mainline_score") or 0), item["theme_id"]))
    for rank, state in enumerate(states, start=1):
        state["era_rank"] = rank
    points_by_date: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for state in states:
        for point in state.get("score_history") or []:
            points_by_date.setdefault(str(point.get("date") or ""), []).append((state, point))
    for points in points_by_date.values():
        points.sort(key=lambda pair: (-float(pair[1].get("era_mainline_score") or 0), pair[0]["theme_id"]))
        for historical_rank, (_, point) in enumerate(points, start=1):
            point["era_rank"] = historical_rank
    milestones: dict[str, dict[str, Any]] = {}
    for state in states:
        history = state.get("score_history") or []
        transitions_by_stage = {item.get("to_stage"): item.get("change_date", "") for item in state.get("stage_history") or []}
        top_three = next((point for point in history if int(point.get("era_rank") or 999) <= 3), None)
        peak = max(history, key=lambda point: float(point.get("era_mainline_score") or 0), default=None)
        milestones[state["theme_id"]] = {
            "theme_id": state["theme_id"],
            "theme_name": state["theme_name"],
            "first_top3_date": top_three.get("date", "") if top_three else "",
            "first_launching_date": transitions_by_stage.get("launching", ""),
            "first_confirmation_date": transitions_by_stage.get("confirmed", ""),
            "highest_score_date": peak.get("date", "") if peak else "",
            "highest_score": peak.get("era_mainline_score") if peak else None,
            "cooling_start_date": transitions_by_stage.get("cooling", ""),
            "ended_date": transitions_by_stage.get("ended", ""),
            "restarting_date": transitions_by_stage.get("restarting", ""),
        }
    confirmed = [item for item in states if item["era_mainline_status"] == "confirmed_candidate"]
    gap = float(confirmed[0]["era_mainline_score"] - confirmed[1]["era_mainline_score"]) if len(confirmed) > 1 else 999.0
    if not states or max(float(item.get("data_coverage") or 0) for item in states) < float(active["thresholds"]["minimum_data_coverage"]):
        regime = "data_insufficient"
    elif not confirmed:
        regime = "no_clear_mainline"
    elif len(confirmed) == 1:
        regime = "single_dominant"
    elif len(confirmed) >= 3:
        regime = "multi_mainline"
    elif gap <= float(active["thresholds"]["dual_mainline_gap"]):
        regime = "dual_mainline"
    else:
        regime = "single_dominant"
    for index, state in enumerate(confirmed):
        state["era_mainline_status"] = "primary_era_mainline" if index == 0 else "secondary_era_mainline"
    primary = confirmed[0] if confirmed else None
    secondary = confirmed[1] if len(confirmed) > 1 else None
    if regime == "no_clear_mainline":
        summary = "当前没有明确时代主线，市场处于旧主线退潮和新主线孕育之间。"
    else:
        names = "、".join(item["theme_name"] for item in confirmed[:3])
        regime_labels = {"single_dominant": "单一主线", "dual_mainline": "双主线", "multi_mainline": "多主线并存", "rotation": "轮动", "transition": "切换"}
        summary = f"当前主线格局为{regime_labels.get(regime, regime)}，获得多层证据确认的方向包括：{names}。"
    return {
        "scoring_version": VERSION,
        "rules_version": active.get("version", "era_mainline_rules_v1"),
        "report_id": report_id.replace("mainline_review_", "era_mainline_review_"),
        "source_report_id": report_id,
        "basis_date": report.get("basis_date", ""),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mainline_regime": regime,
        "primary_mainline": primary,
        "secondary_mainline": secondary,
        "emerging_candidates": [item for item in states if item["era_mainline_status"] == "emerging_candidate"],
        "declining_mainlines": [item for item in states if item["era_mainline_status"] in {"declining_mainline", "legacy_mainline"}],
        "theme_states": states,
        "transitions": sorted(transitions, key=lambda item: (item.get("change_date", ""), item.get("theme_id", ""))),
        "milestones": milestones,
        "data_coverage": {
            "policy": "available", "industry": "partial_or_unknown", "market": "available", "narrative": "available_from_official_sources",
            "overall": round(sum(float(item.get("data_coverage") or 0) for item in states) / len(states), 4) if states else 0.0,
        },
        "summary": summary,
        "score_semantics": "时代主线分用于结构化研究政策持续性、产业验证、市场确认和叙事扩散，不表示未来收益。",
    }
