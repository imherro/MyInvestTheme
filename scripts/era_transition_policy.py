from __future__ import annotations

from datetime import date
from typing import Any

try:
    from era_evidence_windows import RuleUsageTracker, get_rule, parse_date
except ModuleNotFoundError:
    from scripts.era_evidence_windows import RuleUsageTracker, get_rule, parse_date


VERSION = "era_transition_policy_v1"
FORWARD_ORDER = ["dormant", "incubating", "emerging", "launching", "confirmed", "expanding", "mature"]


def _days(start: str, end: str) -> int:
    left, right = parse_date(start), parse_date(end)
    return (right - left).days if left and right and right >= left else 0


def _skipped(previous: str, desired: str) -> list[str]:
    if previous not in FORWARD_ORDER or desired not in FORWARD_ORDER:
        return []
    left, right = FORWARD_ORDER.index(previous), FORWARD_ORDER.index(desired)
    return FORWARD_ORDER[left + 1 : right] if right > left + 1 else []


def decide_transition(
    previous: str,
    desired: str,
    *,
    observation_date: str,
    stage_entered_at: str,
    rules: dict[str, Any],
    tracker: RuleUsageTracker | None = None,
    target_condition_satisfied: bool = False,
    severe_reverse_evidence: bool = False,
    recovery_satisfied: bool = False,
    restart_satisfied: bool = False,
) -> dict[str, Any]:
    allowed_map = get_rule(rules, "transition_policy.allowed_direct_transitions", tracker)
    minimum_map = get_rule(rules, "minimum_stage_dwell_days", tracker)
    dwell_days = _days(stage_entered_at, observation_date)
    minimum_dwell = int(minimum_map.get(previous, 0))
    dwell_satisfied = dwell_days >= minimum_dwell
    result = {
        "actual_stage": previous,
        "desired_stage": desired,
        "transition_type": "stable" if desired == previous else "blocked",
        "transition_reason_codes": [],
        "skipped_stages": _skipped(previous, desired),
        "transition_allowed": desired == previous,
        "stage_dwell_days": dwell_days,
        "minimum_stage_dwell_days": minimum_dwell,
        "stage_dwell_satisfied": dwell_satisfied,
        "dwell_override": False,
    }
    if desired == previous:
        return result
    if previous == "ended" and desired != "restarting":
        result["transition_reason_codes"] = ["ENDED_REQUIRES_RESTART"]
        return result
    if previous == "declining" and desired == "launching":
        result["transition_reason_codes"] = ["DECLINING_CANNOT_LAUNCH_DIRECTLY"]
        return result
    if desired == "restarting" and not restart_satisfied:
        result["transition_reason_codes"] = ["RESTART_CONDITIONS_NOT_MET"]
        return result
    if desired not in set(allowed_map.get(previous, [])):
        result["transition_reason_codes"] = ["ILLEGAL_STAGE_TARGET"]
        return result
    if previous == "cooling" and desired in {"emerging", "launching", "confirmed", "expanding"} and not recovery_satisfied:
        result["transition_reason_codes"] = ["COOLING_RECOVERY_WINDOW_NOT_MET"]
        return result
    if not dwell_satisfied and not severe_reverse_evidence:
        result["transition_reason_codes"] = ["MINIMUM_STAGE_DWELL_NOT_MET"]
        return result
    if severe_reverse_evidence and not dwell_satisfied:
        result["dwell_override"] = True
        result["transition_reason_codes"].append("SEVERE_REVERSE_EVIDENCE_OVERRIDE")
    skipped = result["skipped_stages"]
    if skipped:
        if not target_condition_satisfied:
            result["transition_reason_codes"].append("TARGET_WINDOW_NOT_SATISFIED")
            return result
        transition_type = "direct_evidence_jump"
        result["transition_reason_codes"].append("TARGET_WINDOW_SATISFIED")
    elif previous == "cooling" and desired not in {"declining", "restarting"}:
        transition_type = "recovery"
        result["transition_reason_codes"].append("RECOVERY_WINDOW_SATISFIED")
    elif desired == "restarting":
        transition_type = "restart"
        result["transition_reason_codes"].append("NEW_CYCLE_DRIVER_CONFIRMED")
    else:
        transition_type = "stable"
    result.update({"actual_stage": desired, "transition_type": transition_type, "transition_allowed": True})
    return result
