import asyncio
import json
import sys
from datetime import date
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from era_evidence_windows import evaluate_condition_window
from era_lifecycle_engine import apply_transition, condition_windows, enrich_lifecycle, lifecycle_dates, load_rules as load_lifecycle_rules, target_stage
from era_mainline_model import _status, determine_regime, load_rules as load_mainline_rules
from era_mainline_validator import validate
from generate_era_mainline_report import generate, render_markdown
from industry_validation import build_industry_dimension
from web.main import app


THRESHOLDS = {
    "absolute_mainline_score": 58,
    "policy_minimum": 50,
    "industry_confirmation": 50,
    "market_confirmation": 52,
    "emerging_score": 42,
    "declining_score": 38,
}


def get(path: str) -> httpx.Response:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path)

    return asyncio.run(run())


def snapshot(policy=0, market=0, industry=None, narrative=0, score=0, date="2026-01-01", events=2, secondary=2):
    return {
        "date": date, "observation_date": date, "policy_score": policy, "market_score": market,
        "industry_score": industry, "narrative_score": narrative, "era_mainline_score": score,
        "policy_dimension": {"event_count": events}, "narrative_dimension": {"active_subtheme_count": secondary},
    }


def test_policy_strong_without_industry_or_market_is_not_confirmed():
    stage, _ = target_stage(snapshot(policy=75, market=20, narrative=45, score=55))
    status = _status({**snapshot(policy=75, market=20, narrative=45, score=55), "lifecycle_stage": stage}, THRESHOLDS)
    assert stage == "incubating"
    assert status == "policy_theme_only"


def test_market_strong_without_policy_is_market_theme_only():
    status = _status({**snapshot(policy=20, market=80, narrative=50, score=60), "lifecycle_stage": "launching"}, THRESHOLDS)
    assert status == "market_theme_only"


def test_legacy_tail_with_policy_basis_is_preserved_as_old_mainline():
    status = _status({**snapshot(policy=32, market=45, narrative=20, score=35), "source_lifecycle_state": "legacy_tail", "lifecycle_stage": "launching"}, THRESHOLDS)
    assert status == "legacy_mainline"
    stage, _ = target_stage({**snapshot(policy=32, market=45, narrative=20, score=35), "source_lifecycle_state": "legacy_tail"}, "launching")
    assert stage == "cooling"


def test_multidimensional_confirmation_enters_confirmed_target():
    stage, _ = target_stage(snapshot(policy=72, market=65, industry=None, narrative=58, score=67), "launching")
    assert stage == "launching"


def test_first_policy_signal_does_not_jump_to_confirmation():
    history, transitions = enrich_lifecycle([
        {"theme_id": "t", "date": "2026-01-01", "confidence": 50, **snapshot(policy=25, market=5, score=20)},
    ])
    assert history[0]["lifecycle_stage"] == "incubating"
    assert transitions[0]["to_stage"] == "incubating"


def test_illegal_direct_stage_target_is_blocked_without_stepwise_advance():
    stage, direct = apply_transition("dormant", "mature")
    assert stage == "dormant"
    assert direct is False


def test_ended_theme_with_new_driver_restarts():
    history = [
        {**snapshot(policy=55, market=45, narrative=55, score=52, date="2026-01-01"), "new_policy_event_count": 2, "new_strong_policy_event_count": 2, "new_cycle_driver_ids": ["a", "b"]},
        {**snapshot(policy=55, market=45, narrative=55, score=52, date="2026-01-15"), "new_policy_event_count": 2, "new_strong_policy_event_count": 2, "new_cycle_driver_ids": ["a", "b"]},
    ]
    windows = condition_windows(history)
    stage, _ = target_stage(history[-1], "ended", windows=windows, history=history)
    assert stage == "restarting"


def test_missing_industry_data_is_unknown_not_zero():
    dimension = build_industry_dimension("ai_compute_communications", mapping={"themes": {"ai_compute_communications": ["capex"]}, "observations": {}})
    assert dimension["industry_validation_score"] is None
    assert dimension["industry_stage_label"] == "产业验证不足"


