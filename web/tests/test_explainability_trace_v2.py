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

from explainability_trace import main as explainability_main
from golden_snapshot_builder import build_golden_snapshot
from mainline_contract_validator import validate_mainline_report_contract
from system_drift_detector import build_drift_report
from theme_explanation_engine import SCORING_VERSION, build_theme_explanation, latest_report_path
from trace_graph_builder import orphan_theme_nodes
from web.main import app


def get(path: str) -> httpx.Response:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path)

    return asyncio.run(run())


def latest_payload() -> dict:
    return json.loads(latest_report_path().read_text(encoding="utf-8"))


def top_theme_id(report: dict) -> str:
    return report["mainline_ranking"][0]["theme_id"]


def test_policy_trace_connects_policy_event_theme():
    report = latest_payload()
    explanation = build_theme_explanation(report, top_theme_id(report))
    theme_node = f"theme:{explanation['theme_id']}"
    node_ids = {node["id"] for node in explanation["trace_graph"]["nodes"]}
    edges = {(edge["from"], edge["to"]) for edge in explanation["trace_graph"]["edges"]}

    assert explanation["top_policy_paths"]
    assert theme_node in node_ids
    assert ("theme:" + explanation["theme_id"], "mainline:mainline_score_v6") in edges
    for path in explanation["top_policy_paths"]:
        policy_node = f"policy:{path['policy_id']}"
        event_node = f"event:{path['event_cluster_id']}"
        assert policy_node in node_ids
        assert event_node in node_ids
        assert (policy_node, event_node) in edges
        assert (event_node, theme_node) in edges


def test_event_contribution_sum_matches_theme_score_v5_for_all_themes():
    report = latest_payload()
    for theme in report["theme_summary"]["themes"]:
        explanation = build_theme_explanation(report, theme["theme_id"])
        check = explanation["validation"]["contribution"]
        assert check["status"] == "pass"
        assert check["abs_delta"] <= 1e-6


def test_trace_graph_has_no_orphan_theme_node():
    report = latest_payload()
    explanation = build_theme_explanation(report, top_theme_id(report))
    assert orphan_theme_nodes(explanation["trace_graph"]) == []
    assert explanation["validation"]["graph"]["status"] == "pass"


def test_api_explain_theme_does_not_affect_latest_api():
    report = latest_payload()
    theme_id = top_theme_id(report)
    before = get("/api/latest").json()["result"]["mainline_ranking"][0]["mainline_score_v6"]
    body = get(f"/api/explain/theme/{theme_id}").json()
    after = get("/api/latest").json()["result"]["mainline_ranking"][0]["mainline_score_v6"]

    assert body["result"]["scoring_version"] == SCORING_VERSION
    assert body["result"]["theme_id"] == theme_id
    assert before == after


def test_explanation_is_deterministic():
    report = latest_payload()
    first = build_theme_explanation(report, top_theme_id(report))
    second = build_theme_explanation(report, top_theme_id(report))
    assert first == second


def test_explanation_does_not_mutate_report_or_contract_or_drift():
    report = latest_payload()
    before = deepcopy(report)
    explanation = build_theme_explanation(report, top_theme_id(report))

    assert explanation["status"] == "pass"
    assert report == before
    contract = validate_mainline_report_contract(report, checked_at="2026-06-22T19:10:00+08:00")
    assert contract["status"] == "pass"
    drift = build_drift_report(build_golden_snapshot(report), report)
    assert drift["drift_status"] == "perfect_match"


def test_api_missing_theme_returns_404():
    response = get("/api/explain/theme/not_a_real_theme")
    assert response.status_code == 404


def test_cli_outputs_top_drivers(capsys):
    report = latest_payload()
    exit_code = explainability_main(["--path", str(latest_report_path()), "--theme", top_theme_id(report)])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "TOP DRIVERS:" in output
    assert "TOP REASONS:" in output
    assert "CHECKS:" in output
