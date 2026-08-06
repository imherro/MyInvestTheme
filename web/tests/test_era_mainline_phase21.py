import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from era_lifecycle_engine import condition_windows, enrich_lifecycle, load_rules
from era_mainline_model import CONFIDENCE_RULES_PATH, _confidence_values
from era_mainline_validator import validate
from narrative_momentum import build_narrative_dimension


def row(date, *, policy=70, market=65, industry=None, narrative=55, score=65, events=2, secondary=2):
    return {
        "date": date,
        "observation_date": date,
        "policy_score": policy,
        "market_score": market,
        "industry_score": industry,
        "narrative_score": narrative,
        "era_mainline_score": score,
        "policy_dimension": {"event_count": events},
        "narrative_dimension": {"active_subtheme_count": secondary},
    }


def test_formation_event_and_duration_rules_are_active():
    rules = load_rules()
    history = [row("2026-01-01"), row("2026-01-15")]
    assert condition_windows(history, rules=rules)["formation"]["currently_met"] is True
    fewer_events = [row("2026-01-01", events=1), row("2026-01-15", events=1)]
    assert condition_windows(fewer_events, rules=rules)["formation"]["currently_met"] is False
    stricter = json.loads(json.dumps(rules))
    stricter["formation"]["minimum_duration_days"] = 20
    assert condition_windows(history, rules=stricter)["formation"]["currently_met"] is False


def test_expansion_rules_are_active():
    rules = load_rules()
    history = [row("2026-01-01", market=70, narrative=60, secondary=3), row("2026-01-15", market=70, narrative=60, secondary=3)]
    assert condition_windows(history, rules=rules)["expansion"]["currently_met"] is True
    stricter = json.loads(json.dumps(rules))
    stricter["expansion"]["minimum_secondary_theme_count"] = 4
    assert condition_windows(history, rules=stricter)["expansion"]["currently_met"] is False


def test_cooling_market_drop_rule_is_active():
    rules = load_rules()
    history = [row("2026-01-01", market=80), row("2026-01-08", market=65), row("2026-01-15", market=64)]
    for item in history:
        item.update({"cycle_id": "t_cycle_001", "cycle_peak_score": 75, "cycle_peak_market_score": 80})
    assert condition_windows(history, rules=rules)["cooling"]["currently_met"] is True
    stricter = json.loads(json.dumps(rules))
    stricter["cooling"]["minimum_market_score_drop"] = 25
    stricter["cooling"]["minimum_peak_score_drop"] = 25
    assert condition_windows(history, rules=stricter)["cooling"]["currently_met"] is False


def test_declining_and_ending_duration_rules_are_active():
    rules = load_rules()
    history = [row("2026-01-01", policy=20, market=20, narrative=20, score=25), row("2026-01-15", policy=20, market=20, narrative=20, score=25), row("2026-02-01", policy=20, market=20, narrative=20, score=25)]
    windows = condition_windows(history, rules=rules)
    assert windows["declining"]["currently_met"] is True
    assert windows["ending"]["currently_met"] is True
    stricter = json.loads(json.dumps(rules))
    stricter["ending"]["minimum_consecutive_observations"] = 4
    assert condition_windows(history, rules=stricter)["ending"]["currently_met"] is False


def test_maturity_duration_and_growth_rules_are_active():
    rules = load_rules()
    history = [row("2026-01-01", narrative=40), row("2026-04-02", narrative=40), row("2026-07-02", narrative=40)]
    enriched, _ = enrich_lifecycle(history, rules=rules)
    assert enriched[-1]["evidence_stage"] == "mature"
    stricter = json.loads(json.dumps(rules))
    stricter["maturity"]["minimum_confirmed_duration_days"] = 120
    enriched, _ = enrich_lifecycle(history, rules=stricter)
    assert enriched[-1]["evidence_stage"] == "confirmed"