def test_left_censored_history_does_not_invent_start_date():
    event = {"event_id": "old", "event_date": "2025-12-01", "strength": 70, "direction": "supportive"}
    history = [snapshot(policy=70, market=70, narrative=55, score=68, date="2026-01-01"), snapshot(policy=70, market=70, narrative=55, score=68, date="2026-01-15")]
    for item in history:
        item["policy_dimension"]["events"] = [event]
    enriched, _ = enrich_lifecycle(history)
    dates = lifecycle_dates(enriched, [{"event_date": "2025-12-01"}], "2026-01-15")
    assert dates["history_coverage"]["is_left_censored"] is True
    assert dates["estimated_start_date"] == ""
    assert dates["start_date_status"] == "before_available_history"


def _confirmed_history(dates):
    return [snapshot(policy=70, market=65, narrative=55, score=65, date=value) for value in dates]


def test_report_frequency_does_not_accelerate_confirmation():
    daily = _confirmed_history([f"2026-01-{day:02d}" for day in range(1, 16)])
    weekly = _confirmed_history(["2026-01-01", "2026-01-08", "2026-01-15"])
    missing = _confirmed_history(["2026-01-01", "2026-01-04", "2026-01-15"])
    for history in (daily, weekly, missing):
        window = condition_windows(history)["confirmation"]
        assert window["first_qualified_date"] == "2026-01-15"
        assert window["first_qualified_start_date"] == "2026-01-01"


def test_single_confirmation_observation_cannot_confirm():
    history = _confirmed_history(["2026-01-01"]) + [snapshot(policy=40, market=20, score=35, date="2026-01-16")]
    enriched, _ = enrich_lifecycle(history)
    assert all(row["evidence_stage"] != "confirmed" for row in enriched)


def test_confirmation_requires_count_and_calendar_duration():
    short = condition_windows(_confirmed_history(["2026-01-01", "2026-01-02"]))["confirmation"]
    sustained = condition_windows(_confirmed_history(["2026-01-01", "2026-01-15"]))["confirmation"]
    assert short["currently_met"] is False
    assert sustained["currently_met"] is True


def test_window_resets_after_condition_break():
    rows = [{"date": "2026-01-01", "ok": True}, {"date": "2026-01-15", "ok": False}, {"date": "2026-01-20", "ok": True}]
    window = evaluate_condition_window(rows, lambda row, _: row["ok"], 1, 10)
    assert window["first_met_date"] == "2026-01-20"
    assert window["duration_days"] == 0
    assert window["currently_met"] is False


def test_lifecycle_configuration_changes_results():
    rules = load_lifecycle_rules()
    history = _confirmed_history(["2026-01-01", "2026-01-15"])
    assert condition_windows(history, rules=rules)["confirmation"]["currently_met"] is True
    stricter = json.loads(json.dumps(rules))
    stricter["confirmation"]["minimum_duration_days"] = 20
    assert condition_windows(history, rules=stricter)["confirmation"]["currently_met"] is False
    stricter = json.loads(json.dumps(rules))
    stricter["confirmation"]["minimum_consecutive_observations"] = 3
    assert condition_windows(history, rules=stricter)["confirmation"]["currently_met"] is False


def _regime_state(score, qualification="confirmed_candidate", confidence=70, stability=0.9, ranks=(1, 1), stage="confirmed"):
    return {
        "era_mainline_score": score, "mainline_qualification": qualification, "current_state_confidence": confidence,
        "rank_stability": stability, "data_coverage": 0.75, "evidence_stage": stage,
        "score_history": [{"era_rank": rank} for rank in ranks],
    }


def test_regime_uses_dominance_dual_multi_rotation_transition_and_no_clear_rules():
    thresholds = load_mainline_rules()["thresholds"]
    assert determine_regime([_regime_state(75)], thresholds) == "single_dominant"
    assert determine_regime([_regime_state(75), _regime_state(70)], thresholds) == "dual_mainline"
    assert determine_regime([_regime_state(75), _regime_state(70), _regime_state(63)], thresholds) == "multi_mainline"
    rotating = [_regime_state(75, stability=0.3, ranks=(1, 2, 1)), _regime_state(73, stability=0.3, ranks=(2, 1, 2))]
    assert determine_regime(rotating, thresholds) == "rotation"
    transition = [_regime_state(45, "legacy_mainline", stage="cooling"), _regime_state(55, "emerging_candidate", stage="launching")]
    assert determine_regime(transition, thresholds) == "transition"
    assert determine_regime([_regime_state(55, "emerging_candidate", stage="launching")], thresholds) == "no_clear_mainline"
    wider_gap = json.loads(json.dumps(thresholds))
    wider_gap["dual_mainline_gap"] = 3
    assert determine_regime([_regime_state(75), _regime_state(70)], wider_gap) == "rotation"


