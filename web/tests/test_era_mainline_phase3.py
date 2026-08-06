import sys
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from allocation_style_model import evaluate_high_dividend
from era_mainline_validator import validate
from generate_era_mainline_report import generate
from mainline_classification import classify_mainline, profile_for
from mainline_phase3 import build_class_rankings, build_research_objects, compare_research_hypothesis
from structural_validation import build_market_expression_lifecycle, build_structural_indicators, evaluate_structural_lifecycle


def classified(object_id, **changes):
    features = profile_for(object_id)
    features.update(changes)
    return classify_mainline(features)


def test_ai_is_era_industrial_even_when_market_is_weak():
    result = classified("ai_technology_self_reliance", market_intensity=20, industry_data_available=100)
    assert result["primary_mainline_class"] == "era_industrial"


def test_short_commodity_spike_cannot_become_era_industrial():
    result = classify_mainline({"commodity_price_dependency": 95, "event_dependency": 85, "market_intensity": 95, "macro_sensitivity": 55, "technology_paradigm": 5, "industrial_capex": 10, "industry_penetration": 5})
    assert result["primary_mainline_class"] in {"macro_cycle", "trading_branch"}
    assert result["primary_mainline_class"] != "era_industrial"


def test_reflation_resources_is_macro_cycle_with_independent_scores():
    result = classified("reflation_resources", market_intensity=92, industry_data_available=100)
    assert result["primary_mainline_class"] == "macro_cycle"
    assert result["class_scores"]["macro_cycle"] > result["class_scores"]["era_industrial"]


def test_anti_involution_is_policy_profit_repair():
    result = classified("anti_involution_profit_repair", industry_data_available=100)
    assert result["primary_mainline_class"] == "policy_profit_repair"


def test_advanced_manufacturing_is_strategic_growth_before_mature_commercialization():
    result = classified("advanced_manufacturing", industry_data_available=0)
    assert result["primary_mainline_class"] == "strategic_growth"


def test_high_dividend_is_allocation_style():
    result = classified("high_dividend_style", industry_data_available=0)
    style = evaluate_high_dividend(result["feature_scores"], market_score=50)
    assert result["primary_mainline_class"] == "allocation_style"
    assert style["allocation_style_score"] > 0


def test_structural_and_market_lifecycles_are_decoupled():
    features = profile_for("ai_technology_self_reliance")
    indicators = build_structural_indicators("ai", features, data_date="2026-08-05", industry_available=True)
    structural = evaluate_structural_lifecycle("era_industrial", features, indicators, history_days=400)
    market = build_market_expression_lifecycle({"evidence_stage": "cooling", "lifecycle_stage_confidence": 70, "date": "2026-08-05", "cycle_start_observation_date": "2026-06-01"})
    assert structural["structural_stage"] in {"industrial_buildout", "commercial_validation", "penetration_expansion"}
    assert market["market_expression_stage"] == "cooling"


def test_market_rally_does_not_change_resource_class():
    low = classified("reflation_resources", market_intensity=20)
    high = classified("reflation_resources", market_intensity=100)
    assert low["primary_mainline_class"] == high["primary_mainline_class"] == "macro_cycle"


def test_long_term_market_decline_does_not_end_growing_structure():
    features = profile_for("ai_technology_self_reliance")
    indicators = build_structural_indicators("ai", features, industry_available=True)
    structural = evaluate_structural_lifecycle("era_industrial", features, indicators, history_days=500)
    market = build_market_expression_lifecycle({"evidence_stage": "declining", "date": "2026-08-05", "cycle_start_observation_date": "2026-01-01"})
    assert structural["structural_stage"] != "structural_decline"
    assert market["market_expression_stage"] == "declining"


