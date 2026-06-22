from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

from theme_relevance import MIN_RELEVANCE_THRESHOLD, build_theme_summary, load_theme_config


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "data" / "policy_signals.json"


def load_policy_store(path: Path = POLICY_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"updated_at": "", "signals": []}
    return json.loads(path.read_text(encoding="utf-8"))


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def is_unit_number(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return not (math.isnan(number) or math.isinf(number)) and 0.0 <= number <= 1.0


def policy_theme_summary(basis_date: str, theme_names: list[str], path: Path = POLICY_PATH) -> dict[str, Any]:
    basis = parse_date(basis_date)
    if basis is None:
        raise ValueError(f"Invalid basis_date: {basis_date}")

    store = load_policy_store(path)
    allowed = set(theme_names)
    themes = [theme for theme in load_theme_config() if theme.get("theme_name") in allowed]
    return build_theme_summary(store.get("signals", []), themes, basis, min_threshold=MIN_RELEVANCE_THRESHOLD)


def score_policy_by_theme(basis_date: str, theme_names: list[str], path: Path = POLICY_PATH) -> dict[str, dict[str, Any]]:
    summary = policy_theme_summary(basis_date, theme_names, path)
    result: dict[str, dict[str, Any]] = {
        theme: {
            "score": 0.0,
            "evidence_count": 0,
            "top_policies": [],
            "theme_score_v2": 0.0,
            "matched_policy_count": 0,
            "avg_relevance_score_v2": 0.0,
            "avg_policy_score_v2": 0.0,
        }
        for theme in theme_names
    }

    for theme_item in summary.get("themes", []):
        theme = theme_item.get("theme_name", "")
        if theme not in result:
            continue
        contributors = theme_item.get("top_policy_contributors") or []
        result[theme].update(
            {
                "score": min(100.0, float(theme_item.get("theme_score_v2") or 0.0) * 100),
                "evidence_count": int(theme_item.get("matched_policy_count") or 0),
                "theme_score_v2": theme_item.get("theme_score_v2", 0.0),
                "matched_policy_count": int(theme_item.get("matched_policy_count") or 0),
                "avg_relevance_score_v2": theme_item.get("avg_relevance_score_v2", 0.0),
                "avg_policy_score_v2": theme_item.get("avg_policy_score_v2", 0.0),
                "top_policies": [
                    {
                        "id": row.get("policy_id", ""),
                        "title": row.get("title", ""),
                        "source": row.get("source", ""),
                        "published_date": row.get("published_date", ""),
                        "url": row.get("url", ""),
                        "score": float(row.get("contribution") or 0.0) * 100,
                        "base_score": float(row.get("policy_score_v2") or 0.0) * 100,
                        "relevance_score_v2": row.get("relevance_score_v2", 0.0),
                        "contribution": row.get("contribution", 0.0),
                        "keyword_score": row.get("keyword_score", 0.0),
                        "beneficiary_score": row.get("beneficiary_score", 0.0),
                        "policy_objective_score": row.get("policy_objective_score", 0.0),
                        "negative_filter_score": row.get("negative_filter_score", 1.0),
                        "matched_evidence": row.get("matched_evidence", []),
                        "policy_score_v2": row.get("policy_score_v2", 0.0),
                        "authority_score": row.get("authority_score", 0.0),
                        "actionability_score": row.get("actionability_score", 0.0),
                        "economic_scope_score": row.get("economic_scope_score", 0.0),
                        "time_decay_score": row.get("time_decay_score", 0.0),
                    }
                    for row in contributors
                ],
            }
        )

    return result


def validate_policy_store(path: Path = POLICY_PATH) -> list[str]:
    store = load_policy_store(path)
    errors: list[str] = []
    seen: set[str] = set()
    for index, signal in enumerate(store.get("signals", []), start=1):
        signal_id = signal.get("id")
        if not signal_id:
            errors.append(f"signal {index}: missing id")
        elif signal_id in seen:
            errors.append(f"signal {index}: duplicate id {signal_id}")
        else:
            seen.add(signal_id)
        for field in ("title", "source", "published_date", "authority_level", "url"):
            if not signal.get(field):
                errors.append(f"signal {signal_id or index}: missing {field}")
        if parse_date(signal.get("published_date")) is None:
            errors.append(f"signal {signal_id or index}: invalid published_date")
        for deprecated in ("specificity", "implementation_path", "confidence"):
            if deprecated in signal:
                errors.append(f"signal {signal_id or index}: deprecated field {deprecated} must not be used")
        if "themes" in signal:
            errors.append(f"signal {signal_id or index}: deprecated field themes must not be used")
        for field in ("authority_score", "actionability_score", "economic_scope_score", "time_decay_score", "policy_score_v2"):
            if field not in signal:
                errors.append(f"signal {signal_id or index}: missing {field}")
            elif not is_unit_number(signal.get(field)):
                errors.append(f"signal {signal_id or index}: {field} must be 0-1")
    return errors
