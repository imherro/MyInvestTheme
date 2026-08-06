from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from era_cycle_state import FORMED_STAGES, new_cycle, update_cycle
    from era_evidence_windows import RuleUsageTracker, deduplicate_observations, evaluate_condition_window, get_rule, history_coverage, parse_date
    from era_transition_policy import FORWARD_ORDER, decide_transition
except ModuleNotFoundError:
    from scripts.era_cycle_state import FORMED_STAGES, new_cycle, update_cycle
    from scripts.era_evidence_windows import RuleUsageTracker, deduplicate_observations, evaluate_condition_window, get_rule, history_coverage, parse_date
    from scripts.era_transition_policy import FORWARD_ORDER, decide_transition


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "config" / "era_lifecycle_rules.json"
VERSION = "era_lifecycle_engine_v3"


def load_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _score(row: dict[str, Any], name: str) -> float:
    value = row.get(name)
    return float(value) if value is not None else 0.0


def _event_count(row: dict[str, Any]) -> int:
    return int(row.get("policy_dimension", {}).get("event_count") or row.get("independent_event_count") or 0)


def _secondary_count(row: dict[str, Any]) -> int:
    return int(row.get("narrative_dimension", {}).get("active_subtheme_count") or row.get("secondary_theme_count") or 0)


def _weak_dimensions(row: dict[str, Any], threshold: float) -> int:
    values = [row.get(name) for name in ("policy_score", "industry_score", "market_score", "narrative_score")]
    return sum(1 for value in values if value is not None and float(value) < threshold)


def _formation(row: dict[str, Any], _: list[dict[str, Any]], rules: dict[str, Any], tracker: RuleUsageTracker | None) -> bool:
    return _score(row, "policy_score") >= get_rule(rules, "formation.minimum_policy_score", tracker) and _event_count(row) >= get_rule(rules, "formation.minimum_independent_events", tracker)


def _confirmation(row: dict[str, Any], _: list[dict[str, Any]], rules: dict[str, Any], tracker: RuleUsageTracker | None) -> bool:
    industry = row.get("industry_score")
    external = _score(row, "market_score") >= get_rule(rules, "confirmation.minimum_market_score", tracker) or (industry is not None and float(industry) >= get_rule(rules, "confirmation.minimum_industry_score", tracker))
    return _score(row, "policy_score") >= get_rule(rules, "confirmation.minimum_policy_score", tracker) and external and _score(row, "era_mainline_score") >= get_rule(rules, "confirmation.minimum_era_score", tracker)


def _expansion(row: dict[str, Any], _: list[dict[str, Any]], rules: dict[str, Any], tracker: RuleUsageTracker | None) -> bool:
    return _score(row, "market_score") >= get_rule(rules, "expansion.minimum_market_score", tracker) and _score(row, "narrative_score") >= get_rule(rules, "expansion.minimum_narrative_score", tracker) and _secondary_count(row) >= get_rule(rules, "expansion.minimum_secondary_theme_count", tracker)


def _cooling(row: dict[str, Any], _: list[dict[str, Any]], rules: dict[str, Any], tracker: RuleUsageTracker | None) -> bool:
    peak_score = float(row.get("cycle_peak_score") or _score(row, "era_mainline_score"))
    peak_market = float(row.get("cycle_peak_market_score") or _score(row, "market_score"))
    score_drop = peak_score - _score(row, "era_mainline_score")
    market_drop = peak_market - _score(row, "market_score")
    relative_drop = score_drop >= get_rule(rules, "cooling.minimum_peak_score_drop", tracker) or market_drop >= get_rule(rules, "cooling.minimum_market_score_drop", tracker)
    absolute_unprotected = _score(row, "era_mainline_score") <= get_rule(rules, "cooling.maximum_current_era_score_for_cooling", tracker) or _score(row, "market_score") <= get_rule(rules, "cooling.maximum_current_market_score_for_cooling", tracker)
    return relative_drop and absolute_unprotected and bool(row.get("cycle_id"))


