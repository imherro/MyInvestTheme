import asyncio
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from generate_mainline_report import attach_contract_validation, render_markdown
from mainline_contract_validator import latest_report_path, validate_mainline_report_contract
from web.main import app


FIXED_CHECKED_AT = "2026-06-22T16:30:00+08:00"


def get(path: str) -> httpx.Response:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path)

    return asyncio.run(run())


@pytest.fixture()
def report() -> dict:
    return json.loads(latest_report_path().read_text(encoding="utf-8"))


def issue_codes(summary: dict, severity: str = "error") -> set[str]:
    return {issue["code"] for issue in summary["issues"] if issue["severity"] == severity}


def validate(payload: dict) -> dict:
    return validate_mainline_report_contract(payload, checked_at=FIXED_CHECKED_AT)


def test_latest_valid_report_passes_contract(report):
    summary = validate(report)
    assert summary["scoring_version"] == "mainline_contract_validator_v2"
    assert summary["status"] == "pass"
    assert summary["error_count"] == 0
    assert summary["checked_sections"]["score_formulas"] is True


def test_missing_required_section_is_error(report):
    broken = deepcopy(report)
    broken.pop("policy_summary")
    summary = validate(broken)
    assert summary["status"] == "fail"
    assert "MISSING_REQUIRED_SECTION" in issue_codes(summary)


def test_canonical_top_mismatch_is_error(report):
    broken = deepcopy(report)
    broken["canonical_mainline_summary"]["top_mainline"]["theme_id"] = "wrong_theme"
    summary = validate(broken)
    assert "CANONICAL_TOP_MISMATCH" in issue_codes(summary)


def test_mainline_sorting_error_is_detected(report):
    broken = deepcopy(report)
    rows = broken["mainline_ranking"]
    rows[0], rows[1] = rows[1], rows[0]
    summary = validate(broken)
    assert "MAINLINE_RANKING_ORDER_MISMATCH" in issue_codes(summary)
    assert "MAINLINE_RANKING_SORT_MISMATCH" in issue_codes(summary)


def test_v6_greater_than_v5_is_error(report):
    broken = deepcopy(report)
    first = broken["theme_summary"]["themes"][0]
    first["mainline_score_v6"] = round(first["theme_score_v5"] + 0.1, 4)
    summary = validate(broken)
    assert "SCORE_MONOTONICITY_BROKEN" in issue_codes(summary)


def test_theme_score_formula_error_is_detected(report):
    broken = deepcopy(report)
    broken["theme_summary"]["themes"][0]["theme_score_v5"] = 0.1234
    summary = validate(broken)
    assert "THEME_SCORE_V5_FORMULA_MISMATCH" in issue_codes(summary)


def test_allocation_cap_error_is_detected(report):
    broken = deepcopy(report)
    event = broken["event_theme_allocation_summary"]["events"][0]
    event["allocation_budget_used"] = round(event["event_contribution_budget"] + 0.05, 4)
    summary = validate(broken)
    assert "EVENT_BUDGET_OVERUSED" in issue_codes(summary)
    assert "CAPPED_EVENT_CONTRACT_BROKEN" in issue_codes(summary)


def test_lifecycle_state_count_mismatch_is_error(report):
    broken = deepcopy(report)
    broken["mainline_lifecycle_summary"]["sustained_count"] += 1
    summary = validate(broken)
    assert "LIFECYCLE_STATE_COUNT_MISMATCH" in issue_codes(summary)


def test_legacy_default_leak_is_error(report):
    broken = deepcopy(report)
    broken["canonical_mainline_summary"]["default_score_field"] = "evidence_score"
    summary = validate(broken)
    assert "LEGACY_DEFAULT_FIELD_LEAK" in issue_codes(summary)


def test_cli_bad_report_exits_one(report, tmp_path):
    broken = deepcopy(report)
    broken.pop("policy_summary")
    bad_path = tmp_path / "bad_report.json"
    bad_path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(SCRIPTS / "mainline_contract_validator.py"), "--path", str(bad_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 1
    assert "MISSING_REQUIRED_SECTION" in completed.stdout


def test_generator_validation_blocks_bad_payload_before_render(report):
    broken = deepcopy(report)
    broken.pop("policy_summary")
    with pytest.raises(RuntimeError, match="Mainline report contract failed before write"):
        attach_contract_validation(broken)


def test_markdown_contains_contract_validation_section(report):
    payload = deepcopy(report)
    attach_contract_validation(payload)
    markdown = render_markdown(payload)
    assert "## 报告合约校验" in markdown
    assert "mainline_contract_validator_v2" in markdown


def test_api_latest_exposes_contract_summary():
    body = get("/api/latest").json()
    summary = body["result"]["contract_validation_summary"]
    assert summary["scoring_version"] == "mainline_contract_validator_v2"
    assert summary["status"] == "pass"
    assert summary["error_count"] == 0


def test_api_index_exposes_contract_status():
    body = get("/api/index").json()
    assert body["contract_validation_summary"]["status"] == "pass"
    assert body["latest_report"]["contract_validation_status"] == "pass"
    assert body["latest_report"]["contract_validation_error_count"] == 0


def test_api_health_exposes_contract_status():
    body = get("/api/health").json()
    assert body["latest_contract_status"] == "pass"
    assert body["latest_contract_error_count"] == 0
    assert body["latest_contract_warning_count"] >= 0


def test_score_series_default_contract_still_uses_v6():
    body = get("/api/score-series").json()
    points = [point for theme in body["themes"] for point in theme["points"]]
    assert points
    for point in points:
        assert point["default_score_field"] == "mainline_score_v6"
        assert point["score"] == point["mainline_score_v6"]
        assert point["default_score"] == point["mainline_score_v6"]


def test_contract_validation_is_deterministic(report):
    first = validate_mainline_report_contract(report, checked_at=FIXED_CHECKED_AT)
    second = validate_mainline_report_contract(report, checked_at=FIXED_CHECKED_AT)
    assert first == second
