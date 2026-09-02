"""Small, read-only Hithink fallback helpers for existing market stages.

Tushare remains the primary source.  These helpers are deliberately limited to
data with a clear equivalent: local A-share daily bars for breadth and the
official Hithink index-history command for broad indexes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_DB_PATH = Path.home() / "AppData" / "Local" / "hithink-finance" / "data" / "market.duckdb"
BROAD_INDEXES = [
    ("000001.SH", "上证综指"),
    ("000300.SH", "沪深300"),
    ("000905.SH", "中证500"),
    ("000852.SH", "中证1000"),
    ("000688.SH", "科创50"),
    ("399006.SZ", "创业板指"),
    ("399001.SZ", "深证成指"),
]


class HithinkFallbackError(RuntimeError):
    """Hithink could not provide an equivalent fallback dataset."""


def _db_path() -> Path:
    return Path(os.environ.get("HITHINK_FINANCE_DB", str(DEFAULT_DB_PATH)))


def _date_ms(date_text: str) -> int:
    return int(datetime.strptime(date_text[:10], "%Y-%m-%d").replace(tzinfo=TZ).timestamp() * 1000)


def _date_text(date_ms: int) -> str:
    return datetime.fromtimestamp(int(date_ms) / 1000, TZ).strftime("%Y-%m-%d")


def _cli_json(args: list[str]) -> dict[str, Any]:
    cli = os.environ.get("HITHINK_FINANCE_CLI", "hithink-finance")
    command = [cli]
    resolved_cli = shutil.which(cli)
    if sys.platform == "win32" and resolved_cli and Path(resolved_cli).suffix.lower() == ".cmd":
        command = [os.environ.get("ComSpec", "cmd.exe"), "/d", "/c", resolved_cli]
    elif sys.platform == "win32" and not resolved_cli:
        powershell_script = Path.home() / "AppData" / "Roaming" / "npm" / "hithink-finance.ps1"
        if powershell_script.exists():
            powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
            if powershell:
                command = [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(powershell_script)]
    try:
        result = subprocess.run(
            [*command, *args, "--format", "json"],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HithinkFallbackError(f"Hithink CLI unavailable: {exc}") from exc
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise HithinkFallbackError("Hithink CLI returned invalid JSON") from exc
    if result.returncode != 0 or payload.get("ok") is not True:
        error = payload.get("error") or result.stderr.strip() or f"exit code {result.returncode}"
        raise HithinkFallbackError(f"Hithink request failed: {error}")
    return payload


def hithink_breadth(basis: str, d5: str, d20: str) -> dict[str, Any]:
    """Calculate the existing breadth fields from Hithink's local daily bars."""
    db_path = _db_path()
    if not db_path.exists():
        raise HithinkFallbackError(f"Hithink local database not found: {db_path}")
    try:
        import duckdb

        with duckdb.connect(str(db_path), read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT thscode, date, close, prev_close
                FROM (
                    SELECT
                        thscode,
                        date,
                        close,
                        LAG(close) OVER (PARTITION BY thscode ORDER BY date) AS prev_close
                    FROM v_daily
                    WHERE date BETWEEN ? AND ?
                ) bars
                WHERE date IN (?, ?, ?)
                """,
                [d20[:10], basis[:10], basis[:10], d5[:10], d20[:10]],
            ).fetchdf()
    except Exception as exc:
        raise HithinkFallbackError(f"Hithink breadth query failed: {exc}") from exc
    if rows.empty:
        raise HithinkFallbackError(f"Hithink has no daily bars for {basis}, {d5}, {d20}")
    current = rows[rows["date"].astype(str).str[:10] == basis[:10]].copy()
    old5 = rows[rows["date"].astype(str).str[:10] == d5[:10]][["thscode", "close"]].rename(columns={"close": "close_5"})
    old20 = rows[rows["date"].astype(str).str[:10] == d20[:10]][["thscode", "close"]].rename(columns={"close": "close_20"})
    if current.empty:
        raise HithinkFallbackError(f"Hithink has no current bars for {basis}")
    # v_daily does not expose a separate pct_chg column; prev_close is the
    # equivalent source field for the existing one-day breadth calculation.
    current["pct_chg"] = (current["close"] / current["prev_close"] - 1) * 100
    merged = current.merge(old5, on="thscode", how="left").merge(old20, on="thscode", how="left")
    merged["r5"] = (merged["close"] / merged["close_5"] - 1) * 100
    merged["r20"] = (merged["close"] / merged["close_20"] - 1) * 100
    return {
        "rows": int(len(current)),
        "up_ratio": float((current["pct_chg"] > 0).mean() * 100),
        "median_pct_chg": float(current["pct_chg"].median()),
        "gt_5_count": int((current["pct_chg"] >= 5).sum()),
        "lt_minus_5_count": int((current["pct_chg"] <= -5).sum()),
        "r5_positive_ratio": float((merged["r5"] > 0).mean() * 100),
        "r20_positive_ratio": float((merged["r20"] > 0).mean() * 100),
        "median_r5": float(merged["r5"].median()),
        "median_r20": float(merged["r20"].median()),
    }


def hithink_broad_indexes(basis: str, d5: str, d20: str) -> list[dict[str, Any]]:
    """Read the seven existing broad indexes through Hithink's index history."""
    result: list[dict[str, Any]] = []
    for code, name in BROAD_INDEXES:
        payload = _cli_json(
            [
                "index",
                "history",
                "--thscode",
                code,
                "--start-ms",
                str(_date_ms(d20)),
                "--end-ms",
                str(_date_ms(basis) + 86399999),
            ]
        )
        items = payload.get("data", {}).get("item") or []
        by_date = {_date_text(int(item["date_ms"])): item for item in items if item.get("date_ms") is not None}
        if basis not in by_date or d5 not in by_date or d20 not in by_date:
            continue
        current = by_date[basis]
        close = float(current["close_price"])
        close5 = float(by_date[d5]["close_price"])
        close20 = float(by_date[d20]["close_price"])
        prior_dates = sorted(date for date in by_date if date < basis)
        prior_close = float(by_date[prior_dates[-1]]["close_price"]) if prior_dates else None
        result.append(
            {
                "code": code,
                "name": name,
                "close": close,
                "r1": (close / prior_close - 1) * 100 if prior_close else None,
                "r5": (close / close5 - 1) * 100,
                "r20": (close / close20 - 1) * 100,
            }
        )
    return sorted(result, key=lambda item: item["r5"], reverse=True)


def with_hithink_fallback(
    stage: str,
    primary: Callable[[], Any],
    fallback: Callable[[], Any],
    source_state: dict[str, dict[str, Any]],
) -> Any:
    """Run Tushare first, then use Hithink only for an empty/failed stage."""
    try:
        value = primary()
        empty = value is None or (isinstance(value, (list, pd.DataFrame, dict)) and len(value) == 0)
        if not empty:
            source_state[stage] = {"source": "tushare", "fallback_used": False, "fallback_reason": ""}
            return value
        primary_error = "empty_response"
    except Exception as exc:
        primary_error = str(exc)
    try:
        value = fallback()
    except Exception as fallback_exc:
        source_state[stage] = {
            "source": "unavailable",
            "fallback_used": False,
            "fallback_reason": primary_error,
            "fallback_error": str(fallback_exc),
        }
        raise
    source_state[stage] = {
        "source": "hithink",
        "fallback_used": True,
        "fallback_reason": primary_error,
    }
    return value
