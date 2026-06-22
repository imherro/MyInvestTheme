from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

from policy_event_clustering import build_event_cluster_summary, build_policy_event_clusters
from policy_scoring import policy_score_components
from theme_relevance import (
    MIN_RELEVANCE_THRESHOLD,
    build_deduped_theme_summary,
    load_theme_config,
    theme_keywords,
)


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
    signals = scored_policy_signals(store.get("signals", []), basis)
    clusters = build_policy_event_clusters(signals, theme_keywords(themes))
    return build_deduped_theme_summary(signals, themes, clusters, basis, min_threshold=MIN_RELEVANCE_THRESHOLD)


def policy_stance_summary(basis_date: str, theme_names: list[str], path: Path = POLICY_PATH) -> dict[str, Any]:
    return policy_theme_summary(basis_date, theme_names, path).get("policy_stance_summary", {})


def policy_event_summary(basis_date: str, theme_names: list[str], path: Path = POLICY_PATH) -> dict[str, Any]:
    basis = parse_date(basis_date)
    if basis is None:
        raise ValueError(f"Invalid basis_date: {basis_date}")
    store = load_policy_store(path)
    allowed = set(theme_names)
    themes = [theme for theme in load_theme_config() if theme.get("theme_name") in allowed]
    signals = scored_policy_signals(store.get("signals", []), basis)
    clusters = build_policy_event_clusters(signals, theme_keywords(themes))
    return build_event_cluster_summary(signals, clusters)


def scored_policy_signals(signals: list[dict[str, Any]], basis: date) -> list[dict[str, Any]]:
    scored = []
    for signal in signals:
        item = dict(signal)
        item.update(policy_score_components(item, basis))
        scored.append(item)
    return scored


