from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "research" / "era_mainline"
VERSION = "era_mainline_validator_v1"


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    def error(code: str, path: str, message: str) -> None:
        errors.append({"code": code, "path": path, "message": message})

    required = {"report_id", "basis_date", "mainline_regime", "primary_mainline", "secondary_mainline", "emerging_candidates", "declining_mainlines", "theme_states", "data_coverage", "summary"}
    for field in sorted(required - set(payload)):
        error("ERA_REQUIRED_FIELD_MISSING", field, "Required era-mainline field is missing.")
    ranks = []
    for index, theme in enumerate(payload.get("theme_states") or []):
        path = f"theme_states.{index}"
        for field in ("theme_id", "theme_name", "era_mainline_score", "era_rank", "era_mainline_status", "lifecycle_stage", "lifecycle_confidence", "supporting_evidence", "contradicting_evidence", "invalidating_conditions", "score_history", "stage_history"):
            if field not in theme:
                error("ERA_THEME_FIELD_MISSING", f"{path}.{field}", "Required theme-state field is missing.")
        ranks.append(theme.get("era_rank"))
        start = str(theme.get("estimated_start_date") or "")
        confirmation = str(theme.get("confirmation_date") or "")
        if start and confirmation and confirmation < start:
            error("CONFIRMATION_BEFORE_START", f"{path}.confirmation_date", "Confirmation date cannot precede estimated start date.")
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