def _confidence_state(*, coverage=0.75, industry=None, pit="degraded", observations=3, coverage_days=70, gap=7):
    history = [{"era_rank": 1} for _ in range(observations)]
    return {
        "policy_score": 75,
        "industry_score": industry,
        "market_score": 65,
        "narrative_score": 55,
        "era_mainline_score": 68,
        "data_coverage": coverage,
        "conflicts": [],
        "score_history": history,
        "history_coverage": {"coverage_days": coverage_days, "maximum_observation_gap_days": gap},
        "condition_windows": {"confirmation": {"consecutive_observations": observations}},
        "point_in_time_status": pit,
    }


def test_confidence_caps_and_coverage_improvement():
    rules = json.loads(CONFIDENCE_RULES_PATH.read_text(encoding="utf-8"))
    degraded = _confidence_values(_confidence_state(observations=1, coverage_days=0, gap=20), rules)
    improved = _confidence_values(_confidence_state(industry=60, pit="verified", coverage=1.0, observations=5, coverage_days=90), rules)
    assert degraded["current_state_confidence"] <= 75
    assert degraded["lifecycle_stage_confidence"] <= 55
    assert degraded["lifecycle_date_confidence"] <= 60
    assert improved["current_state_confidence"] > degraded["current_state_confidence"]
    assert improved["lifecycle_stage_confidence"] > degraded["lifecycle_stage_confidence"]


def test_official_narrative_does_not_score_event_frequency():
    event = {"source_org_norm": "国家发展改革委", "primary_policy_title": "人工智能战略行动规划"}
    one = build_narrative_dimension({"all_event_contributors": [event]}, [])
    repeated = build_narrative_dimension({"all_event_contributors": [event] * 10}, [])
    assert one["official_narrative_diffusion_score"] == repeated["official_narrative_diffusion_score"]
    assert repeated["narrative_frequency"] is None
    assert "不代表社会舆论热度" in repeated["semantics"]


def test_validator_rejects_date_and_status_conflicts():
    theme = {
        "theme_id": "t", "theme_name": "T", "era_mainline_score": 60, "era_rank": 1,
        "era_mainline_status": "primary_era_mainline", "mainline_qualification": "primary_era_mainline",
        "lifecycle_stage": "launching", "evidence_stage": "launching", "lifecycle_confidence": 50,
        "current_state_confidence": 50, "lifecycle_stage_confidence": 50, "lifecycle_date_confidence": 40,
        "history_coverage": {}, "condition_windows": {}, "effective_dimension_weights": {},
        "supporting_evidence": [], "contradicting_evidence": [], "invalidating_conditions": [],
        "score_history": [], "stage_history": [], "industry_score": None,
        "estimated_start_date": "2026-02-01", "confirmation_date": "2026-01-01",
    }
    payload = {
        "report_id": "r", "basis_date": "2026-02-01", "mainline_regime": "single_dominant",
        "primary_mainline": theme, "secondary_mainline": None, "emerging_candidates": [], "declining_mainlines": [],
        "theme_states": [theme], "data_coverage": {}, "history_semantics": {}, "summary": "",
        "rule_usage": {key: True for key in ("formation.minimum_duration_days", "confirmation.minimum_consecutive_observations", "confirmation.minimum_duration_days", "cooling.minimum_market_score_drop", "dominance_gap", "dual_mainline_gap", "all_lifecycle_rule_fields")},
    }
    codes = {item["code"] for item in validate(payload)["errors"]}
    assert "ERA_DATE_ORDER_INVALID" in codes
    assert "QUALIFICATION_STAGE_CONFLICT" in codes


def test_duplicate_basis_date_keeps_final_observation():
    history = [row("2026-01-01", score=40), {**row("2026-01-01", score=70), "observation_id": "z"}, row("2026-01-15", score=70)]
    enriched, _ = enrich_lifecycle(history)
    assert len(enriched) == 2
    assert enriched[0]["era_mainline_score"] == 70
