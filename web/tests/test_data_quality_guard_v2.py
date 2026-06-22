import json
import sys
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import generate_mainline_report as gen
from data_quality_guard import (
    build_data_quality_summary,
    build_stage_status,
    clean_records_safe,
    empty_dataframe_with_columns,
    ensure_dataframe_columns,
    run_optional_stage,
)
from mainline_contract_validator import latest_report_path, validate_mainline_report_contract
from web.tests.test_app import get


def latest_payload() -> dict:
    return json.loads(latest_report_path().read_text(encoding="utf-8"))


def issue_codes(summary: dict, severity: str = "error") -> set[str]:
    return {issue["code"] for issue in summary["issues"] if issue["severity"] == severity}


def mock_report_inputs(monkeypatch: pytest.MonkeyPatch, *, sw_stage=None, policy_fails: bool = False) -> None:
    payload = latest_payload()
    dates = [(datetime(2026, 5, 20) + timedelta(days=index)).strftime("%Y%m%d") for index in range(30)]
    basis = dates[-1]

    monkeypatch.setattr(gen, "make_client", lambda: object())
    monkeypatch.setattr(gen, "get_trade_dates", lambda pro, today: dates)
    monkeypatch.setattr(
        gen,
        "choose_basis_date",
        lambda pro, open_days: (basis, {"daily_rows": 1, "daily_basic_rows": 1, "basis": basis, "checked": []}),
    )
    monkeypatch.setattr(gen, "load_policy_store", lambda: {"updated_at": "2026-06-22", "signals": [{}]})
    monkeypatch.setattr(gen, "policy_event_summary", lambda basis_date, themes: deepcopy(payload["event_cluster_summary"]))
    if policy_fails:
        def fail_policy_theme_summary(basis_date, themes):
            raise RuntimeError("mock required policy stage failed")

        monkeypatch.setattr(gen, "policy_theme_summary", fail_policy_theme_summary)
    else:
        monkeypatch.setattr(gen, "policy_theme_summary", lambda basis_date, themes: deepcopy(payload["theme_summary"]))
    policy_by_theme = {theme["theme_name"]: deepcopy(theme) for theme in payload["theme_summary"]["themes"]}
    monkeypatch.setattr(gen, "score_policy_by_theme", lambda basis_date, themes: deepcopy(policy_by_theme))
    monkeypatch.setattr(gen, "stock_breadth", lambda pro, basis_raw, d5, d20: deepcopy(payload["breadth"]))
    monkeypatch.setattr(gen, "broad_index_data", lambda pro, basis_raw, d5, d20: deepcopy(payload["broad_indexes"]))
    monkeypatch.setattr(gen, "score_sw", sw_stage or (lambda pro, window_dates: pd.DataFrame(payload["sw_top"])))
    monkeypatch.setattr(gen, "score_ths", lambda pro, window_dates: pd.DataFrame(payload["ths_top"]))
    monkeypatch.setattr(gen, "score_etf", lambda pro, window_dates: pd.DataFrame(payload["etf_top"]))
    monkeypatch.setattr(
        gen,
        "limit_up_data",
        lambda pro, basis_raw: (
            pd.DataFrame([{"ts_code": "000001.SZ", "name": "mock", "industry": "电子", "limit": "U", "turnover_ratio": 1.0}]),
            [{"industry": "电子", "limit_count": 1, "avg_turnover": 1.0}],
        ),
    )
    monkeypatch.setattr(
        gen,
        "moneyflow_data",
        lambda pro, basis_raw: (
            pd.DataFrame([{"ts_code": "000001.SZ", "industry": "半导体", "large_net": 1.0, "net_mf_amount": 1.0}]),
            [{"industry": "半导体", "large_net": 1.0, "net": 1.0, "count": 1}],
        ),
    )
    monkeypatch.setattr(gen, "baostock_check", lambda basis_raw: [{"name": "上证综指", "rows": []}])


def test_empty_dataframe_gets_schema_columns():
    result = ensure_dataframe_columns(pd.DataFrame(), ["ts_code", "name", "score"], {"name": "", "score": 0.0})
    assert {"ts_code", "name", "score"}.issubset(result.columns)
    assert len(result) == 0


def test_missing_columns_are_added_with_defaults():
    result = ensure_dataframe_columns(
        pd.DataFrame([{"ts_code": "801080.SI"}]),
        ["ts_code", "name", "score"],
        {"name": "", "score": 0.0},
    )
    assert "name" in result.columns
    assert "score" in result.columns
    assert result.iloc[0]["name"] == ""
    assert result.iloc[0]["score"] == 0.0


def test_clean_records_safe_handles_missing_columns():
    records = clean_records_safe(pd.DataFrame([{"ts_code": "801080.SI"}]), 20, ["ts_code", "name", "score"], {"name": "", "score": 0.0})
    assert isinstance(records, list)
    assert records[0]["name"] == ""
    assert records[0]["score"] == 0.0


