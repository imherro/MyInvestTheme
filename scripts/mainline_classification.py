from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "config" / "mainline_classification_rules.json"
CLASS_LABELS = {
    "era_industrial": "时代产业主线",
    "strategic_growth": "战略成长主线",
    "policy_profit_repair": "政策盈利修复主线",
    "macro_cycle": "宏观阶段主线",
    "trading_branch": "交易支线",
    "allocation_style": "配置风格",
    "unclassified": "未分类",
}
FEATURE_LABELS = {
    "technology_paradigm": "技术范式", "national_strategy": "国家长期战略", "industrial_capex": "产业资本开支",
    "industry_penetration": "产业渗透", "commercialization": "商业化", "cross_industry_diffusion": "跨行业扩散",
    "duration_support": "持续时间支持", "profit_repair": "盈利修复", "supply_discipline": "供给纪律",
    "macro_sensitivity": "宏观敏感度", "commodity_price_dependency": "商品价格依赖", "event_dependency": "事件依赖",
    "allocation_style_dependency": "配置属性", "cash_flow_quality": "现金流质量", "rate_sensitivity": "利率敏感度",
    "market_intensity": "市场强度",
}


def load_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _score(features: dict[str, float], weights: dict[str, float]) -> float:
    denominator = sum(float(weight) for weight in weights.values())
    return round(sum(float(features.get(key) or 0) * float(weight) for key, weight in weights.items()) / denominator, 2) if denominator else 0.0


def classify_mainline(features: dict[str, Any], *, rules: dict[str, Any] | None = None) -> dict[str, Any]:
    active = rules or load_rules()
    values = {key: max(0.0, min(100.0, float(value or 0))) for key, value in features.items()}
    class_scores = {name: _score(values, spec.get("weights") or {}) for name, spec in active["classes"].items()}
    constraints = active["constraints"]
    era_blockers: list[str] = []
    if max(values.get("technology_paradigm", 0), values.get("cross_industry_diffusion", 0)) < constraints["era_minimum_technology_or_restructuring"]:
        class_scores["era_industrial"] = min(class_scores["era_industrial"], constraints["commodity_era_cap"])
        era_blockers.append("缺少技术范式变化或跨行业产业重构证据")
    if max(values.get("industrial_capex", 0), values.get("industry_penetration", 0)) < constraints["era_minimum_capex_or_penetration"]:
        class_scores["era_industrial"] = min(class_scores["era_industrial"], constraints["commodity_era_cap"])
        era_blockers.append("长期资本开支或产业渗透证据不足")
    if values.get("commodity_price_dependency", 0) >= 75 and values.get("technology_paradigm", 0) < 55:
        class_scores["era_industrial"] = min(class_scores["era_industrial"], constraints["commodity_era_cap"])
        era_blockers.append("主要由商品价格驱动，不能据此升级为时代产业主线")
    if values.get("event_dependency", 0) >= 75:
        class_scores["era_industrial"] = min(class_scores["era_industrial"], constraints["event_era_cap"])
        era_blockers.append("对单一事件依赖过高")
    era_structural_gate = (
        class_scores["era_industrial"] >= 65
        and values.get("technology_paradigm", 0) >= 65
        and values.get("national_strategy", 0) >= 75
        and values.get("industrial_capex", 0) >= 70
        and max(values.get("industry_penetration", 0), values.get("cross_industry_diffusion", 0)) >= 65
    )
    if values.get("allocation_style_dependency", 0) >= 70:
        primary = "allocation_style"
    elif era_structural_gate:
        primary = "era_industrial"
    elif (
        class_scores["strategic_growth"] >= float(active["classes"]["strategic_growth"]["minimum_score"])
        and class_scores["era_industrial"] - class_scores["strategic_growth"] < 5
        and min(values.get("commercialization", 0), values.get("industry_penetration", 0)) < 60
    ):
        primary = "strategic_growth"
    else:
        eligible = [(score, name) for name, score in class_scores.items() if score >= float(active["classes"][name].get("minimum_score") or 0)]
        primary = max(eligible, default=(0, "unclassified"))[1]
    ordered = sorted(class_scores.items(), key=lambda item: (-item[1], item[0]))
    secondary = [name for name, score in ordered if name != primary and score >= 45 and name not in {"allocation_style", "trading_branch"}][:2]
    best = class_scores.get(primary, 0)
    runner_up = max((score for name, score in ordered if name != primary), default=0)
    margin = max(0.0, best - runner_up)
    confidence = min(92.0, 48.0 + margin * 1.2 + best * 0.25)
    if values.get("industry_data_available", 0) <= 0 and primary in {"era_industrial", "strategic_growth"}:
        confidence = min(confidence, float(constraints["industry_unknown_confidence_cap"]))
    if margin < 5:
        confidence = min(confidence, float(constraints["low_margin_confidence_cap"]))
    positive_features = sorted(((value, key) for key, value in values.items() if value >= 60 and key != "industry_data_available"), reverse=True)[:4]
    contradictions = list(era_blockers if primary != "era_industrial" else [])
    if primary in {"era_industrial", "strategic_growth"} and values.get("commercialization", 0) < 50:
        contradictions.append("商业化验证仍不足")
    if primary in {"era_industrial", "strategic_growth"} and values.get("industry_data_available", 0) <= 0:
        contradictions.append("产业指标缺失，类型置信度受限")
    return {
        "classification_version": active["version"],
        "primary_mainline_class": primary,
        "mainline_class": primary,
        "mainline_class_label": CLASS_LABELS[primary],
        "secondary_class_tags": secondary,
        "secondary_class_labels": [CLASS_LABELS[name] for name in secondary],
        "class_confidence": round(confidence, 2),
        "feature_scores": values,
        "class_scores": class_scores,
        "class_reasons": [f"{FEATURE_LABELS.get(key, key)} {value:.0f}" for value, key in positive_features],
        "class_contradictions": contradictions,
    }


def profile_for(research_object_id: str, *, rules: dict[str, Any] | None = None) -> dict[str, float]:
    active = rules or load_rules()
    return dict(active.get("research_object_profiles", {}).get(research_object_id) or {})
