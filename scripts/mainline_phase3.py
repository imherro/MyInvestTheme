from __future__ import annotations

from typing import Any

try:
    from allocation_style_model import evaluate_high_dividend
    from mainline_classification import CLASS_LABELS, classify_mainline, load_rules as load_classification_rules, profile_for
    from structural_validation import build_market_expression_lifecycle, build_structural_indicators, evaluate_structural_lifecycle
except ModuleNotFoundError:
    from scripts.allocation_style_model import evaluate_high_dividend
    from scripts.mainline_classification import CLASS_LABELS, classify_mainline, load_rules as load_classification_rules, profile_for
    from scripts.structural_validation import build_market_expression_lifecycle, build_structural_indicators, evaluate_structural_lifecycle


RESEARCH_OBJECTS = [
    {"theme_id": "ai_technology_self_reliance", "theme_name": "AI科技与自主可控", "sources": [("ai_compute_communications", 0.62), ("hard_tech_semiconductor", 0.38)]},
    {"theme_id": "energy_revolution_power_system", "theme_name": "能源革命与新型电力系统", "sources": [("new_energy_power_equipment", 1.0)]},
    {"theme_id": "advanced_manufacturing", "theme_name": "先进制造、机器人与商业航天", "sources": [("high_end_manufacturing_robotics_defense", 1.0)]},
    {"theme_id": "reflation_resources", "theme_name": "再通胀与资源品", "sources": [("resources_cycle", 1.0)]},
    {"theme_id": "anti_involution_profit_repair", "theme_name": "反内卷与供给治理", "sources": [("infrastructure_materials", 0.72), ("resources_cycle", 0.28)]},
    {"theme_id": "high_dividend_style", "theme_name": "高股息与稳定现金流", "sources": [("resources_cycle", 0.45), ("infrastructure_materials", 0.35), ("consumption_media", 0.20)]},
    {"theme_id": "innovative_medicine_growth", "theme_name": "创新医药战略成长", "sources": [("innovative_medicine", 1.0)]},
    {"theme_id": "consumer_culture_branch", "theme_name": "消费与文化服务交易支线", "sources": [("consumption_media", 1.0)]},
]
CLASS_RANKING_KEYS = {
    "era_industrial": "era_industrial_ranking", "strategic_growth": "strategic_growth_ranking",
    "policy_profit_repair": "policy_profit_repair_ranking", "macro_cycle": "macro_cycle_ranking",
    "trading_branch": "trading_branch_ranking", "allocation_style": "allocation_style_ranking",
}


def _weighted(states: dict[str, dict[str, Any]], sources: list[tuple[str, float]], field: str) -> float | None:
    values = [(states[source].get(field), weight) for source, weight in sources if source in states and states[source].get(field) is not None]
    denominator = sum(weight for _, weight in values)
    return round(sum(float(value) * weight for value, weight in values) / denominator, 2) if denominator else None


def _representative(states: dict[str, dict[str, Any]], sources: list[tuple[str, float]]) -> dict[str, Any]:
    available = [(weight, states[source]) for source, weight in sources if source in states]
    return max(available, key=lambda item: item[0])[1] if available else {}


def _compact_base(base: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "era_rank", "mainline_qualification", "mainline_qualification_label", "era_mainline_status", "era_mainline_score", "narrative_score",
        "evidence_stage", "lifecycle_stage", "lifecycle_stage_label", "momentum_state", "momentum_state_label",
        "cycle_id", "cycle_sequence", "cycle_start_observation_date", "stage_entered_at", "stage_dwell_days",
        "stage_dwell_satisfied", "estimated_start_date", "start_date_status", "confirmation_date", "date",
        "latest_reinforcement_date", "latest_reinforcement_type", "latest_policy_reinforcement_event_date",
        "latest_market_reinforcement_date", "latest_industry_reinforcement_date", "latest_narrative_reinforcement_date",
        "latest_composite_reinforcement_date", "latest_reinforcement_reasons", "current_state_confidence",
        "lifecycle_stage_confidence", "lifecycle_date_confidence", "era_mainline_confidence", "lifecycle_confidence",
        "supporting_evidence", "contradicting_evidence", "invalidating_conditions", "stage_history", "history_coverage",
    )
    result = {field: base.get(field) for field in fields if field in base}
    result["policy_dimension"] = {key: value for key, value in (base.get("policy_dimension") or {}).items() if key != "events"}
    result["industry_dimension"] = base.get("industry_dimension") or {}
    result["market_dimension"] = base.get("market_dimension") or {}
    result["narrative_dimension"] = base.get("narrative_dimension") or {}
    result["score_history"] = [
        {key: point.get(key) for key in ("date", "report_id", "era_mainline_score", "market_score", "evidence_stage", "lifecycle_stage", "momentum_state")}
        for point in base.get("score_history") or []
    ]
    return result


