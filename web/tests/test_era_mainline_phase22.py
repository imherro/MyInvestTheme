import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from era_lifecycle_engine import analyze_reinforcements, enrich_lifecycle, lifecycle_dates, load_rules
from era_mainline_model import CONFIDENCE_RULES_PATH, _confidence_values
from era_mainline_validator import validate
from generate_era_mainline_report import generate
from era_transition_policy import decide_transition


def event(event_id, date, *, strength=70, direction="supportive"):
    return {"event_id": event_id, "event_date": date, "strength": strength, "execution_level": 65, "direction": direction, "title": event_id}


def point(date, *, policy=70, market=65, industry=None, narrative=55, score=65, events=None, changes=None):
    policy_events = events if events is not None else [event("a", date), event("b", date)]
    return {
        "theme_id": "t", "date": date, "observation_date": date,
        "policy_score": policy, "market_score": market, "industry_score": industry,
        "narrative_score": narrative, "era_mainline_score": score,
        "policy_dimension": {"event_count": len(policy_events), "events": policy_events, "restrictive_event_count": sum(item["direction"] == "restrictive" for item in policy_events)},
        "narrative_dimension": {"active_subtheme_count": 2},
        "dimension_changes": changes or {},
    }


def decision(previous, desired, **kwargs):
    return decide_transition(previous, desired, observation_date=kwargs.pop("date", "2026-02-01"), stage_entered_at=kwargs.pop("entered", "2026-01-01"), rules=kwargs.pop("rules", load_rules()), **kwargs)


def test_forbidden_transitions_are_blocked_without_adjacent_advance():
    for previous, desired in (("dormant", "cooling"), ("dormant", "ended"), ("ended", "confirmed"), ("declining", "launching")):
        result = decision(previous, desired, target_condition_satisfied=True, recovery_satisfied=True)
        assert result["transition_allowed"] is False
        assert result["actual_stage"] == previous
        assert result["transition_type"] == "blocked"


def test_sustained_evidence_can_jump_incubating_to_confirmed():
    result = decision("incubating", "confirmed", target_condition_satisfied=True)
    assert result["actual_stage"] == "confirmed"
    assert result["transition_type"] == "direct_evidence_jump"
    assert result["skipped_stages"] == ["emerging", "launching"]


def test_stage_dwell_keeps_launching_during_early_marginal_weakness():
    history = [point("2026-01-01", score=70, market=70), point("2026-01-03", score=67, market=67)]
    enriched, _ = enrich_lifecycle(history)
    assert enriched[-1]["evidence_stage"] == "launching"
    assert enriched[-1]["momentum_state"] in {"marginally_weakening", "stable"}


def test_severe_reverse_evidence_can_override_dwell():
    history = [point("2026-01-01", score=70, market=70), point("2026-01-02", policy=20, market=20, narrative=20, score=25)]
    enriched, transitions = enrich_lifecycle(history)
    assert enriched[-1]["evidence_stage"] == "cooling"
    last = transitions[-1]
    assert last["dwell_override"] is True
    assert "SEVERE_REVERSE_EVIDENCE_OVERRIDE" in last["transition_reason_codes"]


def test_marginal_weakness_does_not_become_cooling():
    history = [point("2026-01-01", score=70, market=70), point("2026-01-08", score=67, market=68), point("2026-01-15", score=68, market=69)]
    enriched, _ = enrich_lifecycle(history)
    assert enriched[-1]["evidence_stage"] != "cooling"
    assert enriched[1]["momentum_state"] in {"marginally_weakening", "stable"}


def test_cooling_requires_sustained_drop_and_sustained_recovery_to_exit():
    history = [
        point("2026-01-01", score=70, market=70), point("2026-01-08", score=57, market=55),
        point("2026-01-15", score=56, market=54), point("2026-01-18", score=62, market=60),
        point("2026-01-22", score=65, market=63), point("2026-01-29", score=66, market=64),
    ]
    enriched, _ = enrich_lifecycle(history)
    by_date = {item["date"]: item for item in enriched}
    assert by_date["2026-01-15"]["evidence_stage"] == "cooling"
    assert by_date["2026-01-18"]["evidence_stage"] == "cooling"
    assert by_date["2026-01-29"]["evidence_stage"] in {"launching", "emerging"}


