from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "research" / "era_mainline"
VERSION = "era_mainline_validator_v2"


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
    if ranks and ranks != list(range(1, len(ranks) + 1)):
        error("ERA_RANK_SEQUENCE_INVALID", "theme_states", "Era ranks must be contiguous and ordered.")
    transitions = payload.get("transitions") or []
    if transitions != sorted(transitions, key=lambda item: (item.get("change_date", ""), item.get("theme_id", ""))):
        error("ERA_TRANSITIONS_OUT_OF_ORDER", "transitions", "Lifecycle transitions must be chronological.")
    required_usage = {"formation.minimum_duration_days", "confirmation.minimum_consecutive_observations", "confirmation.minimum_duration_days", "cooling.minimum_market_score_drop", "dominance_gap", "dual_mainline_gap", "all_lifecycle_rule_fields"}
    usage = payload.get("rule_usage") or {}
    for key in sorted(required_usage):
        if usage.get(key) is not True:
            error("ERA_RULE_UNUSED", f"rule_usage.{key}", "Core configuration must be used by the model.")
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