def _observed_features(object_id: str, base: dict[str, Any], sources: list[tuple[str, float]], states: dict[str, dict[str, Any]]) -> dict[str, float]:
    features = profile_for(object_id)
    policy = _weighted(states, sources, "policy_score")
    market = _weighted(states, sources, "market_score")
    industry = _weighted(states, sources, "industry_score")
    narrative = _weighted(states, sources, "narrative_score")
    if policy is not None:
        features["national_strategy"] = round(features.get("national_strategy", policy) * 0.65 + policy * 0.35, 2)
    if market is not None:
        features["market_intensity"] = market
    if narrative is not None:
        features["cross_industry_diffusion"] = round(features.get("cross_industry_diffusion", narrative) * 0.75 + narrative * 0.25, 2)
    if industry is not None:
        features["commercialization"] = round(features.get("commercialization", industry) * 0.55 + industry * 0.45, 2)
        features["industry_penetration"] = round(features.get("industry_penetration", industry) * 0.65 + industry * 0.35, 2)
        features["industry_data_available"] = 100
    else:
        features["industry_data_available"] = 0
    return features


def _ranking_score(item: dict[str, Any]) -> float:
    kind = item["primary_mainline_class"]
    structural = float(item.get("structural_conviction_score") or 0)
    cycle = float(item.get("cycle_strength_score") or 0)
    market = float(item.get("market_expression_score") or 0)
    features = item.get("feature_scores") or {}
    if kind == "era_industrial":
        return structural
    if kind == "strategic_growth":
        return 0.60 * structural + 0.20 * float(features.get("commercialization") or 0) + 0.20 * float(features.get("industry_penetration") or 0)
    if kind == "policy_profit_repair":
        return 0.35 * float(item.get("policy_score") or 0) + 0.35 * cycle + 0.30 * float(features.get("profit_repair") or 0)
    if kind == "macro_cycle":
        return 0.55 * cycle + 0.45 * market
    if kind == "trading_branch":
        return 0.65 * market + 0.35 * float(features.get("event_dependency") or 0)
    if kind == "allocation_style":
        return float(item.get("allocation_style_score") or 0)
    return 0.0


def build_research_objects(theme_states: list[dict[str, Any]], *, basis_date: str = "") -> list[dict[str, Any]]:
    by_id = {item["theme_id"]: item for item in theme_states}
    objects: list[dict[str, Any]] = []
    for spec in RESEARCH_OBJECTS:
        representative = _representative(by_id, spec["sources"])
        if not representative:
            continue
        base = _compact_base(representative)
        features = _observed_features(spec["theme_id"], base, spec["sources"], by_id)
        classification = classify_mainline(features)
        history_days = max((int(by_id[source].get("history_coverage", {}).get("coverage_days") or 0) for source, _ in spec["sources"] if source in by_id), default=0)
        indicators = build_structural_indicators(spec["theme_id"], features, data_date=basis_date, industry_available=features.get("industry_data_available", 0) > 0)
        structural = evaluate_structural_lifecycle(classification["primary_mainline_class"], features, indicators, history_days=history_days)
        if classification["primary_mainline_class"] in {"era_industrial", "strategic_growth"}:
            structural["structural_conviction_score"] = classification["class_scores"][classification["primary_mainline_class"]]
        market = build_market_expression_lifecycle(base)
        policy_score = _weighted(by_id, spec["sources"], "policy_score") or 0.0
        market_score = _weighted(by_id, spec["sources"], "market_score") or 0.0
        industry_score = _weighted(by_id, spec["sources"], "industry_score")
        cycle_score = round(0.34 * float(features.get("macro_sensitivity") or 0) + 0.24 * float(features.get("profit_repair") or 0) + 0.20 * float(features.get("supply_discipline") or 0) + 0.22 * policy_score, 2)
        item = {
            **base, **classification, **structural, **market,
            "theme_id": spec["theme_id"], "theme_name": spec["theme_name"], "source_theme_ids": [source for source, _ in spec["sources"]],
            "policy_score": round(policy_score, 2), "industry_score": industry_score, "market_score": round(market_score, 2),
            "structural_lifecycle": structural, "market_expression_lifecycle": market,
            "cycle_strength_score": cycle_score, "market_expression_score": round(market_score, 2),
            "structural_indicators": indicators,
            "duration_estimate_confidence": structural["structural_age"]["confidence"],
        }
        if classification["primary_mainline_class"] == "allocation_style":
            style = evaluate_high_dividend(features, market_score=market_score)
            item.update(style)
        item["class_ranking_score"] = round(_ranking_score(item), 2)
        objects.append(item)
    return objects