def test_generated_report_contains_required_research_sections():
    payload, _, _ = generate(write=False)
    markdown = render_markdown(payload)
    for heading in ("## 当前结论", "## 当前主线格局", "## 四维证据拆解", "## 反面证据", "## 失效条件", "## 数据不足和不确定性"):
        assert heading in markdown
    assert payload["mainline_regime"] in {"single_dominant", "dual_mainline", "multi_mainline", "rotation", "transition", "no_clear_mainline", "data_insufficient"}
    assert validate(payload)["error_count"] == 0
    for theme in payload["theme_states"]:
        dates = [point["date"] for point in theme["score_history"]]
        assert len(dates) == len(set(dates))
        assert theme["industry_score"] is None
        assert theme["current_state_confidence"] <= 75
        assert theme["lifecycle_date_confidence"] <= 60
        assert theme["era_mainline_confidence"] == theme["current_state_confidence"]
        assert theme["configured_dimension_weights"] == {"policy": 0.4, "industry": 0.25, "market": 0.25, "narrative": 0.1}
        assert theme["effective_dimension_weights"]["industry"] == 0
        if theme["mainline_qualification"] in {"primary_era_mainline", "secondary_era_mainline"}:
            assert theme["evidence_stage"] in {"confirmed", "expanding", "mature"}
        if theme["mainline_qualification"] == "legacy_mainline":
            assert theme["evidence_stage"] in {"cooling", "declining", "ended"}
    assert payload["history_semantics"]["type"] == "retrospective_replay"
    assert all(payload["rule_usage"].values())


def test_era_apis_and_pages_are_read_only_and_complete():
    latest = get("/api/era-mainline/latest")
    assert latest.status_code == 200
    body = latest.json()
    assert {"report_id", "basis_date", "mainline_regime", "primary_mainline", "secondary_mainline", "emerging_candidates", "declining_mainlines", "theme_states", "data_coverage", "summary"} <= set(body)
    theme_id = body["theme_states"][0]["theme_id"]
    for path in (
        "/api/era-mainline/ranking", "/api/era-mainline/regime", "/api/era-mainline/history", "/api/era-mainline/transitions",
        f"/api/era-mainline/theme/{theme_id}", f"/api/era-mainline/theme/{theme_id}/timeline",
        f"/api/era-mainline/theme/{theme_id}/evidence", f"/api/era-mainline/theme/{theme_id}/invalidation",
        "/era-mainline", f"/era-mainline/{theme_id}", "/era-timeline", "/era-transitions",
    ):
        assert get(path).status_code == 200, path


def test_history_and_transitions_are_ordered():
    history_body = get("/api/era-mainline/history").json()
    history = history_body["history"]
    transitions = get("/api/era-mainline/transitions").json()["transitions"]
    assert history == sorted(history, key=lambda item: (item["date"], item["theme_id"], item["report_id"]))
    assert transitions == sorted(transitions, key=lambda item: (item["change_date"], item["theme_id"]))
    assert history_body["milestones"]
    assert {"first_top3_date", "first_launching_date", "first_confirmation_date", "highest_score_date", "cooling_start_date", "ended_date", "restarting_date"} <= set(next(iter(history_body["milestones"].values())))


def test_era_read_only_apis_do_not_write_report_files():
    report_dir = ROOT / "research" / "era_mainline"
    before = {path.name: path.stat().st_mtime_ns for path in report_dir.glob("*")}
    body = get("/api/era-mainline/latest").json()
    theme_id = body["theme_states"][0]["theme_id"]
    for path in ("/api/era-mainline/history", "/api/era-mainline/transitions", f"/api/era-mainline/theme/{theme_id}/evidence"):
        assert get(path).status_code == 200
    after = {path.name: path.stat().st_mtime_ns for path in report_dir.glob("*")}
    assert before == after
