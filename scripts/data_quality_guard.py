from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "config" / "data_quality_rules.json"
SCORING_VERSION = "live_report_data_guard_v2"


def round4(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return round(number, 4)


def load_data_quality_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def empty_dataframe_with_columns(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=list(columns))


def ensure_dataframe_columns(
    df: pd.DataFrame | None,
    required_columns: list[str],
    defaults: dict[str, Any] | None = None,
) -> pd.DataFrame:
    base_defaults = defaults or {}
    if df is None:
        return empty_dataframe_with_columns(required_columns)
    result = df.copy()
    for column in required_columns:
        if column not in result.columns:
            result[column] = base_defaults.get(column)
    ordered_columns = list(required_columns) + [column for column in result.columns if column not in required_columns]
    return result.loc[:, ordered_columns]


def clean_records_safe(
    df: pd.DataFrame | None,
    limit: int,
    columns: list[str],
    defaults: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    safe = ensure_dataframe_columns(df, columns, defaults)
    if safe.empty:
        return []
    rows: list[dict[str, Any]] = []
    for _, row in safe.head(limit).iterrows():
        item: dict[str, Any] = {}
        for column in columns:
            value = row.get(column)
            if pd.isna(value):
                item[column] = None
            elif hasattr(value, "item"):
                item[column] = value.item()
            elif isinstance(value, float):
                item[column] = round(value, 6)
            else:
                item[column] = value
        rows.append(item)
    return rows


def build_stage_status(
    stage: str,
    status: str,
    required: bool,
    row_count: int = 0,
    missing_columns: list[str] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    missing = list(missing_columns or [])
    fallback_used = (not required) and status == "degraded"
    if status == "pass":
        message = ""
    elif required:
        message = "Required data stage failed; report generation must stop."
    else:
        message = "Optional data stage returned empty or missing required columns; empty schema fallback was used."
    if error and not required:
        message = "Optional data stage failed; fallback was used."
    return {
        "stage": stage,
        "required": bool(required),
        "status": status,
        "row_count": int(row_count),
        "missing_columns": missing,
        "error": error or "",
        "fallback_used": bool(fallback_used),
        "message": message,
    }


def _row_count(value: Any) -> int:
    if isinstance(value, pd.DataFrame):
        return int(len(value))
    if isinstance(value, tuple) and value:
        return _row_count(value[0])
    if isinstance(value, list):
        return int(len(value))
    if isinstance(value, dict):
        rows = value.get("rows")
        if isinstance(rows, int):
            return rows
        return 1 if value else 0
    return 0


def _missing_columns(value: Any, required_columns: list[str] | None) -> list[str]:
    if not required_columns:
        return []
    if isinstance(value, pd.DataFrame):
        return [column for column in required_columns if column not in value.columns]
    if isinstance(value, tuple) and value and isinstance(value[0], pd.DataFrame):
        return [column for column in required_columns if column not in value[0].columns]
    return []


def _ensure_columns_for_value(value: Any, required_columns: list[str] | None, defaults: dict[str, Any] | None) -> Any:
    if not required_columns:
        return value
    if isinstance(value, pd.DataFrame):
        return ensure_dataframe_columns(value, required_columns, defaults)
    if isinstance(value, tuple) and value and isinstance(value[0], pd.DataFrame):
        items = list(value)
        items[0] = ensure_dataframe_columns(items[0], required_columns, defaults)
        return tuple(items)
    return value


def run_optional_stage(
    stage: str,
    fn: Callable[[], Any],
    fallback: Any,
    required_columns: list[str] | None = None,
    defaults: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, Any]]:
    try:
        value = fn()
    except Exception as exc:
        fallback_value = _ensure_columns_for_value(deepcopy(fallback), required_columns, defaults)
        return fallback_value, build_stage_status(stage, "degraded", False, _row_count(fallback_value), required_columns or [], str(exc))

    missing = _missing_columns(value, required_columns)
    row_count = _row_count(value)
    if row_count == 0 or missing:
        fallback_value = _ensure_columns_for_value(deepcopy(fallback), required_columns, defaults)
        status = build_stage_status(stage, "degraded", False, _row_count(fallback_value), missing, None)
        return fallback_value, status

    value = _ensure_columns_for_value(value, required_columns, defaults)
    return value, build_stage_status(stage, "pass", False, _row_count(value), [], None)


def build_data_quality_summary(stage_statuses: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = [dict(status) for status in stage_statuses]
    required = [status for status in statuses if status.get("required")]
    optional = [status for status in statuses if not status.get("required")]
    required_failure_count = sum(1 for status in required if status.get("status") == "fail")
    optional_failure_count = sum(1 for status in optional if status.get("status") in {"degraded", "fail"})
    empty_optional_stage_count = sum(1 for status in optional if int(status.get("row_count") or 0) == 0)
    missing_column_stage_count = sum(1 for status in statuses if status.get("missing_columns"))
    if required_failure_count:
        status_value = "fail"
    elif optional_failure_count:
        status_value = "degraded"
    else:
        status_value = "pass"
    return {
        "scoring_version": SCORING_VERSION,
        "status": status_value,
        "required_stage_count": len(required),
        "optional_stage_count": len(optional),
        "required_failure_count": required_failure_count,
        "optional_failure_count": optional_failure_count,
        "empty_optional_stage_count": empty_optional_stage_count,
        "missing_column_stage_count": missing_column_stage_count,
        "stage_statuses": statuses,
    }


def assert_required_data_quality(data_quality_summary: dict[str, Any]) -> None:
    if int(data_quality_summary.get("required_failure_count") or 0) > 0 or data_quality_summary.get("status") == "fail":
        raise RuntimeError("Required data quality stage failed; report generation stopped.")
