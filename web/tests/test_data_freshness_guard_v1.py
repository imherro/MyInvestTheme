import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_freshness_guard import build_data_freshness_summary


def test_weekend_does_not_create_two_stale_days():
    summary = build_data_freshness_summary(actual_basis_date="2026-06-19", generated_at="2026-06-21T18:00:00+08:00", trading_dates=["20260618", "20260619", "20260622"], policies=[])
    assert summary["expected_latest_trade_date"] == "2026-06-19"
    assert summary["stale_trading_days"] == 0


def test_holiday_is_not_counted_as_trading_delay():
    summary = build_data_freshness_summary(actual_basis_date="2026-10-09", generated_at="2026-10-12T18:00:00+08:00", trading_dates=["20260930", "20261009", "20261012"], policies=[])
    assert summary["stale_trading_days"] == 1
    assert summary["data_freshness_status"] != "stale"


def test_market_delay_over_threshold_is_stale():
    summary = build_data_freshness_summary(actual_basis_date="2026-06-18", generated_at="2026-06-23T18:00:00+08:00", trading_dates=["20260618", "20260619", "20260622", "20260623"], policies=[])
    assert summary["stale_trading_days"] == 3
    assert summary["data_freshness_status"] == "stale"


def test_policy_and_market_lags_are_separate():
    summary = build_data_freshness_summary(actual_basis_date="2026-06-22", generated_at="2026-06-22T18:00:00+08:00", trading_dates=["20260619", "20260622"], policies=[{"first_seen_at": "2026-06-22T17:00:00+08:00"}])
    assert summary["policy_ingestion_lag_hours"] == 1.0
    assert summary["market_data_lag_hours"] == 3.0