def test_optional_stage_exception_uses_fallback():
    fallback = empty_dataframe_with_columns(["ts_code", "name"])

    def bad_stage():
        raise RuntimeError("mock source failed")

    value, status = run_optional_stage("sw_score", bad_stage, fallback, ["ts_code", "name"], {"name": ""})
    assert value.equals(fallback)
    assert status["status"] == "degraded"
    assert status["fallback_used"] is True
    assert "mock source failed" in status["error"]


def test_data_quality_summary_counts_statuses():
    summary = build_data_quality_summary(
        [
            build_stage_status("policy_store", "pass", True, 1),
            build_stage_status("sw_score", "degraded", False, 0, ["name"]),
            build_stage_status("policy_theme_summary", "fail", True, 0, [], "mock required failure"),
        ]
    )
    assert summary["required_failure_count"] == 1
    assert summary["optional_failure_count"] == 1
    assert summary["status"] == "fail"


def test_optional_failure_does_not_block_build_report(monkeypatch):
    def bad_sw(pro, window_dates):
        raise RuntimeError("mock sw failed")

    mock_report_inputs(monkeypatch, sw_stage=bad_sw)
    _, payload, _ = gen.build_report("2026-06-22")
    assert payload["data_quality_summary"]["status"] == "degraded"
    assert payload["data_quality_summary"]["required_failure_count"] == 0
    assert payload["contract_validation_summary"]["status"] == "pass"


def test_required_failure_blocks_before_write(monkeypatch, tmp_path):
    mock_report_inputs(monkeypatch, policy_fails=True)
    monkeypatch.setattr(gen, "REPORT_DIR", tmp_path)
    before = set(tmp_path.glob("*"))
    with pytest.raises(RuntimeError, match="mock required policy stage failed"):
        gen.build_report("2026-06-22")
    after = set(tmp_path.glob("*"))
    assert after == before


def test_sw_empty_missing_name_no_longer_crashes(monkeypatch):
    mock_report_inputs(monkeypatch, sw_stage=lambda pro, window_dates: pd.DataFrame())
    _, payload, _ = gen.build_report("2026-06-22")
    assert payload["sw_top"] == []
    sw_status = next(status for status in payload["data_quality_summary"]["stage_statuses"] if status["stage"] == "sw_score")
    assert sw_status["status"] == "degraded"
    assert "name" in sw_status["missing_columns"]


def test_canonical_mainline_unchanged_by_optional_degraded(monkeypatch):
    mock_report_inputs(monkeypatch)
    _, normal_payload, _ = gen.build_report("2026-06-22")
    mock_report_inputs(monkeypatch, sw_stage=lambda pro, window_dates: pd.DataFrame())
    _, degraded_payload, _ = gen.build_report("2026-06-22")
    assert normal_payload["mainline_ranking"][0]["theme_id"] == degraded_payload["mainline_ranking"][0]["theme_id"]
    assert normal_payload["mainline_ranking"][0]["mainline_score_v6"] == degraded_payload["mainline_ranking"][0]["mainline_score_v6"]


def test_contract_validator_detects_required_data_quality_failure():
    report = latest_payload()
    report["data_quality_summary"] = build_data_quality_summary(
        [
            build_stage_status("policy_store", "fail", True, 0, [], "mock required failure"),
            build_stage_status("sw_score", "pass", False, 1),
        ]
    )
    summary = validate_mainline_report_contract(report, checked_at="2026-06-22T17:00:00+08:00")
    assert "DATA_QUALITY_REQUIRED_FAILURE" in issue_codes(summary)


def test_api_health_exposes_data_quality_status():
    body = get("/api/health").json()
    assert body["latest_data_quality_status"] in ["pass", "degraded"]
    assert body["latest_data_quality_required_failure_count"] == 0


def test_api_latest_exposes_data_quality_summary():
    body = get("/api/latest").json()
    summary = body["result"]["data_quality_summary"]
    assert summary["scoring_version"] == "live_report_data_guard_v2"
    assert summary["status"] in ["pass", "degraded"]
    assert summary["required_failure_count"] == 0


def test_api_index_exposes_data_quality_summary():
    body = get("/api/index").json()
    assert body["data_quality_summary"]["status"] in ["pass", "degraded"]
    assert body["latest_report"]["data_quality_status"] == body["data_quality_summary"]["status"]
    assert body["latest_report"]["data_quality_required_failure_count"] == 0


def test_required_stage_failure_does_not_copy_previous_report(monkeypatch, tmp_path):
    mock_report_inputs(monkeypatch, policy_fails=True)
    monkeypatch.setattr(gen, "REPORT_DIR", tmp_path)
    before_count = len(list(tmp_path.glob("mainline_review_*")))
    with pytest.raises(RuntimeError):
        gen.build_report("2026-06-22")
    assert len(list(tmp_path.glob("mainline_review_*"))) == before_count


def test_data_quality_summary_is_deterministic():
    statuses = [
        build_stage_status("policy_store", "pass", True, 1),
        build_stage_status("sw_score", "degraded", False, 0, ["name"]),
    ]
    outputs = [build_data_quality_summary(statuses) for _ in range(10)]
    assert all(item == outputs[0] for item in outputs)
