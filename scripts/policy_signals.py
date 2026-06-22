from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "data" / "policy_signals.json"

AUTHORITY_SCORES = {
    "state_council": 100,
    "multi_ministry": 95,
    "national_ministry": 90,
    "national_regulator": 88,
    "exchange": 75,
    "provincial": 70,
    "industry_association": 55,
}


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


def freshness_score(published: date | None, basis: date) -> float:
    if published is None:
        return 0.0
    age = (basis - published).days
    if age < 0:
        return 0.0
    if age <= 7:
        return 100.0
    if age <= 30:
        return 85.0
    if age <= 90:
        return 65.0
    if age <= 180:
        return 45.0
    if age <= 365:
        return 25.0
    return 0.0


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


def signal_base_score(signal: dict[str, Any], basis: date) -> float:
    authority = AUTHORITY_SCORES.get(str(signal.get("authority_level") or ""), 50)
    freshness = freshness_score(parse_date(signal.get("published_date")), basis)
    specificity = normalized_factor(signal.get("specificity"))
    implementation = normalized_factor(signal.get("implementation_path"))
    confidence = normalized_factor(signal.get("confidence", 0.7))
    return (
        0.30 * authority
        + 0.20 * freshness
        + 0.20 * specificity
        + 0.20 * implementation
        + 0.10 * confidence
    )


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
                    "score": score,
                    "base_score": base_score,
                    "relevance": relevance,
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
    return errors