def _declining(row: dict[str, Any], _: list[dict[str, Any]], rules: dict[str, Any], tracker: RuleUsageTracker | None) -> bool:
    weak_threshold = get_rule(rules, "declining.maximum_weak_dimension_score", tracker)
    return _score(row, "era_mainline_score") <= get_rule(rules, "declining.maximum_era_score", tracker) and _weak_dimensions(row, weak_threshold) >= get_rule(rules, "declining.minimum_weak_dimensions", tracker)


def _ending(row: dict[str, Any], _: list[dict[str, Any]], rules: dict[str, Any], tracker: RuleUsageTracker | None) -> bool:
    weak_threshold = get_rule(rules, "declining.maximum_weak_dimension_score", tracker)
    return _score(row, "era_mainline_score") <= get_rule(rules, "ending.maximum_era_score", tracker) and _weak_dimensions(row, weak_threshold) >= get_rule(rules, "ending.minimum_weak_dimensions", tracker) and not bool(row.get("has_new_reinforcement"))


def _restarting(row: dict[str, Any], _: list[dict[str, Any]], rules: dict[str, Any], tracker: RuleUsageTracker | None) -> bool:
    industry = row.get("industry_score")
    external = max(_score(row, "market_score"), float(industry) if industry is not None else 0.0)
    driver_ids = row.get("new_cycle_driver_ids") or []
    require_driver = bool(get_rule(rules, "restarting.require_new_cycle_driver", tracker))
    strong_events = int(row.get("new_strong_policy_event_count") or 0)
    return (
        int(row.get("new_policy_event_count") or len(driver_ids)) >= get_rule(rules, "restarting.minimum_new_policy_events", tracker)
        and strong_events >= get_rule(rules, "restarting.minimum_new_policy_events", tracker)
        and _score(row, "policy_score") >= get_rule(rules, "restarting.minimum_policy_score", tracker)
        and external >= get_rule(rules, "restarting.minimum_market_or_industry_score", tracker)
        and (not require_driver or bool(driver_ids))
    )


def _confirmation_exit(row: dict[str, Any], _: list[dict[str, Any]], rules: dict[str, Any], tracker: RuleUsageTracker | None) -> bool:
    return _score(row, "era_mainline_score") <= get_rule(rules, "hysteresis.confirmation_exit.maximum_era_score", tracker) and _score(row, "market_score") <= get_rule(rules, "hysteresis.confirmation_exit.maximum_market_score", tracker)


def _cooling_exit(row: dict[str, Any], _: list[dict[str, Any]], rules: dict[str, Any], tracker: RuleUsageTracker | None) -> bool:
    score_recovery = _score(row, "era_mainline_score") - float(row.get("cycle_trough_score") or _score(row, "era_mainline_score"))
    market_recovery = _score(row, "market_score") - float(row.get("cycle_trough_market_score") or _score(row, "market_score"))
    return score_recovery >= get_rule(rules, "hysteresis.cooling_exit.minimum_score_recovery_from_trough", tracker) or market_recovery >= get_rule(rules, "hysteresis.cooling_exit.minimum_market_recovery_from_trough", tracker)


def _declining_exit(row: dict[str, Any], _: list[dict[str, Any]], rules: dict[str, Any], tracker: RuleUsageTracker | None) -> bool:
    changes = row.get("dimension_changes") or {}
    strengthened = sum(1 for name in ("policy_score", "industry_score", "market_score", "narrative_score") if changes.get(name) is not None and float(changes[name]) > 0)
    return _score(row, "era_mainline_score") >= get_rule(rules, "hysteresis.declining_exit.minimum_era_score", tracker) and strengthened >= get_rule(rules, "hysteresis.declining_exit.minimum_strengthened_dimensions", tracker)