def score_policy_by_theme(basis_date: str, theme_names: list[str], path: Path = POLICY_PATH) -> dict[str, dict[str, Any]]:
    summary = policy_theme_summary(basis_date, theme_names, path)
    result: dict[str, dict[str, Any]] = {
        theme: {
            "score": 0.0,
            "evidence_count": 0,
            "top_policies": [],
            "theme_score_v4": 0.0,
            "theme_score_v3_dedup": 0.0,
            "theme_score_v3": 0.0,
            "theme_score_v2_raw": 0.0,
            "matched_event_cluster_count": 0,
            "matched_policy_count_raw": 0,
            "deduplication_effect": 0.0,
            "stance_adjustment_effect": 0.0,
            "supportive_cluster_count": 0,
            "mildly_supportive_cluster_count": 0,
            "neutral_or_mixed_cluster_count": 0,
            "mildly_restrictive_cluster_count": 0,
            "restrictive_cluster_count": 0,
            "avg_cluster_relevance_score_v2": 0.0,
            "avg_cluster_policy_score_v2": 0.0,
            "avg_cluster_stance_score_v2": 0.0,
        }
        for theme in theme_names
    }

    for theme_item in summary.get("themes", []):
        theme = theme_item.get("theme_name", "")
        if theme not in result:
            continue
        contributors = theme_item.get("top_policy_contributors") or []
        event_contributors = theme_item.get("top_event_contributors") or contributors
        result[theme].update(
            {
                "theme_id": theme_item.get("theme_id", ""),
                "theme_name": theme_item.get("theme_name", theme),
                "score": min(100.0, float(theme_item.get("theme_score_v4") or 0.0) * 100),
                "evidence_count": int(theme_item.get("matched_event_cluster_count") or 0),
                "theme_score_v4": theme_item.get("theme_score_v4", 0.0),
                "theme_score_v3_dedup": theme_item.get("theme_score_v3_dedup", theme_item.get("theme_score_v3", 0.0)),
                "theme_score_v3": theme_item.get("theme_score_v3", 0.0),
                "theme_score_v2_raw": theme_item.get("theme_score_v2_raw", 0.0),
                "matched_event_cluster_count": int(theme_item.get("matched_event_cluster_count") or 0),
                "matched_policy_count_raw": int(theme_item.get("matched_policy_count_raw") or 0),
                "deduplication_effect": theme_item.get("deduplication_effect", 0.0),
                "stance_adjustment_effect": theme_item.get("stance_adjustment_effect", 0.0),
                "supportive_cluster_count": int(theme_item.get("supportive_cluster_count") or 0),
                "mildly_supportive_cluster_count": int(theme_item.get("mildly_supportive_cluster_count") or 0),
                "neutral_or_mixed_cluster_count": int(theme_item.get("neutral_or_mixed_cluster_count") or 0),
                "mildly_restrictive_cluster_count": int(theme_item.get("mildly_restrictive_cluster_count") or 0),
                "restrictive_cluster_count": int(theme_item.get("restrictive_cluster_count") or 0),
                "avg_cluster_relevance_score_v2": theme_item.get("avg_cluster_relevance_score_v2", 0.0),
                "avg_cluster_policy_score_v2": theme_item.get("avg_cluster_policy_score_v2", 0.0),
                "avg_cluster_stance_score_v2": theme_item.get("avg_cluster_stance_score_v2", 0.0),
                "top_policies": [
                    {
                        "id": row.get("primary_policy_id", row.get("policy_id", "")),
                        "event_cluster_id": row.get("event_cluster_id", ""),
                        "title": row.get("primary_policy_title", row.get("title", "")),
                        "source": row.get("source", ""),
                        "published_date": row.get("published_date", ""),
                        "url": row.get("url", ""),
                        "score": float(row.get("stance_adjusted_cluster_contribution", row.get("contribution", 0.0)) or 0.0) * 100,
                        "base_score": float(row.get("cluster_policy_score_v2", row.get("policy_score_v2", 0.0)) or 0.0) * 100,
                        "relevance_score_v2": row.get("cluster_relevance_score_v2", row.get("relevance_score_v2", 0.0)),
                        "contribution": row.get("stance_adjusted_cluster_contribution", row.get("contribution", 0.0)),
                        "pre_stance_cluster_contribution": row.get("pre_stance_cluster_contribution", 0.0),
                        "stance_adjusted_cluster_contribution": row.get("stance_adjusted_cluster_contribution", 0.0),
                        "stance_adjustment_effect": row.get("stance_adjustment_effect", 0.0),
                        "keyword_score": row.get("keyword_score", 0.0),
                        "beneficiary_score": row.get("beneficiary_score", 0.0),
                        "policy_objective_score": row.get("policy_objective_score", 0.0),
                        "negative_filter_score": row.get("negative_filter_score", 1.0),
                        "matched_evidence": row.get("top_matched_evidence", row.get("matched_evidence", [])),
                        "policy_score_v2": row.get("cluster_policy_score_v2", row.get("policy_score_v2", 0.0)),
                        "cluster_policy_score_v2": row.get("cluster_policy_score_v2", 0.0),
                        "cluster_relevance_score_v2": row.get("cluster_relevance_score_v2", 0.0),
                        "cluster_contribution": row.get("cluster_contribution", 0.0),
                        "cluster_support_score": row.get("cluster_support_score", 0.0),
                        "cluster_constraint_score": row.get("cluster_constraint_score", 0.0),
                        "cluster_stance_score_v2": row.get("cluster_stance_score_v2", 0.0),
                        "cluster_stance_label": row.get("cluster_stance_label", ""),
                        "direction_multiplier": row.get("direction_multiplier", 0.0),
                        "selected_stance_policy_id": row.get("selected_stance_policy_id", ""),
                        "top_stance_evidence": row.get("top_stance_evidence", []),
                        "cluster_size": row.get("cluster_size", 1),
                        "member_policy_ids": row.get("member_policy_ids", []),
                        "cluster_reason": row.get("cluster_reason", []),
                        "metrics": row.get("metrics", {}),
                        "authority_score": row.get("authority_score", 0.0),
                        "actionability_score": row.get("actionability_score", 0.0),
                        "economic_scope_score": row.get("economic_scope_score", 0.0),
                        "time_decay_score": row.get("time_decay_score", 0.0),
                    }
                    for row in event_contributors
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