def build_class_rankings(objects: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    rankings = {key: [] for key in CLASS_RANKING_KEYS.values()}
    for kind, key in CLASS_RANKING_KEYS.items():
        rows = sorted((item for item in objects if item["primary_mainline_class"] == kind), key=lambda item: (-float(item["class_ranking_score"]), item["theme_id"]))
        for rank, item in enumerate(rows, 1):
            item["class_rank"] = rank
        rankings[key] = rows
    return rankings


def build_relationships(objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ids = {item["theme_id"] for item in objects}
    candidates = [
        ("reflation_resources", "anti_involution_profit_repair", "reinforcing", ["上游价格改善", "供给治理和利润率修复可能形成共振"]),
        ("ai_technology_self_reliance", "energy_revolution_power_system", "dependent", ["算力增长增加电力需求", "数据中心依赖电网和能源基础设施"]),
        ("advanced_manufacturing", "ai_technology_self_reliance", "overlapping", ["机器人和工业自动化依赖AI与自主可控技术"]),
    ]
    return [{"source_theme": source, "target_theme": target, "relationship": relation, "confidence": 70, "reasons": reasons} for source, target, relation, reasons in candidates if source in ids and target in ids]


def compare_research_hypothesis(objects: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {item["theme_id"]: item for item in objects}
    agreements, partial, disagreements, reframing = [], [], [], []
    ai = by_id.get("ai_technology_self_reliance")
    if ai and ai["primary_mainline_class"] == "era_industrial":
        agreements.append("系统证据支持AI科技与自主可控属于长期时代产业主线。")
    elif ai:
        disagreements.append(f"AI科技与自主可控当前被识别为{CLASS_LABELS[ai['primary_mainline_class']]}。")
    resources, repair = by_id.get("reflation_resources"), by_id.get("anti_involution_profit_repair")
    if resources and repair and resources["primary_mainline_class"] != repair["primary_mainline_class"]:
        partial.append("资源品与反内卷存在共振，但证据期限和驱动不同。")
        reframing.append("将再通胀资源品与反内卷盈利修复拆分为宏观阶段主线和政策盈利修复主线。")
    energy, manufacturing = by_id.get("energy_revolution_power_system"), by_id.get("advanced_manufacturing")
    if energy and manufacturing and energy["primary_mainline_class"] != manufacturing["primary_mainline_class"]:
        reframing.append("将电力能源与先进制造拆分为时代产业主线和战略成长主线。")
    dividend = by_id.get("high_dividend_style")
    if dividend and dividend["primary_mainline_class"] == "allocation_style":
        agreements.append("高股息适合作为独立配置风格研究，不参与产业主线排名。")
    return {
        "research_hypothesis": {"first_mainline": "AI科技与自主可控", "second_mainline": "再通胀、资源品和反内卷盈利修复", "third_mainline": "电力能源与先进制造", "allocation_base": "高股息"},
        "system_assessment": {"agreements": agreements, "partial_agreements": partial, "disagreements": disagreements, "recommended_reframing": reframing},
    }


def enrich_phase3(payload: dict[str, Any]) -> dict[str, Any]:
    objects = build_research_objects(payload.get("theme_states") or [], basis_date=str(payload.get("basis_date") or ""))
    rankings = build_class_rankings(objects)
    payload.update({
        "scoring_version": "era_mainline_model_v3", "classification_version": "mainline_classification_v1",
        "structural_lifecycle_version": "structural_lifecycle_v1", "market_expression_lifecycle_version": "market_expression_lifecycle_v1",
        "research_objects": objects, "class_rankings": rankings, **rankings,
        "mainline_relationships": build_relationships(objects), "research_hypothesis_comparison": compare_research_hypothesis(objects),
    })
    for state in payload.get("theme_states") or []:
        state["market_expression_lifecycle"] = build_market_expression_lifecycle(state)
        state["market_expression_stage"] = state["market_expression_lifecycle"]["market_expression_stage"]
        state["market_expression_stage_confidence"] = state["market_expression_lifecycle"]["market_expression_stage_confidence"]
    payload["summary"] = _phase3_summary(rankings)
    return payload


def _phase3_summary(rankings: dict[str, list[dict[str, Any]]]) -> str:
    parts = []
    labels = [("era_industrial_ranking", "长期时代主线"), ("strategic_growth_ranking", "战略成长"), ("policy_profit_repair_ranking", "政策盈利修复"), ("macro_cycle_ranking", "宏观阶段"), ("allocation_style_ranking", "配置风格")]
    for key, label in labels:
        if rankings.get(key):
            parts.append(f"{label}：{rankings[key][0]['theme_name']}")
    return "；".join(parts) + "。结构生命周期与A股市场表达周期分别评估。"