def test_reinforcement_thresholds_types_and_policy_event_date():
    tiny = [point("2026-07-20"), point("2026-07-21", changes={"policy_score": 0.1, "market_score": 0.5, "era_mainline_score": 0.5})]
    assert analyze_reinforcements(tiny, [event("p", "2026-06-01")])["latest_reinforcement_type"] == "none"
    multi = [point("2026-07-20"), point("2026-07-21", changes={"policy_score": 3.2, "market_score": 4.5, "era_mainline_score": 5.2})]
    result = analyze_reinforcements(multi, [])
    assert result["latest_reinforcement_type"] == "multi_dimension"
    assert result["latest_reinforcement_date"] == "2026-07-21"
    policy = analyze_reinforcements([point("2026-07-01")], [event("new-policy", "2026-07-18")])
    assert policy["latest_policy_reinforcement_event_date"] == "2026-07-18"
    assert policy["latest_reinforcement_date"] == "2026-07-18"
    assert policy["latest_reinforcement_type"] == "policy_event"


def test_market_reinforcement_reason_does_not_claim_policy_event():
    history = [point("2026-07-01"), point("2026-07-02", changes={"market_score": 5, "era_mainline_score": 2})]
    result = analyze_reinforcements(history, [])
    assert result["latest_reinforcement_type"] == "market_confirmation"
    assert all("政策事件" not in reason for reason in result["latest_reinforcement_reasons"])


def test_left_censoring_requires_advanced_state_and_old_evidence():
    low = [point("2026-01-01", policy=35, market=20, score=40, events=[event("recent", "2026-01-01")]), point("2026-01-15", policy=35, market=20, score=40)]
    enriched, _ = enrich_lifecycle(low)
    assert enriched[-1]["is_left_censored"] is False
    old_events = [event("old-a", "2025-12-01"), event("old-b", "2025-12-01")]
    high = [point("2026-01-01", score=68, market=70, events=old_events), point("2026-01-15", score=68, market=70, events=old_events)]
    enriched, _ = enrich_lifecycle(high)
    assert enriched[-1]["is_left_censored"] is True
    assert enriched[-1]["left_censoring_reasons"]
    assert enriched[-1]["left_censoring_evidence"]


def test_ending_is_anchored_to_formal_declining_start():
    history = [
        point("2026-01-01", score=70, market=70),
        point("2026-01-15", score=70, market=70),
        point("2026-02-01", policy=20, market=20, narrative=20, score=25),
        point("2026-02-15", policy=20, market=20, narrative=20, score=25),
        point("2026-03-01", policy=20, market=20, narrative=20, score=25),
        point("2026-03-17", policy=20, market=20, narrative=20, score=25),
    ]
    enriched, _ = enrich_lifecycle(history)
    by_date = {item["date"]: item for item in enriched}
    assert by_date["2026-03-01"]["evidence_stage"] == "declining"
    assert by_date["2026-03-17"]["evidence_stage"] == "ended"
    dates = lifecycle_dates(enriched, [], "2026-03-17")
    assert dates["declining_start_date"] == "2026-02-01"
    assert dates["declining_decided_at"] == "2026-02-15"
    assert dates["estimated_end_date"] == "2026-03-17"


def test_ended_market_bounce_stays_ended_and_new_driver_opens_second_cycle():
    old = [event("a", "2026-01-01"), event("b", "2026-01-01")]
    new = old + [event("c", "2026-04-15"), event("d", "2026-04-15")]
    history = [
        point("2026-01-01", score=70, market=70, events=old), point("2026-01-15", score=70, market=70, events=old),
        point("2026-02-01", policy=20, market=20, narrative=20, score=25, events=old), point("2026-02-15", policy=20, market=20, narrative=20, score=25, events=old),
        point("2026-03-01", policy=20, market=20, narrative=20, score=25, events=old), point("2026-03-17", policy=20, market=20, narrative=20, score=25, events=old),
        point("2026-04-01", policy=20, market=55, narrative=20, score=38, events=old),
        point("2026-04-15", policy=50, market=45, narrative=45, score=48, events=new), point("2026-04-29", policy=50, market=45, narrative=45, score=48, events=new),
    ]
    enriched, transitions = enrich_lifecycle(history)
    by_date = {item["date"]: item for item in enriched}
    assert by_date["2026-04-01"]["evidence_stage"] == "ended"
    assert by_date["2026-04-29"]["evidence_stage"] == "restarting"
    assert by_date["2026-04-29"]["cycle_sequence"] == 2
    assert by_date["2026-04-29"]["cycle_id"] != by_date["2026-03-17"]["cycle_id"]
    assert by_date["2026-04-29"]["cycle_peak_score"] == 48
    assert any(item["transition_type"] == "restart" for item in transitions)


