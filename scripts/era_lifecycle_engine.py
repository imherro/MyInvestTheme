from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Any

try:
    from era_evidence_windows import deduplicate_observations, evaluate_condition_window, history_coverage, parse_date
except ModuleNotFoundError:
    from scripts.era_evidence_windows import deduplicate_observations, evaluate_condition_window, history_coverage, parse_date


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "config" / "era_lifecycle_rules.json"
VERSION = "era_lifecycle_engine_v2"


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
    values = [row.get("policy_score"), row.get("industry_score"), row.get("market_score"), row.get("narrative_score")]
    return sum(1 for value in values if value is not None and float(value) < threshold)


def _formation(row: dict[str, Any], _: list[dict[str, Any]], rules: dict[str, Any]) -> bool:
    spec = rules["formation"]
    return _score(row, "policy_score") >= spec["minimum_policy_score"] and _event_count(row) >= spec["minimum_independent_events"]


def _confirmation(row: dict[str, Any], _: list[dict[str, Any]], rules: dict[str, Any]) -> bool:
    spec = rules["confirmation"]
    industry = row.get("industry_score")
    external = _score(row, "market_score") >= spec["minimum_market_score"] or (industry is not None and float(industry) >= spec["minimum_industry_score"])
    return _score(row, "policy_score") >= spec["minimum_policy_score"] and external and _score(row, "era_mainline_score") >= spec["minimum_era_score"]


def _expansion(row: dict[str, Any], _: list[dict[str, Any]], rules: dict[str, Any]) -> bool:
    spec = rules["expansion"]
    return _score(row, "market_score") >= spec["minimum_market_score"] and _score(row, "narrative_score") >= spec["minimum_narrative_score"] and _secondary_count(row) >= spec["minimum_secondary_theme_count"]


def _cooling(row: dict[str, Any], prior: list[dict[str, Any]], rules: dict[str, Any]) -> bool:
    spec = rules["cooling"]
    peak_score = max((_score(item, "era_mainline_score") for item in prior), default=0.0)
    peak_market = max((_score(item, "market_score") for item in prior), default=0.0)
    return peak_score - _score(row, "era_mainline_score") >= spec["minimum_peak_score_drop"] or peak_market - _score(row, "market_score") >= spec["minimum_market_score_drop"]


def _declining(row: dict[str, Any], _: list[dict[str, Any]], rules: dict[str, Any]) -> bool:
    spec = rules["declining"]
    return _score(row, "era_mainline_score") <= spec["maximum_era_score"] and _weak_dimensions(row, spec["maximum_weak_dimension_score"]) >= spec["minimum_weak_dimensions"]


def _ending(row: dict[str, Any], _: list[dict[str, Any]], rules: dict[str, Any]) -> bool:
    spec = rules["ending"]
    weak_threshold = rules["declining"]["maximum_weak_dimension_score"]
    return _score(row, "era_mainline_score") <= spec["maximum_era_score"] and _weak_dimensions(row, weak_threshold) >= spec["minimum_weak_dimensions"] and not bool(row.get("has_new_reinforcement"))


def _restarting(row: dict[str, Any], _: list[dict[str, Any]], rules: dict[str, Any]) -> bool:
    spec = rules["restarting"]
    industry = row.get("industry_score")
    external = max(_score(row, "market_score"), float(industry) if industry is not None else 0.0)
    return int(row.get("new_policy_event_count") or 0) >= spec["minimum_new_policy_events"] and _score(row, "policy_score") >= spec["minimum_policy_score"] and external >= spec["minimum_market_or_industry_score"]


