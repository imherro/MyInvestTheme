from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "research" / "era_mainline"
LIFECYCLE_RULES_PATH = ROOT / "config" / "era_lifecycle_rules.json"
VERSION = "era_mainline_validator_v4"


def _date(value: Any) -> datetime | None:
    try:
        return datetime.strptime(str(value or "")[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _required_rule_paths(rules: dict[str, Any]) -> set[str]:
    result = {"stages", "labels", "transition_policy.allowed_direct_transitions", "minimum_stage_dwell_days"}
    sections = ("formation", "confirmation", "expansion", "maturity", "cooling", "declining", "ending", "restarting", "hysteresis", "reinforcement", "left_censoring", "stage_confidence")
    def walk(value: Any, prefix: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, f"{prefix}.{key}" if prefix else key)
        else:
            result.add(prefix)
    for section in sections:
        walk(rules.get(section, {}), section)
    return result


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    def error(code: str, path: str, message: str) -> None:
        errors.append({"code": code, "path": path, "message": message})

    required = {"report_id", "basis_date", "mainline_regime", "primary_mainline", "secondary_mainline", "emerging_candidates", "declining_mainlines", "theme_states", "data_coverage", "history_semantics", "rule_usage", "summary"}
    for field in sorted(required - set(payload)):
        error("ERA_REQUIRED_FIELD_MISSING", field, "Required era-mainline field is missing.")
    ranks = []
    for index, theme in enumerate(payload.get("theme_states") or []):
        path = f"theme_states.{index}"
        for field in ("theme_id", "theme_name", "era_mainline_score", "era_rank", "era_mainline_status", "mainline_qualification", "lifecycle_stage", "evidence_stage", "current_state_confidence", "lifecycle_stage_confidence", "lifecycle_date_confidence", "history_coverage", "condition_windows", "effective_dimension_weights", "supporting_evidence", "contradicting_evidence", "invalidating_conditions", "score_history", "stage_history"):
            if field not in theme:
                error("ERA_THEME_FIELD_MISSING", f"{path}.{field}", "Required theme-state field is missing.")
        ranks.append(theme.get("era_rank"))
        date_fields = ("earliest_evidence_date", "formation_start_date", "estimated_start_date", "confirmation_date", "weakening_start_date", "declining_start_date", "estimated_end_date")
        dated = [(field, str(theme.get(field) or "")) for field in date_fields if theme.get(field)]
        for (left_name, left), (right_name, right) in zip(dated, dated[1:]):
            if right < left:
                error("ERA_DATE_ORDER_INVALID", f"{path}.{right_name}", f"{right_name} cannot precede {left_name}.")
        start = str(theme.get("estimated_start_date") or "")
        reinforcement = str(theme.get("latest_reinforcement_date") or "")
        if start and reinforcement and reinforcement < start:
            error("REINFORCEMENT_BEFORE_START", f"{path}.latest_reinforcement_date", "Reinforcement cannot precede estimated start.")
        qualification = theme.get("mainline_qualification")
        stage = theme.get("evidence_stage")
        ordered = ["dormant", "incubating", "emerging", "launching", "confirmed", "expanding", "mature"]
        if qualification in {"primary_era_mainline", "secondary_era_mainline"} and (stage not in ordered or ordered.index(stage) < ordered.index("confirmed")):
            error("QUALIFICATION_STAGE_CONFLICT", path, "Confirmed era-mainline qualification requires confirmed-or-higher evidence stage.")
        if qualification == "legacy_mainline" and stage not in {"cooling", "declining", "ended"}:
            error("LEGACY_STAGE_CONFLICT", path, "Legacy mainline must be cooling, declining, or ended.")
        if stage == "ended" and not theme.get("estimated_end_date"):
            error("ENDED_WITHOUT_DATE", f"{path}.estimated_end_date", "Ended evidence stage requires an end date.")
        if qualification == "emerging_candidate" and theme.get("is_confirmed_era_mainline"):
            error("CANDIDATE_CONFIRMATION_TEXT_CONFLICT", path, "Emerging candidate cannot be marked as a confirmed era mainline.")
        history = theme.get("score_history") or []
        if history != sorted(history, key=lambda item: (item.get("date", ""), item.get("report_id", ""))):
            error("ERA_HISTORY_OUT_OF_ORDER", f"{path}.score_history", "Score history must be chronological.")
        if theme.get("industry_score") is None:
            warnings.append({"code": "INDUSTRY_VALIDATION_UNKNOWN", "path": f"{path}.industry_score", "message": "Industry validation is unavailable and must not be interpreted as zero."})
        sequences = [int(item.get("cycle_sequence") or 0) for item in history]
        if any(right < left for left, right in zip(sequences, sequences[1:])):
            error("ERA_CYCLE_ID_INVALID", f"{path}.score_history", "Cycle sequence cannot move backward.")
        cycle_id = str(theme.get("cycle_id") or "")
        current_cycle = [item for item in history if item.get("cycle_id") == cycle_id]
        if current_cycle and float(theme.get("cycle_peak_score") or 0) < max(float(item.get("era_mainline_score") or 0) for item in current_cycle):
            error("ERA_CYCLE_PEAK_INVALID", f"{path}.cycle_peak_score", "Cycle peak must cover every score in the current cycle.")
        cycle_start = str(theme.get("cycle_start_observation_date") or "")
        peak_date = str(theme.get("cycle_peak_date") or "")
        if cycle_start and peak_date and peak_date < cycle_start:
            error("ERA_CYCLE_PEAK_INVALID", f"{path}.cycle_peak_date", "Cycle peak date cannot precede cycle start.")
        if theme.get("history_coverage", {}).get("is_left_censored") and (not theme.get("left_censoring_reasons") or not theme.get("left_censoring_evidence")):
            error("ERA_LEFT_CENSORING_UNSUPPORTED", f"{path}.history_coverage", "Left censoring requires explicit reasons and evidence.")
        reinforcement_type = str(theme.get("latest_reinforcement_type") or "none")
        reinforcement_date = str(theme.get("latest_reinforcement_date") or "")
        source_dates = {
            "policy_event": theme.get("latest_policy_reinforcement_event_date"),
            "market_confirmation": theme.get("latest_market_reinforcement_date"),
            "composite_score": theme.get("latest_composite_reinforcement_date"),
        }
        if reinforcement_type in source_dates and reinforcement_date != str(source_dates[reinforcement_type] or ""):
            error("ERA_REINFORCEMENT_DATE_INVALID", f"{path}.latest_reinforcement_date", "Selected reinforcement date must match its source type.")
        if reinforcement_type == "none" and reinforcement_date:
            error("ERA_REINFORCEMENT_TYPE_MISMATCH", f"{path}.latest_reinforcement_type", "None reinforcement type cannot have a date.")
        if reinforcement_type == "policy_event":
            event_dates = {str(item.get("event_date") or "") for item in theme.get("policy_dimension", {}).get("events") or []}
            if reinforcement_date not in event_dates:
                error("ERA_POLICY_REINFORCEMENT_EVENT_MISSING", f"{path}.latest_policy_reinforcement_event_date", "Policy reinforcement must match an actual event date.")
        end_date, declining_date = _date(theme.get("estimated_end_date")), _date(theme.get("declining_start_date"))
        if end_date:
            minimum_days = json.loads(LIFECYCLE_RULES_PATH.read_text(encoding="utf-8"))["ending"]["minimum_days_after_declining_start"]
            if not declining_date or (end_date - declining_date).days < minimum_days:
                error("ERA_END_BEFORE_DECLINING_DURATION", f"{path}.estimated_end_date", "End date must be anchored to the minimum formal declining duration.")
    if ranks and ranks != list(range(1, len(ranks) + 1)):
        error("ERA_RANK_SEQUENCE_INVALID", "theme_states", "Era ranks must be contiguous and ordered.")
    transitions = payload.get("transitions") or []
    if transitions != sorted(transitions, key=lambda item: (item.get("change_date", ""), item.get("theme_id", ""))):
        error("ERA_TRANSITIONS_OUT_OF_ORDER", "transitions", "Lifecycle transitions must be chronological.")
    forbidden = {("dormant", "cooling"), ("dormant", "declining"), ("dormant", "ended"), ("ended", "confirmed"), ("ended", "expanding"), ("ended", "mature"), ("declining", "launching")}
    for index, item in enumerate(transitions):
        pair = (item.get("from_stage"), item.get("to_stage"))
        if item.get("transition_allowed") and pair in forbidden:
            error("ERA_ILLEGAL_STAGE_TRANSITION", f"transitions.{index}", "Forbidden stage transition was accepted.")
        if item.get("transition_allowed") and not item.get("stage_dwell_satisfied") and not item.get("dwell_override"):
            error("ERA_STAGE_DWELL_VIOLATION", f"transitions.{index}", "Early transition requires an explicit severe-evidence override.")
        if pair == ("ended", "restarting") and (not item.get("cycle_id") or int(item.get("cycle_sequence") or 0) <= 0):
            error("ERA_RESTART_WITHOUT_NEW_CYCLE", f"transitions.{index}.cycle_id", "Restart must open a new lifecycle cycle.")
    if str(payload.get("scoring_version") or "").endswith("_v3") or "research_objects" in payload:
        phase3_required = {"research_objects", "class_rankings", "mainline_relationships", "research_hypothesis_comparison", "era_industrial_ranking", "strategic_growth_ranking", "policy_profit_repair_ranking", "macro_cycle_ranking", "trading_branch_ranking", "allocation_style_ranking"}
        for field in sorted(phase3_required - set(payload)):
            error("ERA_REQUIRED_FIELD_MISSING", field, "Required Phase 3 field is missing.")
        valid_classes = {"era_industrial", "strategic_growth", "policy_profit_repair", "macro_cycle", "trading_branch", "allocation_style", "unclassified"}
        objects = payload.get("research_objects") or []
        object_ids = set()
        for index, item in enumerate(objects):
            path = f"research_objects.{index}"
            object_id = str(item.get("theme_id") or "")
            if not object_id or object_id in object_ids:
                error("MAINLINE_CLASS_INVALID", f"{path}.theme_id", "Research object IDs must be present and unique.")
            object_ids.add(object_id)
            kind = item.get("primary_mainline_class")
            if kind not in valid_classes:
                error("MAINLINE_CLASS_INVALID", f"{path}.primary_mainline_class", "Every research object needs one valid primary class.")
            structural = item.get("structural_lifecycle")
            market = item.get("market_expression_lifecycle")
            if not isinstance(structural, dict) or not isinstance(market, dict) or structural is market or structural == market:
                error("STRUCTURAL_MARKET_STAGE_COUPLED", path, "Structural and market-expression lifecycles must be independent objects.")
            if item.get("structural_conviction_score") == item.get("market_expression_score"):
                error("STRUCTURAL_MARKET_STAGE_COUPLED", f"{path}.structural_conviction_score", "Structural conviction cannot directly copy market expression.")
            if kind in {"era_industrial", "strategic_growth"} and item.get("industry_score") is None and float(item.get("structural_stage_confidence") or 0) > 55:
                error("STRUCTURAL_END_EVIDENCE_INSUFFICIENT", f"{path}.structural_stage_confidence", "Missing industrial data must cap structural-stage confidence.")
            if kind == "era_industrial" and item.get("structural_stage") == "structural_ended" and item.get("market_expression_stage") in {"cooling", "declining", "rejected"}:
                error("STRUCTURAL_END_EVIDENCE_INSUFFICIENT", f"{path}.structural_stage", "Market weakness alone cannot end an era-industrial structure.")
            horizon = item.get("time_horizon") or {}
            if kind == "era_industrial" and horizon.get("unit") != "years":
                error("CLASS_DURATION_MISMATCH", f"{path}.time_horizon", "Era-industrial horizons must use years.")
            if kind == "trading_branch" and horizon.get("unit") not in {"days", "months"}:
                error("CLASS_DURATION_MISMATCH", f"{path}.time_horizon", "Trading branches must use short horizons.")
        industrial_ids = {item.get("theme_id") for key in ("era_industrial_ranking", "strategic_growth_ranking", "policy_profit_repair_ranking", "macro_cycle_ranking") for item in payload.get(key) or []}
        style_ids = {item.get("theme_id") for item in payload.get("allocation_style_ranking") or []}
        trading_ids = {item.get("theme_id") for item in payload.get("trading_branch_ranking") or []}
        if industrial_ids & style_ids:
            error("ALLOCATION_STYLE_IN_MAINLINE_RANKING", "class_rankings", "Allocation styles cannot appear in industrial rankings.")
        if industrial_ids & trading_ids:
            error("TRADING_BRANCH_PROMOTED_TO_ERA_MAINLINE", "class_rankings", "Trading branches cannot appear in industrial rankings.")
        for key, expected_class in (("era_industrial_ranking", "era_industrial"), ("strategic_growth_ranking", "strategic_growth"), ("policy_profit_repair_ranking", "policy_profit_repair"), ("macro_cycle_ranking", "macro_cycle"), ("trading_branch_ranking", "trading_branch"), ("allocation_style_ranking", "allocation_style")):
            if any(item.get("primary_mainline_class") != expected_class for item in payload.get(key) or []):
                error("MAINLINE_CLASS_RANKING_CONFLICT", key, "Every class ranking must contain only its declared class.")
    rules = json.loads(LIFECYCLE_RULES_PATH.read_text(encoding="utf-8"))
    usage_versions = payload.get("rule_usage") or {}
    usage = usage_versions.get(rules["version"])
    if not isinstance(usage, dict) or "all_lifecycle_rule_fields" in usage:
        error("ERA_RULE_USAGE_UNVERIFIED", "rule_usage", "Rule usage must be runtime access records, not a static aggregate boolean.")
    else:
        for key in sorted(_required_rule_paths(rules)):
            record = usage.get(key) or {}
            if record.get("used") is not True or int(record.get("access_count") or 0) <= 0:
                warnings.append({"code": "ERA_CONFIG_FIELD_UNUSED", "path": f"rule_usage.{rules['version']}.{key}", "message": "This conditional lifecycle rule was not exercised by the current report; synthetic tests must cover it."})
    return {
        "scoring_version": VERSION,
        "status": "fail" if errors else ("warning" if warnings else "pass"),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
    }


def latest_path() -> Path:
    files = sorted(REPORT_DIR.glob("era_mainline_review_*.json"), key=lambda path: path.stat().st_mtime)
    if not files:
        raise FileNotFoundError("No era-mainline report found")
    return files[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an era-mainline report.")
    parser.add_argument("--path", type=Path)
    parser.add_argument("--latest", action="store_true")
    args = parser.parse_args()
    path = args.path or latest_path()
    summary = validate(json.loads(path.read_text(encoding="utf-8")))
    print(json.dumps({"path": str(path), **summary}, ensure_ascii=False, indent=2))
    return 1 if summary["error_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
