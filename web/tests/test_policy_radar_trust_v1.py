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

from mainline_contract_validator import latest_report_path, validate_mainline_report_contract
from web.main import app


def get(path: str) -> httpx.Response:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path)

    return asyncio.run(run())


def latest_payload() -> dict:
    return json.loads(latest_report_path().read_text(encoding="utf-8"))


def error_codes(summary: dict) -> set[str]:
    return {item["code"] for item in summary["issues"] if item["severity"] == "error"}


def test_latest_and_index_expose_trust_summaries():
    latest = get("/api/latest").json()["result"]
    index = get("/api/index").json()
    keys = {
        "policy_time_provenance_summary",
        "policy_candidate_summary",
        "field_provenance_summary",
        "theme_relevance_input_summary",
        "data_freshness_summary",
        "score_semantics",
    }
    assert keys.issubset(latest)
    assert keys.issubset(index)
    assert latest["score_semantics"]["field"] == "policy_theme_conviction_score"


def test_health_exposes_policy_radar_guard_fields():
    body = get("/api/health").json()
    assert {
        "time_provenance_status",
        "point_in_time_unavailable_count",
        "candidate_audit_status",
        "high_inference_dependency_count",
        "data_freshness_status",
        "stale_trading_days",
    }.issubset(body)


def test_policy_audit_api_is_read_only_and_complete():
    policy_id = json.loads((ROOT / "data" / "policy_signals.json").read_text(encoding="utf-8"))["signals"][0]["id"]
    response = get(f"/api/policies/{policy_id}/audit")
    assert response.status_code == 200
    body = response.json()
    assert body["read_only"] is True
    assert body["content_hash"].startswith("sha256:")
    assert body["time_provenance"]["point_in_time_basis"] in {"official_publish_at", "first_seen_at", "unavailable"}
    assert body["field_provenance"]["beneficiary_chain"]["source_type"] == "llm_inference"
    assert body["candidate_decision"] == "included"
    assert body["theme_match_inputs"]["production_score_field"] == "theme_relevance_strict"


def test_contract_blocks_missing_candidate_and_score_alias_mismatch():
    report = latest_payload()
    missing_candidate = deepcopy(report)
    missing_candidate["policy_candidate_summary"]["issues"] = [
        {"code": "INCLUDED_POLICY_MISSING_CANDIDATE_RECORD", "path": "policy.x", "actual": "x"}
    ]
    mismatch = deepcopy(report)
    mismatch["mainline_ranking"][0]["policy_theme_conviction_score"] += 0.1
    assert "INCLUDED_POLICY_MISSING_CANDIDATE_RECORD" in error_codes(validate_mainline_report_contract(missing_candidate))
    assert "POLICY_THEME_CONVICTION_SCORE_MISMATCH" in error_codes(validate_mainline_report_contract(mismatch))


def test_contract_blocks_stale_current_language():
    report = latest_payload()
    report["data_freshness_summary"]["data_freshness_status"] = "stale"
    report["freshness_narrative"] = "当前主线是人工智能。"
    assert "STALE_CURRENT_MAINLINE_LANGUAGE" in error_codes(validate_mainline_report_contract(report))


def test_new_markdown_contains_required_trust_sections():
    markdown = latest_report_path().with_suffix(".md").read_text(encoding="utf-8")
    for title in ("## 点时数据完整性", "## 字段来源与推断依赖", "## 候选政策覆盖情况", "## 数据新鲜度", "## 指标语义与使用边界"):
        assert title in markdown
