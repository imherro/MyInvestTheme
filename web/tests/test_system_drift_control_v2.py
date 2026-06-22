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

from golden_snapshot_builder import build_golden_snapshot, latest_report_path
from mainline_contract_validator import validate_mainline_report_contract
from system_drift_detector import (
    build_drift_report,
    compute_allocation_drift,
    compute_lifecycle_drift,
    compute_ranking_drift,
    compute_score_drift,
)
from web.main import app


def get(path: str) -> httpx.Response:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path)

    return asyncio.run(run())


def latest_payload() -> dict:
    return json.loads(latest_report_path().read_text(encoding="utf-8"))


def golden_payload() -> dict:
    return json.loads((ROOT / "data" / "golden_mainline_snapshot.json").read_text(encoding="utf-8"))


def golden_source_payload() -> dict:
    snapshot = golden_payload()
    report_id = snapshot["source_report_id"]
    return json.loads((ROOT / "research" / "mainline" / f"{report_id}.json").read_text(encoding="utf-8"))


def test_golden_snapshot_is_deterministic():
    report = latest_payload()
    first = build_golden_snapshot(report)
    second = build_golden_snapshot(report)
    assert first == second


def test_same_input_has_zero_drift():
    report = latest_payload()
    drift = build_drift_report(build_golden_snapshot(report), report)
    assert drift["drift_status"] == "perfect_match"
    assert drift["score_drift"]["max_abs_delta"] == 0
    assert drift["allocation_drift"]["max_abs_delta"] == 0


def test_controlled_score_perturbation_is_detected():
    report = latest_payload()
    golden = build_golden_snapshot(report)
    changed = deepcopy(report)
    changed["mainline_ranking"][0]["mainline_score_v6"] += 0.02
    drift = compute_score_drift(golden, changed)
    assert drift["status"] == "critical"
    assert drift["max_abs_delta"] > 0.01


def test_ranking_swap_detection_works():
    report = latest_payload()
    golden = build_golden_snapshot(report)
    changed = deepcopy(report)
    changed["mainline_ranking"][0], changed["mainline_ranking"][1] = changed["mainline_ranking"][1], changed["mainline_ranking"][0]
    for index, row in enumerate(changed["mainline_ranking"], start=1):
        row["rank"] = index
    drift = compute_ranking_drift(golden, changed)
    assert drift["status"] == "critical"
    assert drift["golden_top1"] != drift["current_top1"]


def test_allocation_mismatch_is_detected():
    report = latest_payload()
    golden = build_golden_snapshot(report)
    changed = deepcopy(report)
    event = changed["event_theme_allocation_summary"]["events"][0]
    event["allocated_themes"][0]["allocated_cluster_contribution"] += 0.02
    drift = compute_allocation_drift(golden, changed)
    assert drift["status"] == "critical"
    assert drift["max_abs_delta"] > 0.01


def test_lifecycle_mismatch_is_detected():
    report = latest_payload()
    golden = build_golden_snapshot(report)
    changed = deepcopy(report)
    changed["mainline_ranking"][0]["lifecycle_state"] = "dormant"
    drift = compute_lifecycle_drift(golden, changed)
    assert drift["status"] == "critical"
    assert drift["lifecycle_change_count"] == 1


def test_api_golden_snapshot_returns_snapshot():
    body = get("/api/golden-snapshot").json()
    assert body["scoring_version"] == "system_drift_control_v2"
    assert body["snapshot_id"] == "mainline_gold_v1"


def test_api_drift_returns_status_for_latest():
    body = get("/api/drift").json()
    assert body["drift_status"] in {"perfect_match", "warning", "critical"}
    assert body["current_report_id"] == latest_payload()["report_id"]
    if body["current_report_id"] == body["golden_source_report_id"]:
        assert body["drift_status"] == "perfect_match"


def test_api_compare_can_compare_golden_source_report():
    report_id = golden_payload()["source_report_id"]
    body = get(f"/api/compare?report_id={report_id}").json()
    assert body["current_report_id"] == report_id
    assert body["drift_status"] == "perfect_match"


def test_drift_layer_does_not_change_mainline_score_or_contract():
    report = golden_source_payload()
    golden = golden_payload()
    assert report["mainline_ranking"][0]["theme_id"] == golden["mainline_ranking"][0]["theme_id"]
    assert report["mainline_ranking"][0]["mainline_score_v6"] == golden["mainline_ranking"][0]["mainline_score_v6"]
    summary = validate_mainline_report_contract(report, checked_at="2026-06-22T18:40:00+08:00")
    assert summary["status"] == "pass"