def test_type_specific_end_rules_and_time_horizons():
    cases = {}
    for kind, object_id in (("era_industrial", "ai_technology_self_reliance"), ("macro_cycle", "reflation_resources"), ("trading_branch", "consumer_culture_branch")):
        features = profile_for(object_id)
        indicators = build_structural_indicators(object_id, features, industry_available=True)
        cases[kind] = evaluate_structural_lifecycle(kind, features, indicators, history_days=500)
    assert cases["era_industrial"]["time_horizon"]["unit"] == "years"
    assert cases["macro_cycle"]["time_horizon"]["unit"] == "months"
    assert cases["trading_branch"]["time_horizon"]["unit"] == "days"
    assert cases["era_industrial"]["end_rule"]["minimum_decline_days"] > cases["macro_cycle"]["end_rule"]["minimum_decline_days"] > cases["trading_branch"]["end_rule"]["minimum_decline_days"]
    assert cases["trading_branch"]["end_rule"]["market_decline_alone_can_end"] is True


def test_real_report_has_split_objects_and_independent_style_ranking():
    payload, _, _ = generate(write=False)
    ids = {item["theme_id"] for item in payload["research_objects"]}
    assert {"reflation_resources", "anti_involution_profit_repair", "energy_revolution_power_system", "advanced_manufacturing", "high_dividend_style"} <= ids
    industrial_ids = {item["theme_id"] for key in ("era_industrial_ranking", "strategic_growth_ranking", "policy_profit_repair_ranking", "macro_cycle_ranking") for item in payload[key]}
    assert "high_dividend_style" not in industrial_ids
    assert [item["theme_id"] for item in payload["allocation_style_ranking"]] == ["high_dividend_style"]


def test_real_report_expected_classification_and_dual_lifecycle():
    payload, _, _ = generate(write=False)
    by_id = {item["theme_id"]: item for item in payload["research_objects"]}
    assert by_id["ai_technology_self_reliance"]["primary_mainline_class"] == "era_industrial"
    assert by_id["energy_revolution_power_system"]["primary_mainline_class"] == "era_industrial"
    assert by_id["advanced_manufacturing"]["primary_mainline_class"] == "strategic_growth"
    assert by_id["reflation_resources"]["primary_mainline_class"] == "macro_cycle"
    assert by_id["anti_involution_profit_repair"]["primary_mainline_class"] == "policy_profit_repair"
    assert by_id["ai_technology_self_reliance"]["structural_stage"] != "structural_decline"
    assert by_id["ai_technology_self_reliance"]["structural_lifecycle"] != by_id["ai_technology_self_reliance"]["market_expression_lifecycle"]


def test_hypothesis_comparison_is_derived_from_classification():
    payload, _, _ = generate(write=False)
    assessment = payload["research_hypothesis_comparison"]["system_assessment"]
    assert assessment["agreements"]
    assert any("拆分" in item for item in assessment["recommended_reframing"])
    modified = [dict(item) for item in payload["research_objects"]]
    next(item for item in modified if item["theme_id"] == "ai_technology_self_reliance")["primary_mainline_class"] = "unclassified"
    changed = compare_research_hypothesis(modified)["system_assessment"]
    assert changed["disagreements"]


def test_validator_rejects_style_in_industrial_ranking_and_coupled_lifecycle():
    payload, _, _ = generate(write=False)
    style = payload["allocation_style_ranking"][0]
    payload["era_industrial_ranking"].append(style)
    ai = next(item for item in payload["research_objects"] if item["theme_id"] == "ai_technology_self_reliance")
    ai["market_expression_lifecycle"] = dict(ai["structural_lifecycle"])
    result = validate(payload)
    codes = {item["code"] for item in result["errors"]}
    assert "ALLOCATION_STYLE_IN_MAINLINE_RANKING" in codes
    assert "MAINLINE_CLASS_RANKING_CONFLICT" in codes
    assert "STRUCTURAL_MARKET_STAGE_COUPLED" in codes


def test_current_report_validates_without_errors():
    payload, _, _ = generate(write=False)
    assert validate(payload)["error_count"] == 0


def test_phase3_research_object_detail_pages_render():
    from web.main import app

    client = TestClient(app)
    for object_id in ("ai_technology_self_reliance", "high_dividend_style"):
        response = client.get(f"/era-mainline/{object_id}")
        assert response.status_code == 200
        assert "结构生命周期" in response.text
