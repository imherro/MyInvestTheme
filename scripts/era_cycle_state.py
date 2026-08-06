from __future__ import annotations

from typing import Any


VERSION = "era_cycle_state_v1"
FORMED_STAGES = {"emerging", "launching", "confirmed", "expanding", "mature", "cooling", "declining", "restarting"}


def cycle_identifier(theme_id: str, sequence: int, *, pre_history: bool = False) -> str:
    return f"{theme_id}_cycle_pre_history" if pre_history else f"{theme_id}_cycle_{sequence:03d}"


def new_cycle(theme_id: str, sequence: int, observation_date: str, *, previous_cycle_id: str = "", pre_history: bool = False) -> dict[str, Any]:
    return {
        "cycle_id": cycle_identifier(theme_id, sequence, pre_history=pre_history),
        "cycle_sequence": sequence,
        "cycle_start_observation_date": "" if pre_history else observation_date,
        "cycle_start_status": "before_available_history" if pre_history else "observed",
        "cycle_end_observation_date": "",
        "cycle_peak_date": observation_date,
        "cycle_peak_score": 0.0,
        "cycle_peak_market_score": 0.0,
        "cycle_peak_policy_score": 0.0,
        "cycle_trough_date": observation_date,
        "cycle_trough_score": 100.0,
        "cycle_trough_market_score": 100.0,
        "cycle_status": "active",
        "previous_cycle_id": previous_cycle_id,
        "is_left_censored": pre_history,
    }


def update_cycle(cycle: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    score = float(row.get("era_mainline_score") or 0)
    market = float(row.get("market_score") or 0)
    policy = float(row.get("policy_score") or 0)
    current = dict(cycle)
    if score >= float(current.get("cycle_peak_score") or 0):
        current["cycle_peak_score"], current["cycle_peak_date"] = score, row.get("date", "")
    current["cycle_peak_market_score"] = max(float(current.get("cycle_peak_market_score") or 0), market)
    current["cycle_peak_policy_score"] = max(float(current.get("cycle_peak_policy_score") or 0), policy)
    if score <= float(current.get("cycle_trough_score", 100)):
        current["cycle_trough_score"], current["cycle_trough_date"] = score, row.get("date", "")
    current["cycle_trough_market_score"] = min(float(current.get("cycle_trough_market_score", 100)), market)
    return current
