from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE_RULES_PATH = ROOT / "config" / "mainline_class_lifecycle_rules.json"
INDICATOR_RULES_PATH = ROOT / "config" / "structural_indicator_mapping.json"
STRUCTURAL_LABELS = {
    "pre_structural": "尚未形成结构主线", "structural_forming": "结构孕育", "strategic_confirmation": "战略确认",
    "industrial_buildout": "产业建设", "commercial_validation": "商业验证", "penetration_expansion": "渗透扩张",
    "structural_maturity": "结构成熟", "structural_slowdown": "结构增速放缓", "structural_decline": "结构衰退",
    "structural_ended": "结构结束", "structural_restarting": "结构重启", "structural_uncertain": "结构不确定",
    "not_applicable": "不适用",
}
MARKET_STAGE_MAP = {
    "dormant": "unpriced", "incubating": "early_pricing", "emerging": "early_pricing", "launching": "launching",
    "confirmed": "confirmed", "expanding": "broadening", "mature": "crowded", "cooling": "cooling",
    "declining": "declining", "ended": "rejected", "restarting": "restarting", "uncertain": "uncertain",
}
MARKET_LABELS = {
    "unpriced": "尚未定价", "early_pricing": "开始定价", "launching": "市场启动", "confirmed": "市场确认",
    "broadening": "市场扩散", "crowded": "市场拥挤", "high_level_volatility": "高位震荡", "cooling": "市场降温",
    "declining": "市场退潮", "rejected": "市场否定", "restarting": "市场重启", "uncertain": "不确定",
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_structural_indicators(theme_id: str, features: dict[str, Any], *, data_date: str = "", industry_available: bool = False) -> list[dict[str, Any]]:
    mapping = _load(INDICATOR_RULES_PATH)
    indicators = []
    for dimension in mapping["dimensions"]:
        feature = mapping["feature_mapping"].get(dimension)
        value = features.get(feature) if feature else None
        observed = value is not None and (industry_available or dimension in {"technology", "infrastructure", "macro_cycle", "valuation_market", "supply_discipline"})
        numeric = float(value) if observed else None
        trend = "unknown" if numeric is None else ("accelerating" if numeric >= 75 else "improving" if numeric >= 60 else "stable" if numeric >= 40 else "weakening")
        indicators.append({
            "indicator_id": f"{theme_id}_{dimension}", "indicator_name": dimension, "dimension": dimension,
            "value": numeric, "trend": trend, "data_date": data_date, "source": "现有政策/市场研究代理" if observed else "",
            "confidence": 55 if observed else 0, "status": "observed" if observed else "unknown",
        })
    return indicators


def evaluate_structural_lifecycle(primary_class: str, features: dict[str, Any], indicators: list[dict[str, Any]], *, history_days: int = 0, structural_start_date: str = "") -> dict[str, Any]:
    rules = _load(LIFECYCLE_RULES_PATH)
    spec = rules.get(primary_class, rules["unclassified"])
    observed = [item for item in indicators if item["status"] == "observed"]
    observed_industrial = [item for item in observed if item["dimension"] in {"capital_expenditure", "commercialization", "industry_penetration", "revenue_profit", "capacity_utilization"}]
    strategy = float(features.get("national_strategy") or 0)
    technology = float(features.get("technology_paradigm") or 0)
    capex = float(features.get("industrial_capex") or 0)
    penetration = float(features.get("industry_penetration") or 0)
    commercialization = float(features.get("commercialization") or 0)
    if primary_class == "allocation_style":
        stage = "not_applicable"
    elif primary_class == "trading_branch":
        stage = "pre_structural"
    elif max(strategy, technology, float(features.get("macro_sensitivity") or 0), float(features.get("profit_repair") or 0)) < 40:
        stage = "pre_structural"
    elif history_days < int(spec.get("minimum_structural_confirmation_days") or 0):
        stage = "strategic_confirmation" if max(strategy, technology) >= 65 else "structural_forming"
    elif len(observed_industrial) < int(spec.get("minimum_industrial_validation_dimensions") or 0):
        stage = "strategic_confirmation" if max(strategy, technology) >= 65 else "structural_forming"
    elif penetration >= 70:
        stage = "penetration_expansion"
    elif commercialization >= 65:
        stage = "commercial_validation"
    elif capex >= 60:
        stage = "industrial_buildout"
    else:
        stage = "structural_forming"
    structural_inputs = [strategy, technology, capex, penetration, commercialization, float(features.get("cross_industry_diffusion") or 0)]
    conviction = round(sum(structural_inputs) / len(structural_inputs), 2)
    confidence = min(85.0, 35.0 + len(observed) * 5.0 + min(history_days, 365) / 365 * 20)
    required_history = int(spec.get("minimum_structural_confirmation_days") or 0)
    if history_days < required_history or len(observed_industrial) < int(spec.get("minimum_industrial_validation_dimensions") or 0):
        confidence = min(confidence, 55.0)
    age_status = "not_applicable" if primary_class == "allocation_style" else ("before_available_history" if structural_start_date == "" and stage not in {"pre_structural", "structural_forming"} else "insufficient_history" if history_days < required_history else "estimated")
    return {
        "version": "structural_lifecycle_v1", "structural_stage": stage, "structural_stage_label": STRUCTURAL_LABELS[stage],
        "structural_stage_confidence": round(confidence, 2), "structural_conviction_score": conviction,
        "time_horizon": {**spec["expected_duration"], "basis": [f"按{primary_class}类型选择观察窗口", "典型持续时间不是结束倒计时"]},
        "structural_age": {"estimated_start_date": structural_start_date, "estimated_duration_days": history_days if structural_start_date else None, "age_status": age_status, "confidence": round(confidence * 0.7, 2)},
        "indicator_coverage": {"observed": len(observed), "total": len(indicators), "industrial_observed": len(observed_industrial)},
        "end_rule": {"minimum_decline_days": spec.get("structural_decline_minimum_days", 0), "market_decline_alone_can_end": primary_class == "trading_branch"},
        "limitations": ["现有仓库历史长度不足以确认完整结构生命周期"] if history_days < required_history else [],
    }


def build_market_expression_lifecycle(state: dict[str, Any]) -> dict[str, Any]:
    stage = MARKET_STAGE_MAP.get(str(state.get("evidence_stage") or state.get("lifecycle_stage") or "uncertain"), "uncertain")
    start = str(state.get("cycle_start_observation_date") or "")
    current = str(state.get("date") or "")
    duration = 0
    try:
        duration = (date.fromisoformat(current) - date.fromisoformat(start)).days if start and current else 0
    except ValueError:
        duration = 0
    return {
        "version": "market_expression_lifecycle_v1", "market_expression_stage": stage,
        "market_expression_stage_label": MARKET_LABELS[stage],
        "market_expression_stage_confidence": state.get("lifecycle_stage_confidence", 0),
        "market_cycle_age": {"cycle_start_date": start, "duration_days": max(0, duration), "confidence": state.get("lifecycle_date_confidence", 0)},
        "source_lifecycle_stage": state.get("evidence_stage", state.get("lifecycle_stage")),
    }
