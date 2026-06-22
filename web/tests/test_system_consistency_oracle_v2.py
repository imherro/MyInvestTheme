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

from divergence_analyzer import analyze_run_divergence
from golden_snapshot_builder import build_golden_snapshot
from mainline_contract_validator import validate_mainline_report_contract
from multi_run_executor import execute_multi_run, latest_report_path
from system_consistency_oracle import build_consistency_oracle
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


def first_theme_id(report: dict) -> str:
    return report["mainline_ranking"][0]["theme_id"]


def first_allocation_pair(run: dict) -> tuple[str, str]:
    matrix = run["projection"]["allocation_matrix"]
    event_id = sorted(matrix)[0]
    theme_id = sorted(matrix[event_id])[0]
    return event_id, theme_id


def test_ten_runs_are_identical_and_stable():
    report = latest_payload()
    result = build_consistency_oracle(report, 10)
    hashes = {row["output_hash"] for row in result["run_hashes"]}

    assert result["consistency_status"] == "stable"
    assert result["run_count"] == 10
    assert result["score_variance"] == 0
    assert result["allocation_variance"] == 0
    assert result["root_cause"]["layer"] == "none"
    assert len(hashes) == 1


def test_multi_run_executor_outputs_identical_projection_hashes():
    report = latest_payload()
    runs = execute_multi_run(report, 10)

    assert len(runs) == 10
    assert len({run["output_hash"] for run in runs}) == 1


def test_injected_score_perturbation_detection_and_attribution():
    report = latest_payload()
    runs = execute_multi_run(report, 3)
    changed = deepcopy(runs)
    theme_id = first_theme_id(report)
    changed[1]["projection"]["theme_scores"][theme_id]["mainline_score_v6"] += 0.01

    analysis = analyze_run_divergence(changed)

    assert analysis["consistency_status"] == "critical"
    assert "score" in analysis["divergence"]
    assert analysis["score_variance"] > 0
    assert analysis["root_cause"]["layer"] == "scoring"


def test_injected_allocation_perturbation_detection_and_attribution():
    report = latest_payload()
    runs = execute_multi_run(report, 3)
    changed = deepcopy(runs)
    event_id, theme_id = first_allocation_pair(changed[1])
    changed[1]["projection"]["allocation_matrix"][event_id][theme_id] += 0.01

    analysis = analyze_run_divergence(changed)

    assert analysis["consistency_status"] == "critical"
    assert "allocation" in analysis["divergence"]
    assert analysis["allocation_variance"] > 0
    assert analysis["root_cause"]["layer"] == "allocation"


def test_injected_ranking_swap_detection():
    report = latest_payload()
    runs = execute_multi_run(report, 3)
    changed = deepcopy(runs)
    changed[1]["projection"]["ranking"][0], changed[1]["projection"]["ranking"][1] = (
        changed[1]["projection"]["ranking"][1],
        changed[1]["projection"]["ranking"][0],
    )

    analysis = analyze_run_divergence(changed)

    assert analysis["consistency_status"] == "critical"
    assert "ranking" in analysis["divergence"]
    assert analysis["root_cause"]["layer"] == "ranking"


def test_injected_graph_divergence_is_detected_without_scoring_root_cause():
    report = latest_payload()
    runs = execute_multi_run(report, 3)
    changed = deepcopy(runs)
    theme_id = first_theme_id(report)
    changed[1]["projection"]["explainability_graph_hashes"][theme_id] = "sha256:changed"

    analysis = analyze_run_divergence(changed)

    assert analysis["consistency_status"] == "unstable"
    assert "graph" in analysis["divergence"]
    assert analysis["root_cause"]["layer"] == "explainability"


def test_consistency_api_is_read_only_and_stable():
    before = get("/api/latest").json()["result"]["mainline_ranking"][0]["mainline_score_v6"]
    body = get("/api/consistency/oracle?runs=10").json()["result"]
    after = get("/api/latest").json()["result"]["mainline_ranking"][0]["mainline_score_v6"]

    assert body["consistency_status"] == "stable"
    assert body["run_count"] == 10
    assert before == after


def test_oracle_does_not_mutate_report_contract_or_drift():
    report = latest_payload()
    before = deepcopy(report)

    build_consistency_oracle(report, 10)

    assert report == before
    contract = validate_mainline_report_contract(report, checked_at="2026-06-22T21:10:00+08:00")
    assert contract["status"] == "pass"
    drift = build_drift_report(build_golden_snapshot(report), report)
    assert drift["drift_status"] == "perfect_match"