def condition_windows(history: list[dict[str, Any]], *, rules: dict[str, Any] | None = None, tracker: RuleUsageTracker | None = None) -> dict[str, dict[str, Any]]:
    active = rules or load_rules()
    specs = {
        "formation": (_formation, 1, get_rule(active, "formation.minimum_duration_days", tracker)),
        "confirmation": (_confirmation, get_rule(active, "confirmation.minimum_consecutive_observations", tracker), get_rule(active, "confirmation.minimum_duration_days", tracker)),
        "expansion": (_expansion, get_rule(active, "expansion.minimum_consecutive_observations", tracker), 0),
        "cooling": (_cooling, get_rule(active, "cooling.minimum_consecutive_observations", tracker), get_rule(active, "cooling.minimum_duration_days", tracker)),
        "declining": (_declining, get_rule(active, "declining.minimum_consecutive_observations", tracker), get_rule(active, "declining.minimum_duration_days", tracker)),
        "ending": (_ending, get_rule(active, "ending.minimum_consecutive_observations", tracker), 0),
        "restarting": (_restarting, 1, get_rule(active, "restarting.minimum_duration_days", tracker)),
        "confirmation_exit": (_confirmation_exit, get_rule(active, "hysteresis.confirmation_exit.minimum_consecutive_observations", tracker), get_rule(active, "hysteresis.confirmation_exit.minimum_duration_days", tracker)),
        "cooling_exit": (_cooling_exit, get_rule(active, "hysteresis.cooling_exit.minimum_consecutive_observations", tracker), get_rule(active, "hysteresis.cooling_exit.minimum_duration_days", tracker)),
        "declining_exit": (_declining_exit, 1, get_rule(active, "hysteresis.declining_exit.minimum_duration_days", tracker)),
    }
    return {name: evaluate_condition_window(history, lambda row, prior, fn=predicate: fn(row, prior, active, tracker), int(observations), int(days)) for name, (predicate, observations, days) in specs.items()}


def _growth_30d(history: list[dict[str, Any]]) -> float:
    latest = history[-1]
    latest_date = parse_date(latest.get("date"))
    candidates = [row for row in history if latest_date and parse_date(row.get("date")) and (latest_date - parse_date(row.get("date"))).days >= 30]
    baseline = candidates[-1] if candidates else history[0]
    return _score(latest, "era_mainline_score") - _score(baseline, "era_mainline_score")


def momentum_state(history: list[dict[str, Any]]) -> str:
    if len(history) < 2:
        return "unknown"
    previous, current = history[-2], history[-1]
    delta = _score(current, "era_mainline_score") - _score(previous, "era_mainline_score")
    market_delta = _score(current, "market_score") - _score(previous, "market_score")
    if delta >= 3 or market_delta >= 4:
        return "recovering" if len(history) >= 3 and _score(previous, "era_mainline_score") < _score(history[-3], "era_mainline_score") else "strengthening"
    if delta <= -3 or market_delta <= -4:
        return "marginally_weakening"
    return "stable"


def _severe_reverse(history: list[dict[str, Any]]) -> bool:
    if len(history) < 2:
        return False
    previous, current = history[-2], history[-1]
    return (_score(previous, "era_mainline_score") - _score(current, "era_mainline_score") >= 20 and _score(previous, "market_score") - _score(current, "market_score") >= 20 and _weak_dimensions(current, 40) >= 2) or int(current.get("policy_dimension", {}).get("restrictive_event_count") or 0) > 0


