from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

from policy_scoring import compute_policy_score_v2, policy_score_components


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


def clamp_score(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return max(0.0, min(100.0, number))


def normalized_factor(value: Any) -> float:
    number = clamp_score(value, default=0.0)
    return number * 100 if number <= 1 else number


def is_unit_number(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return not (math.isnan(number) or math.isinf(number)) and 0.0 <= number <= 1.0


def signal_base_score(signal: dict[str, Any], basis: date) -> float:
    return compute_policy_score_v2(signal, basis) * 100


def score_policy_by_theme(basis_date: str, theme_names: list[str], path: Path = POLICY_PATH) -> dict[str, dict[str, Any]]:
    basis = parse_date(basis_date)
    if basis is None:
        raise ValueError(f"Invalid basis_date: {basis_date}")

    store = load_policy_store(path)
    result: dict[str, dict[str, Any]] = {
        theme: {"score": 0.0, "evidence_count": 0, "top_policies": []}
        for theme in theme_names
    }

    for signal in store.get("signals", []):
        base_score = signal_base_score(signal, basis)
        components = policy_score_components(signal, basis)
        for mapping in signal.get("themes", []):
            theme = mapping.get("theme")
            if theme not in result:
                continue
            relevance = normalized_factor(mapping.get("relevance", 0.0)) / 100
            if relevance <= 0:
                continue
            score = base_score * relevance
            result[theme]["top_policies"].append(
                {
                    "id": signal.get("id", ""),
                    "title": signal.get("title", ""),
                    "source": signal.get("source", ""),
                    "published_date": signal.get("published_date", ""),
                    "url": signal.get("url", ""),
                    "authority_level": signal.get("authority_level", ""),
                    "economic_scope": signal.get("economic_scope", ""),
                    "score": score,
                    "base_score": base_score,
                    "relevance": relevance,
                    **components,
                    "evidence": signal.get("evidence", ""),
                    "beneficiary_chain": mapping.get("beneficiary_chain", []),
                }
            )

    for theme, item in result.items():
        policies = sorted(item["top_policies"], key=lambda row: row["score"], reverse=True)
        item["top_policies"] = policies[:3]
        item["evidence_count"] = sum(1 for row in policies if row["score"] >= 50)
        if not policies:
            item["score"] = 0.0
            continue
        weights = [1.0, 0.6, 0.3]
        weighted = sum(row["score"] * weights[index] for index, row in enumerate(policies[:3]))
        divisor = sum(weights[: len(policies[:3])])
        item["score"] = weighted / divisor
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
        for field in ("title", "source", "published_date", "authority_level", "url", "themes"):
            if not signal.get(field):
                errors.append(f"signal {signal_id or index}: missing {field}")
        if parse_date(signal.get("published_date")) is None:
            errors.append(f"signal {signal_id or index}: invalid published_date")
        if not isinstance(signal.get("themes"), list):
            errors.append(f"signal {signal_id or index}: themes must be a list")
        for deprecated in ("specificity", "implementation_path", "confidence"):
            if deprecated in signal:
                errors.append(f"signal {signal_id or index}: deprecated field {deprecated} must not be used")
        for field in ("authority_score", "actionability_score", "economic_scope_score", "time_decay_score", "policy_score_v2"):
            if field not in signal:
                errors.append(f"signal {signal_id or index}: missing {field}")
            elif not is_unit_number(signal.get(field)):
                errors.append(f"signal {signal_id or index}: {field} must be 0-1")
    return errors
