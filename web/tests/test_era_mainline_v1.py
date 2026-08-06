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

from era_lifecycle_engine import apply_transition, enrich_lifecycle, lifecycle_dates, target_stage
from era_mainline_model import _status
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


def snapshot(policy=0, market=0, industry=None, narrative=0, score=0):
    return {"policy_score": policy, "market_score": market, "industry_score": industry, "narrative_score": narrative, "era_mainline_score": score}


def test_policy_strong_without_industry_or_market_is_not_confirmed():
    stage, _ = target_stage(snapshot(policy=75, market=20, narrative=45, score=55))
    status = _status({**snapshot(policy=75, market=20, narrative=45, score=55), "lifecycle_stage": stage}, THRESHOLDS)
    assert stage == "emerging"
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
    assert stage == "confirmed"


def test_first_policy_signal_does_not_jump_to_confirmation():
    history, transitions = enrich_lifecycle([
        {"theme_id": "t", "date": "2026-01-01", "confidence": 50, **snapshot(policy=25, market=5, score=20)},
    ])
    assert history[0]["lifecycle_stage"] == "incubating"
    assert transitions[0]["to_stage"] == "incubating"


def test_illegal_lifecycle_jump_is_reduced_to_adjacent_stage():
    stage, direct = apply_transition("dormant", "mature")
    assert stage == "incubating"
    assert direct is False


def test_ended_theme_with_new_driver_restarts():
    stage, _ = target_stage(snapshot(policy=55, market=45, narrative=55, score=52), "ended")
    assert stage == "restarting"


def test_missing_industry_data_is_unknown_not_zero():
    dimension = build_industry_dimension("ai_compute_communications", mapping={"themes": {"ai_compute_communications": ["capex"]}, "observations": {}})
    assert dimension["industry_validation_score"] is None
    assert dimension["industry_stage_label"] == "产业验证不足"


def test_start_and_confirmation_dates_do_not_precede_available_evidence():
    history = [{"date": "2026-01-01", "lifecycle_stage": "confirmed"}]
    dates = lifecycle_dates(history, [{"event_date": "2026-02-01"}], "2026-03-01")
    assert dates["estimated_start_date"] == "2026-02-01"
    assert dates["confirmation_date"] == "2026-02-01"


def test_generated_report_contains_required_research_sections():
    payload, _, _ = generate(write=False)
    markdown = render_markdown(payload)
    for heading in ("## 当前结论", "## 当前主线格局", "## 四维证据拆解", "## 反面证据", "## 失效条件", "## 数据不足和不确定性"):
        assert heading in markdown
    assert payload["mainline_regime"] in {"single_dominant", "dual_mainline", "multi_mainline", "no_clear_mainline", "data_insufficient"}
    assert validate(payload)["error_count"] == 0
    for theme in payload["theme_states"]:
        dates = [point["date"] for point in theme["score_history"]]
        assert len(dates) == len(set(dates))


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