def target_stage(snapshot: dict[str, Any], previous_stage: str = "dormant", *, windows: dict[str, Any] | None = None, history: list[dict[str, Any]] | None = None, rules: dict[str, Any] | None = None, tracker: RuleUsageTracker | None = None) -> tuple[str, list[str]]:
    active, rows = rules or load_rules(), history or [snapshot]
    state = windows or condition_windows(rows, rules=active, tracker=tracker)
    if snapshot.get("source_lifecycle_state") == "legacy_tail":
        return "cooling", ["旧政策贡献仍可识别，当前轮次证据处于降温观察"]
    if previous_stage == "ended":
        return ("restarting", ["新的独立周期驱动力持续满足重启条件"]) if state["restarting"]["currently_met"] else ("ended", ["结束后尚无完整新周期驱动力"])
    if previous_stage in {"emerging", "launching", "confirmed", "expanding", "mature"} and _severe_reverse(rows):
        return "cooling", ["多维证据快速崩塌或限制性政策触发严重反向证据豁免"]
    if previous_stage == "declining":
        declining_start = parse_date(snapshot.get("declining_start_date"))
        current_date = parse_date(snapshot.get("date"))
        days_after = (current_date - declining_start).days if current_date and declining_start else 0
        reinforcement_date = parse_date(snapshot.get("last_reinforcement_observation_date"))
        days_since_reinforcement = (current_date - reinforcement_date).days if current_date and reinforcement_date else 999
        if state["ending"]["currently_met"] and days_after >= get_rule(active, "ending.minimum_days_after_declining_start", tracker) and days_since_reinforcement >= get_rule(active, "ending.minimum_days_since_last_reinforcement", tracker):
            return "ended", ["结束条件持续成立，且正式衰退和无强化周期均达到门槛"]
        if state["declining_exit"]["currently_met"]:
            return "cooling", ["衰退后至少两个维度持续恢复，但尚未构成新周期"]
        return "declining", ["正式衰退条件尚未解除"]
    if state["declining"]["currently_met"]:
        return "declining", ["至少两个有效维度持续转弱，综合分低于衰退门槛"]
    if previous_stage == "cooling":
        if state["cooling_exit"]["currently_met"]:
            target = str(snapshot.get("recovery_target_stage") or "launching")
            return target, ["从本轮低点持续恢复并达到降温退出门槛"]
        return "cooling", ["恢复窗口尚未持续满足，维持降温观察"]
    if state["cooling"]["currently_met"] and previous_stage in {"emerging", "launching", "confirmed", "expanding", "mature"}:
        if previous_stage in {"confirmed", "expanding", "mature"} and not state["confirmation_exit"]["currently_met"] and not _severe_reverse(rows):
            return previous_stage, ["相对峰值下降，但绝对证据尚未满足确认退出迟滞条件"]
        return "cooling", ["当前轮次分数或市场分相对峰值持续明显下降"]
    if previous_stage in {"emerging", "launching", "confirmed", "expanding", "mature"} and int(state["cooling"].get("consecutive_observations") or 0) > 0:
        return previous_stage, ["本轮峰值后出现边际转弱，但降温持续窗口尚未满足"]
    confirmed_start = parse_date(state["confirmation"].get("first_qualified_date"))
    current_date = parse_date(snapshot.get("date"))
    if confirmed_start and current_date and (current_date - confirmed_start).days >= get_rule(active, "maturity.minimum_confirmed_duration_days", tracker) and _score(snapshot, "era_mainline_score") >= get_rule(active, "maturity.minimum_era_score", tracker) and _growth_30d(rows) <= get_rule(active, "maturity.maximum_score_growth_30d", tracker):
        return "mature", ["确认状态持续达到成熟周期，绝对证据仍强且30日增速趋缓"]
    if state["confirmation"]["currently_met"] and state["expansion"]["currently_met"]:
        return "expanding", ["确认条件持续成立，市场广度与官方战略叙事继续扩散"]
    if state["confirmation"]["currently_met"]:
        return "confirmed", ["政策、市场或产业及综合分持续达到确认门槛"]
    raw_confirmation = _confirmation(snapshot, rows, active, tracker)
    if raw_confirmation or (state["formation"]["currently_met"] and (_score(snapshot, "market_score") >= 35 or snapshot.get("industry_score") is not None)):
        return "launching", ["多层证据开始响应，但确认持续周期尚未满足"]
    if state["formation"]["currently_met"]:
        return "emerging", ["政策方向与独立事件持续达到形成条件，外部确认仍不足"]
    if _score(snapshot, "policy_score") > 0 or _score(snapshot, "market_score") > 0 or _event_count(snapshot) > 0:
        return "incubating", ["至少一维已有早期信号，但形成持续周期尚未满足"]
    return "dormant", ["尚无结构性证据或有效数据不足"]


def apply_transition(previous: str, desired: str, *, rules: dict[str, Any] | None = None) -> tuple[str, bool]:
    active = rules or load_rules()
    decision = decide_transition(previous, desired, observation_date="2099-01-01", stage_entered_at="2000-01-01", rules=active, target_condition_satisfied=True, recovery_satisfied=True, restart_satisfied=desired == "restarting")
    return decision["actual_stage"], bool(decision["transition_allowed"])


