from __future__ import annotations

import json
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

try:
    from policy_time_provenance import parse_policy_time
except ModuleNotFoundError:
    from scripts.policy_time_provenance import parse_policy_time


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "config" / "data_freshness_rules.json"
VERSION = "data_freshness_guard_v1"


def load_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _trade_date(value: Any) -> date | None:
    text = str(value or "").replace("-", "")
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None


def expected_latest_trade_date(
    as_of: datetime,
    trading_dates: Iterable[Any],
    *,
    cutoff_time: str = "17:00:00",
) -> date | None:
    cutoff = time.fromisoformat(cutoff_time)
    eligible_until = as_of.date() if as_of.time() >= cutoff else as_of.date().fromordinal(as_of.date().toordinal() - 1)
    eligible = sorted(item for item in (_trade_date(value) for value in trading_dates) if item and item <= eligible_until)
    return eligible[-1] if eligible else None


def build_data_freshness_summary(
    *,
    actual_basis_date: str,
    generated_at: str | datetime,
    trading_dates: Iterable[Any],
    policies: list[dict[str, Any]],
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active = rules or load_rules()
    timezone = str(active.get("timezone") or "Asia/Shanghai")
    zone = ZoneInfo(timezone)
    generated = generated_at if isinstance(generated_at, datetime) else parse_policy_time(generated_at, timezone)
    if generated is None:
        generated = datetime.now(zone)
    actual = _trade_date(actual_basis_date)
    trade_days = sorted({item for item in (_trade_date(value) for value in trading_dates) if item})
    expected = expected_latest_trade_date(generated, trade_days, cutoff_time=str(active.get("complete_data_cutoff_time") or "17:00:00"))
    stale_days = sum(1 for item in trade_days if actual and expected and actual < item <= expected)
    warnings: list[str] = []
    if actual is None or expected is None:
        status = "unknown"
        warnings.append("TRADING_CALENDAR_OR_BASIS_UNAVAILABLE")
    elif stale_days > int(active.get("max_stale_trading_days") or 1):
        status = "stale"
        warnings.append("MARKET_DATA_STALE")
    else:
        status = "fresh"

    first_seen_values = [parse_policy_time(policy.get("first_seen_at"), timezone) for policy in policies]
    first_seen_values = [value for value in first_seen_values if value]
    latest_first_seen = max(first_seen_values) if first_seen_values else None
    policy_lag = round((generated - latest_first_seen).total_seconds() / 3600, 2) if latest_first_seen else None
    if policy_lag is None:
        warnings.append("POLICY_FIRST_SEEN_UNAVAILABLE")
        if status == "fresh":
            status = "degraded"
    elif policy_lag > float(active.get("max_policy_ingestion_lag_hours") or 72):
        warnings.append("POLICY_INGESTION_LAG")
        if status == "fresh":
            status = "degraded"

    market_lag = None
    if actual:
        close_at = datetime.combine(actual, time.fromisoformat(str(active.get("market_close_time") or "15:00:00")), zone)
        market_lag = round(max(0.0, (generated - close_at).total_seconds() / 3600), 2)
        if market_lag > float(active.get("max_market_data_lag_hours") or 72):
            warnings.append("MARKET_DATA_LAG_HOURS")
            if status == "fresh":
                status = "degraded"
    return {
        "scoring_version": active.get("version", VERSION),
        "data_freshness_status": status,
        "expected_latest_trade_date": expected.isoformat() if expected else "",
        "actual_basis_date": actual.isoformat() if actual else str(actual_basis_date or ""),
        "stale_trading_days": stale_days,
        "latest_policy_first_seen_at": latest_first_seen.isoformat(timespec="seconds") if latest_first_seen else "",
        "policy_ingestion_lag_hours": policy_lag,
        "market_data_lag_hours": market_lag,
        "freshness_warnings": warnings,
        "block_report_write": status == "stale" and bool(active.get("block_report_write_when_stale")),
    }


def freshness_narrative(summary: dict[str, Any], theme_name: str = "") -> str:
    rules = load_rules()
    if summary.get("data_freshness_status") == "stale":
        return str(rules["stale_message_template"]).format(actual_basis_date=summary.get("actual_basis_date", ""))
    return str(rules["fresh_message_template"]).format(theme_name=theme_name or "待确认主题")