def condition_windows(history: list[dict[str, Any]], *, rules: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    active = rules or load_rules()
    specs = {
        "formation": (_formation, 1, active["formation"]["minimum_duration_days"]),
        "confirmation": (_confirmation, active["confirmation"]["minimum_consecutive_observations"], active["confirmation"]["minimum_duration_days"]),
        "expansion": (_expansion, active["expansion"]["minimum_consecutive_observations"], 0),
        "cooling": (_cooling, active["cooling"]["minimum_consecutive_observations"], 0),
        "declining": (_declining, active["declining"]["minimum_consecutive_observations"], active["declining"]["minimum_duration_days"]),
        "ending": (_ending, active["ending"]["minimum_consecutive_observations"], active["ending"]["minimum_declining_duration_days"]),
        "restarting": (_restarting, 1, active["restarting"]["minimum_duration_days"]),
    }
    return {
        name: evaluate_condition_window(history, lambda row, prior, fn=predicate: fn(row, prior, active), observations, days)
        for name, (predicate, observations, days) in specs.items()
    }


def _growth_30d(history: list[dict[str, Any]]) -> float:
    if not history:
        return 0.0
    latest = history[-1]
    latest_date = parse_date(latest.get("date"))
    candidates = [row for row in history if latest_date and parse_date(row.get("date")) and (latest_date - parse_date(row.get("date"))).days >= 30]
    baseline = candidates[-1] if candidates else history[0]
    return _score(latest, "era_mainline_score") - _score(baseline, "era_mainline_score")


def target_stage(snapshot: dict[str, Any], previous_stage: str = "dormant", *, windows: dict[str, Any] | None = None, history: list[dict[str, Any]] | None = None, rules: dict[str, Any] | None = None) -> tuple[str, list[str]]:
    active = rules or load_rules()
    rows = history or [snapshot]
    state = windows or condition_windows(rows, rules=active)
    if snapshot.get("source_lifecycle_state") == "legacy_tail":
        return "cooling", ["旧政策贡献仍可识别，但近期强化不足"]
    if previous_stage == "ended" and state["restarting"]["currently_met"]:
        return "restarting", ["结束后新的独立政策驱动力持续达到重新启动门槛"]
    if state["ending"]["currently_met"] and previous_stage in {"declining", "ended"}:
        return "ended", ["衰退后多维证据持续低于结束门槛且没有新的强化"]
    if state["declining"]["currently_met"]:
        return "declining", ["至少两个有效维度持续转弱，综合分低于衰退门槛"]
    if state["cooling"]["currently_met"] and previous_stage in {"launching", "confirmed", "expanding", "mature", "cooling"}:
        return "cooling", ["综合分或市场分相对阶段高点持续明显下降"]
    confirmed_start = parse_date(state["confirmation"].get("first_qualified_date"))
    current_date = parse_date(snapshot.get("date"))
    maturity = active["maturity"]
    if confirmed_start and current_date and (current_date - confirmed_start).days >= maturity["minimum_confirmed_duration_days"] and _score(snapshot, "era_mainline_score") >= maturity["minimum_era_score"] and _growth_30d(rows) <= maturity["maximum_score_growth_30d"]:
        return "mature", ["确认状态持续达到成熟周期，绝对证据仍强但30日增速趋缓"]
    if state["confirmation"]["currently_met"] and state["expansion"]["currently_met"]:
        return "expanding", ["确认条件持续成立，市场广度与官方战略叙事继续扩散"]
    if state["confirmation"]["currently_met"]:
        return "confirmed", ["政策、市场或产业及综合分持续达到确认门槛"]
    raw_confirmation = _confirmation(snapshot, rows, active)
    if raw_confirmation or (state["formation"]["currently_met"] and (_score(snapshot, "market_score") >= 35 or snapshot.get("industry_score") is not None)):
        return "launching", ["多层证据开始响应，但确认持续周期尚未满足"]
    if state["formation"]["currently_met"]:
        return "emerging", ["政策方向与独立事件持续达到形成条件，外部确认仍不足"]
    if _score(snapshot, "policy_score") > 0 or _score(snapshot, "market_score") > 0 or _event_count(snapshot) > 0:
        return "incubating", ["至少一维已有早期信号，但形成持续周期尚未满足"]
    return "dormant", ["尚无结构性证据或有效数据不足"]


def apply_transition(previous: str, desired: str, *, rules: dict[str, Any] | None = None) -> tuple[str, bool]:
    """Compatibility helper: evidence stages are no longer advanced one report at a time."""
    return desired, previous == desired or desired != "uncertain"


def enrich_lifecycle(history: list[dict[str, Any]], *, rules: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    active = rules or load_rules()
    rows = deduplicate_observations(history)
    labels = active["labels"]
    previous = "dormant"
    output: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    for index, snapshot in enumerate(rows):
        observed = rows[: index + 1]
        windows = condition_windows(observed, rules=active)
        stage, reasons = target_stage(snapshot, previous, windows=windows, history=observed, rules=active)
        if stage not in active["stages"]:
            stage, reasons = "uncertain", ["模型输出阶段不在配置允许的阶段枚举中"]
        row = {**snapshot, "evidence_stage": stage, "lifecycle_stage": stage, "lifecycle_stage_label": labels.get(stage, stage), "stage_reasons": reasons, "condition_windows": windows, "direct_transition": True}
        if stage != previous:
            transitions.append({"theme_id": snapshot.get("theme_id", ""), "from_stage": previous, "to_stage": stage, "change_date": snapshot.get("date", ""), "change_reasons": reasons, "dimension_changes": snapshot.get("dimension_changes", {}), "confidence": snapshot.get("current_state_confidence", snapshot.get("confidence", 0)), "legal_direct_transition": True})
        output.append(row)
        previous = stage
    return output, transitions


def lifecycle_dates(history: list[dict[str, Any]], events: list[dict[str, Any]], basis_date: str, *, rules: dict[str, Any] | None = None) -> dict[str, Any]:
    active = rules or load_rules()
    rows = deduplicate_observations(history)
    windows = condition_windows(rows, rules=active)
    coverage = history_coverage(rows)
    first = rows[0] if rows else {}
    first_raw_formation = _formation(first, rows[:1], active) if first else False
    first_raw_confirmation = _confirmation(first, rows[:1], active) if first else False
    coverage["is_left_censored"] = bool(first_raw_formation or first_raw_confirmation)
    event_dates = sorted(filter(None, (parse_date(event.get("event_date") or event.get("event_activity_date") or event.get("published_date")) for event in events)))
    earliest_observation = parse_date(coverage["first_available_observation_date"])
    earliest_candidates = ([event_dates[0]] if event_dates else []) + ([earliest_observation] if earliest_observation else [])
    earliest = min(earliest_candidates) if earliest_candidates else None
    formation = windows["formation"]
    confirmation = windows["confirmation"]
    estimated_start = "" if coverage["is_left_censored"] else formation.get("first_qualified_start_date", "")
    decided_at = "" if coverage["is_left_censored"] else formation.get("first_qualified_date", "")
    confirmation_date = confirmation.get("first_qualified_date", "")
    if not estimated_start or (confirmation_date and confirmation_date < estimated_start):
        confirmation_date = ""
    weakening = windows["cooling"].get("first_qualified_start_date", "")
    declining = windows["declining"].get("first_qualified_date", "")
    ended = windows["ending"].get("first_qualified_date", "")
    weakening_anchor = confirmation_date or estimated_start
    if weakening_anchor and weakening and weakening < weakening_anchor:
        weakening = ""
    declining_anchor = weakening or confirmation_date or estimated_start
    if declining_anchor and declining and declining < declining_anchor:
        declining = ""
    end_anchor = declining or weakening or confirmation_date or estimated_start
    if end_anchor and ended and ended < end_anchor:
        ended = ""
    reinforcement = ""
    reinforcement_anchor = estimated_start or coverage["first_available_observation_date"]
    stage_order = {name: index for index, name in enumerate(("dormant", "incubating", "emerging", "launching", "confirmed", "expanding", "mature"))}
    if reinforcement_anchor:
        for previous, current in zip(rows, rows[1:]):
            current_date = str(current.get("date") or "")
            stage_upgraded = stage_order.get(str(current.get("lifecycle_stage")), -1) > stage_order.get(str(previous.get("lifecycle_stage")), -1)
            improved = _score(current, "policy_score") > _score(previous, "policy_score") or _score(current, "era_mainline_score") - _score(previous, "era_mainline_score") >= 5 or _score(current, "market_score") > _score(previous, "market_score") or stage_upgraded
            if current_date >= reinforcement_anchor and improved:
                reinforcement = current_date
    basis = parse_date(basis_date)
    start = parse_date(estimated_start)
    return {
        "earliest_evidence_date": earliest.isoformat() if earliest else "",
        "signal_start_date": earliest.isoformat() if earliest else "",
        "formation_start_date": "" if coverage["is_left_censored"] else formation.get("first_met_date", ""),
        "estimated_start_date": estimated_start,
        "start_date_decided_at": decided_at,
        "model_decided_at": decided_at,
        "start_date_status": "before_available_history" if coverage["is_left_censored"] else ("estimated" if estimated_start else "insufficient_evidence"),
        "confirmation_date": confirmation_date,
        "latest_policy_event_date": event_dates[-1].isoformat() if event_dates else "",
        "latest_reinforcement_date": reinforcement,
        "weakening_start_date": weakening,
        "declining_start_date": declining,
        "estimated_end_date": ended,
        "duration_days": (basis - start).days if basis and start and basis >= start else 0,
        "condition_windows": windows,
        "history_coverage": coverage,
    }