def _left_censoring(history: list[dict[str, Any]], rules: dict[str, Any], tracker: RuleUsageTracker | None) -> dict[str, Any]:
    if not history:
        return {"is_left_censored": False, "left_censoring_reasons": [], "left_censoring_evidence": []}
    first = history[0]
    first_date = parse_date(first.get("date"))
    events = first.get("policy_dimension", {}).get("events") or []
    event_dates = [parse_date(event.get("event_date")) for event in events]
    event_dates = [value for value in event_dates if value]
    earliest = min(event_dates) if event_dates else None
    pre_days = (first_date - earliest).days if first_date and earliest else 0
    initial_score = _score(first, "era_mainline_score")
    initial_launching = _confirmation(first, [first], rules, tracker) or (_formation(first, [first], rules, tracker) and _score(first, "market_score") >= 35)
    qualifies = initial_launching and initial_score >= get_rule(rules, "left_censoring.minimum_initial_era_score", tracker) and pre_days >= get_rule(rules, "left_censoring.minimum_pre_history_evidence_days", tracker)
    get_rule(rules, "left_censoring.minimum_initial_stage", tracker)
    return {
        "is_left_censored": qualifies,
        "left_censoring_reasons": ["INITIAL_ADVANCED_STAGE_WITH_PRE_HISTORY_EVIDENCE"] if qualifies else [],
        "left_censoring_evidence": ([{"earliest_pre_history_event_date": earliest.isoformat(), "days_before_history": pre_days, "initial_era_score": initial_score}] if qualifies and earliest else []),
    }


def _reinforcement_signal(row: dict[str, Any], rules: dict[str, Any], tracker: RuleUsageTracker | None) -> bool:
    changes = row.get("dimension_changes") or {}
    thresholds = {
        "policy_score": get_rule(rules, "reinforcement.minimum_policy_score_increase", tracker),
        "market_score": get_rule(rules, "reinforcement.minimum_market_score_increase", tracker),
        "industry_score": get_rule(rules, "reinforcement.minimum_industry_score_increase", tracker),
        "narrative_score": get_rule(rules, "reinforcement.minimum_narrative_score_increase", tracker),
    }
    strengthened = sum(1 for name, threshold in thresholds.items() if changes.get(name) is not None and float(changes[name]) >= threshold)
    composite = float(changes.get("era_mainline_score") or 0) >= get_rule(rules, "reinforcement.minimum_composite_score_increase", tracker)
    return composite or strengthened >= get_rule(rules, "reinforcement.minimum_strengthened_dimensions", tracker)


