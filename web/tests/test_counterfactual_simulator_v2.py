import asyncio
import json
import sys
from copy import deepcopy
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from core_driver_detector import detect_core_drivers
from counterfactual_simulator import (
    CounterfactualTargetNotFound,
    latest_report_path,
    simulate_remove_event,
    simulate_remove_policy,
)
from golden_snapshot_builder import build_golden_snapshot
from mainline_contract_validator import validate_mainline_report_contract
from mainline_sensitivity_engine import build_theme_sensitivity
from system_drift_detector import build_drift_report
from web.main import app


def get(path: str) -> httpx.Response:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path)

    return asyncio.run(run())


def latest_payload() -> dict:
    return json.loads(latest_report_path().read_text(encoding="utf-8"))


def top_theme(report: dict) -> dict:
    theme_id = report["mainline_ranking"][0]["theme_id"]
    return next(theme for theme in report["theme_summary"]["themes"] if theme["theme_id"] == theme_id)


def top_event(report: dict) -> dict:
    return top_theme(report)["all_event_contributors"][0]


def test_remove_policy_simulation_is_reproducible():
    report = latest_payload()
    policy_id = top_event(report)["primary_policy_id"]

    first = simulate_remove_policy(report, policy_id)
    second = simulate_remove_policy(report, policy_id)

    assert first == second
    assert first["removed_policy"] == policy_id
    assert first["baseline_score_v6"] > first["counterfactual_score"]
    assert first["delta"] < 0
    assert first["overlay_only"] is True
    assert first["writes_report"] is False


def test_remove_event_simulation_recomputes_score():
    report = latest_payload()
    event_id = top_event(report)["event_cluster_id"]

    result = simulate_remove_event(report, event_id)

    assert result["removed_event_cluster_id"] == event_id
    assert result["baseline_score_v6"] > result["counterfactual_score"]
    assert result["impact_summary"]["affected_theme_count"] >= 1
    assert result["impact_summary"]["total_mainline_score_drop"] > 0


def test_counterfactual_does_not_mutate_report_contract_or_drift():
    report = latest_payload()
    before = deepcopy(report)
    policy_id = top_event(report)["primary_policy_id"]

    simulate_remove_policy(report, policy_id)

    assert report == before
    contract = validate_mainline_report_contract(report, checked_at="2026-06-22T20:10:00+08:00")
    assert contract["status"] == "pass"
    drift = build_drift_report(build_golden_snapshot(report), report)
    assert drift["drift_status"] == "perfect_match"


def test_simulation_ranking_is_separate_from_mainline_ranking():
    report = latest_payload()
    event_id = top_event(report)["event_cluster_id"]
    baseline_order = [row["theme_id"] for row in report["mainline_ranking"]]

    result = simulate_remove_event(report, event_id)

    assert [row["theme_id"] for row in report["mainline_ranking"]] == baseline_order
    assert [row["theme_id"] for row in result["baseline_ranking"]] == baseline_order
    assert result["counterfactual_ranking"]
    assert result["ranking_changed"] in {True, False}


def test_theme_sensitivity_index_is_deterministic():
    report = latest_payload()
    theme_id = top_theme(report)["theme_id"]

    first = build_theme_sensitivity(report, theme_id)
    second = build_theme_sensitivity(report, theme_id)

    assert first == second
    assert first["theme_id"] == theme_id
    assert first["sensitivity_index"] > 0
    assert first["top_policy_driver"]["score_drop"] > 0
    assert first["top_event_driver"]["score_drop"] > 0


def test_core_driver_detection_is_deterministic():
    report = latest_payload()

    first = detect_core_drivers(report)
    second = detect_core_drivers(report)

    assert first == second
    assert first["core_driver_count"] >= 1
    assert first["core_drivers"][0]["impact_rank"] == 1
    assert first["core_drivers"][0]["total_mainline_score_drop"] > 0


def test_counterfactual_apis_are_read_only_and_stable():
    report = latest_payload()
    policy_id = top_event(report)["primary_policy_id"]
    event_id = top_event(report)["event_cluster_id"]
    theme_id = top_theme(report)["theme_id"]
    before = get("/api/latest").json()["result"]["mainline_ranking"][0]["mainline_score_v6"]

    policy_body = get(f"/api/simulate/remove-policy/{policy_id}").json()["result"]
    event_body = get(f"/api/simulate/remove-event/{event_id}").json()["result"]
    sensitivity_body = get(f"/api/sensitivity/theme/{theme_id}").json()["result"]
    drivers_body = get("/api/core-drivers").json()["result"]
    after = get("/api/latest").json()["result"]["mainline_ranking"][0]["mainline_score_v6"]

    assert policy_body["simulation_type"] == "remove_policy"
    assert event_body["simulation_type"] == "remove_event"
    assert sensitivity_body["theme_id"] == theme_id
    assert drivers_body["core_driver_count"] >= 1
    assert before == after


def test_missing_counterfactual_targets_return_404_or_raise():
    report = latest_payload()

    try:
        simulate_remove_policy(report, "not-a-real-policy")
    except CounterfactualTargetNotFound:
        pass
    else:
        raise AssertionError("missing policy should raise CounterfactualTargetNotFound")

    assert get("/api/simulate/remove-policy/not-a-real-policy").status_code == 404
    assert get("/api/simulate/remove-event/not-a-real-event").status_code == 404
    assert get("/api/sensitivity/theme/not-a-real-theme").status_code == 404