def test_configuration_changes_dwell_recovery_reinforcement_censoring_and_ending():
    rules = load_rules()
    short = decision("launching", "cooling", date="2026-01-03", entered="2026-01-01", rules=rules, target_condition_satisfied=True)
    assert short["transition_allowed"] is False
    no_dwell = json.loads(json.dumps(rules)); no_dwell["minimum_stage_dwell_days"]["launching"] = 0
    assert decision("launching", "cooling", date="2026-01-03", entered="2026-01-01", rules=no_dwell, target_condition_satisfied=True)["transition_allowed"] is True
    strict_reinforcement = json.loads(json.dumps(rules)); strict_reinforcement["reinforcement"]["minimum_composite_score_increase"] = 10
    history = [point("2026-07-01"), point("2026-07-02", changes={"era_mainline_score": 6})]
    assert analyze_reinforcements(history, [], rules=rules)["latest_reinforcement_type"] == "composite_score"
    assert analyze_reinforcements(history, [], rules=strict_reinforcement)["latest_reinforcement_type"] == "none"
    lenient_censor = json.loads(json.dumps(rules)); lenient_censor["left_censoring"]["minimum_pre_history_evidence_days"] = 5
    events = [event("a", "2025-12-22"), event("b", "2025-12-22")]
    history = [point("2026-01-01", score=68, market=70, events=events)]
    assert enrich_lifecycle(history, rules=rules)[0][-1]["is_left_censored"] is False
    assert enrich_lifecycle(history, rules=lenient_censor)[0][-1]["is_left_censored"] is True


def test_cooling_exit_and_ending_configuration_change_stage_output():
    rules = load_rules()
    cooling_history = [
        point("2026-01-01", score=70, market=70), point("2026-01-08", score=57, market=55),
        point("2026-01-15", score=56, market=54), point("2026-01-22", score=65, market=63), point("2026-01-29", score=66, market=64),
    ]
    assert enrich_lifecycle(cooling_history, rules=rules)[0][-1]["evidence_stage"] in {"launching", "emerging"}
    strict_exit = json.loads(json.dumps(rules)); strict_exit["hysteresis"]["cooling_exit"]["minimum_duration_days"] = 14
    assert enrich_lifecycle(cooling_history, rules=strict_exit)[0][-1]["evidence_stage"] == "cooling"
    declining_history = [
        point("2026-01-01", score=70, market=70), point("2026-01-15", score=70, market=70),
        point("2026-02-01", policy=20, market=20, narrative=20, score=25), point("2026-02-15", policy=20, market=20, narrative=20, score=25),
        point("2026-03-01", policy=20, market=20, narrative=20, score=25), point("2026-03-17", policy=20, market=20, narrative=20, score=25),
    ]
    assert enrich_lifecycle(declining_history, rules=rules)[0][-1]["evidence_stage"] == "ended"
    strict_end = json.loads(json.dumps(rules)); strict_end["ending"]["minimum_days_after_declining_start"] = 60
    assert enrich_lifecycle(declining_history, rules=strict_end)[0][-1]["evidence_stage"] == "declining"


def test_stage_confidence_penalizes_frequent_changes_and_blocked_targets():
    confidence_rules = json.loads(CONFIDENCE_RULES_PATH.read_text(encoding="utf-8"))
    base = {
        "policy_score": 70, "industry_score": 60, "market_score": 65, "narrative_score": 55,
        "era_mainline_score": 65, "data_coverage": 1.0, "conflicts": [], "point_in_time_status": "verified",
        "score_history": [{"era_rank": 1}] * 6, "history_coverage": {"coverage_days": 90, "maximum_observation_gap_days": 7},
        "condition_windows": {"confirmation": {"consecutive_observations": 6}}, "stage_dwell_satisfied": True,
        "stage_stability_score": 90, "stage_changes_14d": 0, "stage_history": [],
    }
    stable = _confidence_values(base, confidence_rules)["lifecycle_stage_confidence"]
    unstable_state = {**base, "stage_stability_score": 30, "stage_changes_14d": 5, "stage_history": [{"transition_allowed": False}]}
    unstable = _confidence_values(unstable_state, confidence_rules)["lifecycle_stage_confidence"]
    assert unstable < stable


def test_current_report_has_no_illegal_transitions_and_runtime_rule_usage():
    payload, _, _ = generate(write=False)
    forbidden = {("dormant", "cooling"), ("dormant", "declining"), ("dormant", "ended"), ("ended", "confirmed"), ("ended", "expanding"), ("declining", "launching")}
    assert not any(item.get("transition_allowed") and (item["from_stage"], item["to_stage"]) in forbidden for item in payload["transitions"])
    usage = payload["rule_usage"]["era_lifecycle_rules_v3"]
    assert "all_lifecycle_rule_fields" not in usage
    assert all(item["access_count"] > 0 for item in usage.values())
    assert validate(payload)["error_count"] == 0
