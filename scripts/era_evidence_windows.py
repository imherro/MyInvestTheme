from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable


VERSION = "era_evidence_windows_v1"


class RuleUsageTracker:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def record(self, path: str) -> None:
        self._counts[path] = self._counts.get(path, 0) + 1

    def report(self) -> dict[str, dict[str, Any]]:
        return {path: {"used": count > 0, "access_count": count} for path, count in sorted(self._counts.items())}


def get_rule(rules: dict[str, Any], path: str, tracker: RuleUsageTracker | None = None) -> Any:
    value: Any = rules
    for part in path.split("."):
        value = value[part]
    if tracker is not None:
        tracker.record(path)
    return value


def parse_date(value: Any) -> date | None:
    try:
        return datetime.strptime(str(value or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def deduplicate_observations(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the final deterministic observation for each basis date."""
    by_date: dict[str, dict[str, Any]] = {}
    for row in sorted(history, key=lambda item: (str(item.get("observation_date") or item.get("date") or ""), str(item.get("observation_id") or ""))):
        observation_date = str(row.get("observation_date") or row.get("date") or "")[:10]
        if observation_date:
            by_date[observation_date] = {**row, "observation_date": observation_date, "date": observation_date}
    return [by_date[key] for key in sorted(by_date)]


def evaluate_condition_window(
    history: list[dict[str, Any]],
    predicate: Callable[[dict[str, Any], list[dict[str, Any]]], bool],
    minimum_consecutive_observations: int = 1,
    minimum_duration_days: int = 0,
) -> dict[str, Any]:
    rows = deduplicate_observations(history)
    run: list[dict[str, Any]] = []
    completed: list[dict[str, Any]] = []
    first_met_date = ""
    confirmation_date = ""
    for index, row in enumerate(rows):
        prior = rows[: index + 1]
        if run and row.get("cycle_id") and run[-1].get("cycle_id") != row.get("cycle_id"):
            run = []
        if predicate(row, prior):
            run.append(row)
            start = parse_date(run[0]["observation_date"])
            end = parse_date(run[-1]["observation_date"])
            duration = (end - start).days if start and end else 0
            if len(run) >= minimum_consecutive_observations and duration >= minimum_duration_days:
                first_met_date = first_met_date or run[0]["observation_date"]
                confirmation_date = confirmation_date or run[-1]["observation_date"]
                completed = list(run)
        else:
            run = []
    current_start = run[0]["observation_date"] if run else ""
    current_end = run[-1]["observation_date"] if run else ""
    start_date = parse_date(current_start)
    end_date = parse_date(current_end)
    duration_days = (end_date - start_date).days if start_date and end_date else 0
    currently_met = bool(run) and len(run) >= minimum_consecutive_observations and duration_days >= minimum_duration_days
    return {
        "first_met_date": current_start,
        "last_met_date": current_end,
        "consecutive_observations": len(run),
        "duration_days": duration_days,
        "currently_met": currently_met,
        "first_qualified_start_date": first_met_date,
        "first_qualified_date": confirmation_date,
        "qualified_observation_count": len(completed),
    }


def history_coverage(history: list[dict[str, Any]]) -> dict[str, Any]:
    rows = deduplicate_observations(history)
    first = parse_date(rows[0]["observation_date"]) if rows else None
    last = parse_date(rows[-1]["observation_date"]) if rows else None
    gaps = []
    for left, right in zip(rows, rows[1:]):
        left_date = parse_date(left["observation_date"])
        right_date = parse_date(right["observation_date"])
        if left_date and right_date:
            gaps.append((right_date - left_date).days)
    return {
        "first_available_observation_date": first.isoformat() if first else "",
        "last_available_observation_date": last.isoformat() if last else "",
        "observation_count": len(rows),
        "coverage_days": (last - first).days if first and last else 0,
        "maximum_observation_gap_days": max(gaps, default=0),
        "missing_interval_count": sum(1 for gap in gaps if gap > 7),
        "is_left_censored": False,
    }