def enrich_lifecycle(history: list[dict[str, Any]], *, rules: dict[str, Any] | None = None, tracker: RuleUsageTracker | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    active = rules or load_rules()
    rows = deduplicate_observations(history)
    labels, stages = get_rule(active, "labels", tracker), get_rule(active, "stages", tracker)
    censoring = _left_censoring(rows, active, tracker)
    previous, stage_entered_at = "dormant", rows[0].get("date", "") if rows else ""
    transitions: list[dict[str, Any]] = []
    output: list[dict[str, Any]] = []
    cycle: dict[str, Any] | None = None
    cycle_sequence = 0
    highest_qualified = "dormant"
    last_reinforcement_date = ""
    ended_event_ids: set[str] = set()
    last_attempt_signature: tuple[Any, ...] | None = None
    if censoring["is_left_censored"] and rows:
        cycle = new_cycle(str(rows[0].get("theme_id") or "theme"), 0, rows[0]["date"], pre_history=True)
    for index, snapshot in enumerate(rows):
        row = dict(snapshot)
        events = row.get("policy_dimension", {}).get("events") or []
        current_event_ids = {str(event.get("event_id") or "") for event in events if event.get("event_id")}
        new_ids = sorted(current_event_ids - ended_event_ids) if previous in {"ended", "declining"} else []
        min_strength = get_rule(active, "restarting.minimum_event_strength", tracker)
        strong_new = [event for event in events if event.get("event_id") in new_ids and float(event.get("strength") or 0) >= min_strength]
        row.update({"new_cycle_driver_ids": new_ids, "new_cycle_driver_reasons": [str(event.get("title") or event.get("event_id")) for event in strong_new], "new_policy_event_count": len(new_ids), "new_strong_policy_event_count": len(strong_new)})
        row["has_new_reinforcement"] = _reinforcement_signal(row, active, tracker)
        if row["has_new_reinforcement"]:
            last_reinforcement_date = row["date"]
        row["last_reinforcement_observation_date"] = last_reinforcement_date
        if cycle:
            cycle = update_cycle(cycle, row)
            row.update(cycle)
        else:
            row.update({"cycle_id": "", "cycle_sequence": 0, "cycle_peak_score": _score(row, "era_mainline_score"), "cycle_peak_market_score": _score(row, "market_score"), "cycle_trough_score": _score(row, "era_mainline_score"), "cycle_trough_market_score": _score(row, "market_score")})
        observed = output + [row]
        windows = condition_windows(observed, rules=active, tracker=tracker)
        if previous == "declining":
            decline_window = windows["declining"]
            row["declining_start_date"] = decline_window.get("first_qualified_start_date", "")
        recovery_target = "confirmed" if highest_qualified in {"confirmed", "expanding", "mature"} else ("launching" if highest_qualified == "launching" else "emerging")
        row.update({"previous_qualified_stage": previous, "highest_qualified_stage_in_cycle": highest_qualified, "recovery_target_stage": recovery_target})
        desired, reasons = target_stage(row, previous, windows=windows, history=observed, rules=active, tracker=tracker)
        severe = _severe_reverse(observed) or row.get("source_lifecycle_state") == "legacy_tail"
        target_satisfied = bool(windows.get(desired, {}).get("currently_met")) or desired in {"mature", "ended"}
        if desired == "launching":
            target_satisfied = _confirmation(row, observed, active, tracker) or (windows["formation"]["currently_met"] and (_score(row, "market_score") >= 35 or row.get("industry_score") is not None))
        elif desired == "emerging":
            target_satisfied = windows["formation"]["currently_met"]
        decision = decide_transition(previous, desired, observation_date=row["date"], stage_entered_at=stage_entered_at, rules=active, tracker=tracker, target_condition_satisfied=target_satisfied, severe_reverse_evidence=severe, recovery_satisfied=windows["cooling_exit"]["currently_met"], restart_satisfied=windows["restarting"]["currently_met"])
        stage = decision["actual_stage"]
        if stage not in stages:
            stage, decision["transition_type"] = "uncertain", "uncertain"
            decision["transition_reason_codes"].append("STAGE_NOT_CONFIGURED")
        if stage != previous:
            if not cycle and stage in FORMED_STAGES:
                cycle_sequence = max(1, cycle_sequence + 1)
                cycle = new_cycle(str(row.get("theme_id") or "theme"), cycle_sequence, row["date"])
            elif previous == "ended" and stage == "restarting":
                old_id = str(cycle.get("cycle_id") if cycle else "")
                cycle_sequence = max(1, cycle_sequence + 1)
                cycle = new_cycle(str(row.get("theme_id") or "theme"), cycle_sequence, row["date"], previous_cycle_id=old_id)
            stage_entered_at = row["date"]
        if cycle:
            cycle = update_cycle(cycle, row)
            if stage == "ended":
                cycle.update({"cycle_status": "ended", "cycle_end_observation_date": row["date"]})
                if previous != "ended":
                    ended_event_ids = current_event_ids
            row.update(cycle)
        if stage in FORWARD_ORDER and FORWARD_ORDER.index(stage) > FORWARD_ORDER.index(highest_qualified):
            highest_qualified = stage
        entered = stage_entered_at
        current_date, entered_date = parse_date(row["date"]), parse_date(entered)
        dwell_days = (current_date - entered_date).days if current_date and entered_date else 0
        minimum_dwell = int(get_rule(active, "minimum_stage_dwell_days", tracker).get(stage, 0))
        row.update({
            "evidence_stage": stage, "lifecycle_stage": stage, "lifecycle_stage_label": labels.get(stage, stage),
            "stage_reasons": reasons, "condition_windows": windows, "momentum_state": momentum_state(observed),
            "stage_entered_at": entered, "stage_dwell_days": dwell_days, "minimum_stage_dwell_days": minimum_dwell,
            "stage_dwell_satisfied": dwell_days >= minimum_dwell, "transition_type": decision["transition_type"],
            "transition_reason_codes": decision["transition_reason_codes"], "skipped_stages": decision["skipped_stages"],
            "transition_allowed": decision["transition_allowed"], "direct_transition": decision["transition_allowed"],
            "previous_qualified_stage": previous, "highest_qualified_stage_in_cycle": highest_qualified,
            **censoring,
        })
        attempt_signature = (previous, desired, stage, decision["transition_type"], tuple(decision["transition_reason_codes"]))
        if desired != previous and (decision["transition_allowed"] or attempt_signature != last_attempt_signature):
            transitions.append({
                "theme_id": row.get("theme_id", ""), "from_stage": previous, "to_stage": stage,
                "desired_stage": desired, "change_date": row["date"], "change_reasons": reasons,
                "transition_type": decision["transition_type"], "transition_reason_codes": decision["transition_reason_codes"],
                "skipped_stages": decision["skipped_stages"], "transition_allowed": decision["transition_allowed"],
                "stage_dwell_days": decision["stage_dwell_days"], "minimum_stage_dwell_days": decision["minimum_stage_dwell_days"],
                "stage_dwell_satisfied": decision["stage_dwell_satisfied"], "dwell_override": decision["dwell_override"],
                "cycle_id": row.get("cycle_id", ""), "cycle_sequence": row.get("cycle_sequence", 0),
                "dimension_changes": row.get("dimension_changes", {}), "confidence": row.get("current_state_confidence", 0),
                "legal_direct_transition": decision["transition_allowed"],
            })
            last_attempt_signature = attempt_signature
        output.append(row)
        previous = stage
    return output, transitions


def analyze_reinforcements(history: list[dict[str, Any]], events: list[dict[str, Any]], *, rules: dict[str, Any] | None = None, tracker: RuleUsageTracker | None = None) -> dict[str, Any]:
    active = rules or load_rules()
    fields = {
        "policy": ("policy_score", get_rule(active, "reinforcement.minimum_policy_score_increase", tracker)),
        "market": ("market_score", get_rule(active, "reinforcement.minimum_market_score_increase", tracker)),
        "industry": ("industry_score", get_rule(active, "reinforcement.minimum_industry_score_increase", tracker)),
        "narrative": ("narrative_score", get_rule(active, "reinforcement.minimum_narrative_score_increase", tracker)),
    }
    dates = {name: "" for name in fields}
    changes_by_date: dict[str, dict[str, Any]] = {}
    multi_date, composite_date = "", ""
    for row in history:
        changes = row.get("dimension_changes") or {}
        strengthened = 0
        for name, (field, threshold) in fields.items():
            value = changes.get(field)
            if value is not None and float(value) >= threshold:
                dates[name] = row["date"]
                strengthened += 1
        composite = float(changes.get("era_mainline_score") or 0) >= get_rule(active, "reinforcement.minimum_composite_score_increase", tracker)
        if composite:
            composite_date, changes_by_date[row["date"]] = row["date"], dict(changes)
        if strengthened >= get_rule(active, "reinforcement.minimum_strengthened_dimensions", tracker):
            multi_date, changes_by_date[row["date"]] = row["date"], dict(changes)
    cycle_start = str(history[-1].get("cycle_start_observation_date") or history[0].get("date") or "") if history else ""
    min_strength = get_rule(active, "reinforcement.minimum_policy_event_strength", tracker)
    policy_events = [event for event in events if event.get("direction") == "supportive" and float(event.get("strength") or 0) >= min_strength and str(event.get("event_date") or "") >= cycle_start]
    policy_event_date = max((str(event.get("event_date") or "") for event in policy_events), default="")
    if multi_date:
        selected_date, selected_type = multi_date, "multi_dimension"
    elif policy_event_date:
        selected_date, selected_type = policy_event_date, "policy_event"
    elif composite_date:
        selected_date, selected_type = composite_date, "composite_score"
    elif dates["market"]:
        selected_date, selected_type = dates["market"], "market_confirmation"
    else:
        selected_date, selected_type = "", "none"
    reason_map = {
        "multi_dimension": "至少两个维度达到强化增量门槛",
        "policy_event": "当前周期内出现达到强度门槛的支持性政策事件",
        "composite_score": "时代主线综合分达到强化增量门槛",
        "market_confirmation": "市场确认分达到强化增量门槛",
        "none": "近期无达到定义门槛的强化事件",
    }
    return {
        "latest_policy_reinforcement_event_date": policy_event_date,
        "latest_market_reinforcement_date": dates["market"],
        "latest_industry_reinforcement_date": dates["industry"],
        "latest_narrative_reinforcement_date": dates["narrative"],
        "latest_composite_reinforcement_date": composite_date,
        "latest_reinforcement_date": selected_date,
        "latest_reinforcement_type": selected_type,
        "latest_reinforcement_reasons": [reason_map[selected_type]],
        "latest_reinforcement_changes": changes_by_date.get(selected_date, {}),
    }


def lifecycle_dates(history: list[dict[str, Any]], events: list[dict[str, Any]], basis_date: str, *, rules: dict[str, Any] | None = None, tracker: RuleUsageTracker | None = None) -> dict[str, Any]:
    rows = deduplicate_observations(history)
    coverage = history_coverage(rows)
    latest = rows[-1] if rows else {}
    windows = latest.get("condition_windows") or condition_windows(rows, rules=rules, tracker=tracker)
    coverage["is_left_censored"] = bool(latest.get("is_left_censored"))
    event_dates = sorted(filter(None, (parse_date(event.get("event_date")) for event in events)))
    earliest_observation = parse_date(coverage["first_available_observation_date"])
    earliest = min(([event_dates[0]] if event_dates else []) + ([earliest_observation] if earliest_observation else []), default=None)
    formation = windows["formation"]
    estimated_start = "" if coverage["is_left_censored"] else formation.get("first_qualified_start_date", "")
    decided_at = "" if coverage["is_left_censored"] else formation.get("first_qualified_date", "")
    first_confirmed = next((row for row in rows if row.get("lifecycle_stage") in {"confirmed", "expanding", "mature"}), None)
    first_cooling = next((row for row in rows if row.get("lifecycle_stage") == "cooling"), None)
    first_declining = next((row for row in rows if row.get("lifecycle_stage") == "declining"), None)
    first_ended = next((row for row in rows if row.get("lifecycle_stage") == "ended"), None)
    decline_window = (first_declining or {}).get("condition_windows", {}).get("declining", {})
    basis, start = parse_date(basis_date), parse_date(estimated_start)
    return {
        "earliest_evidence_date": earliest.isoformat() if earliest else "", "signal_start_date": earliest.isoformat() if earliest else "",
        "formation_start_date": "" if coverage["is_left_censored"] else formation.get("first_met_date", ""),
        "estimated_start_date": estimated_start, "start_date_decided_at": decided_at, "model_decided_at": decided_at,
        "start_date_status": "before_available_history" if coverage["is_left_censored"] else ("estimated" if estimated_start else "insufficient_evidence"),
        "confirmation_date": str((first_confirmed or {}).get("date") or ""),
        "latest_policy_event_date": event_dates[-1].isoformat() if event_dates else "",
        "weakening_start_date": str((first_cooling or {}).get("date") or ""),
        "declining_condition_start_date": str(decline_window.get("first_qualified_start_date") or ""),
        "declining_start_date": str(decline_window.get("first_qualified_start_date") or ""),
        "declining_decided_at": str(decline_window.get("first_qualified_date") or ""),
        "estimated_end_date": str((first_ended or {}).get("date") or ""),
        "duration_days": (basis - start).days if basis and start and basis >= start else 0,
        "condition_windows": windows, "history_coverage": coverage,
        "left_censoring_reasons": latest.get("left_censoring_reasons") or [],
        "left_censoring_evidence": latest.get("left_censoring_evidence") or [],
    }
