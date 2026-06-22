from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

from policy_event_clustering import build_event_cluster_summary, build_policy_event_clusters
from policy_provenance import filter_policies_by_provenance
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


def included_policy_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    included, _ = filter_policies_by_provenance(signals)
    return included


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
    signals = scored_policy_signals(included_policy_signals(store.get("signals", [])), basis)
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
    signals = scored_policy_signals(included_policy_signals(store.get("signals", [])), basis)
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
            "mainline_score_v6": 0.0,
            "theme_score_v5": 0.0,
            "theme_score_v4_stance_adjusted": 0.0,
            "theme_score_v4": 0.0,
            "theme_score_v3_dedup": 0.0,
            "theme_score_v3": 0.0,
            "theme_score_v2_raw": 0.0,
            "allocation_adjustment_effect": 0.0,
            "matched_event_cluster_count": 0,
            "matched_allocated_event_count": 0,
            "matched_policy_count_raw": 0,
            "deduplication_effect": 0.0,
            "stance_adjustment_effect": 0.0,
            "primary_event_count": 0,
            "co_primary_event_count": 0,
            "secondary_event_count": 0,
            "peripheral_event_count": 0,
            "supportive_cluster_count": 0,
            "mildly_supportive_cluster_count": 0,
            "neutral_or_mixed_cluster_count": 0,
            "mildly_restrictive_cluster_count": 0,
            "restrictive_cluster_count": 0,
            "avg_allocation_share": 0.0,
            "avg_cluster_relevance_score_v2": 0.0,
            "avg_cluster_policy_score_v2": 0.0,
            "avg_cluster_stance_score_v2": 0.0,
            "lifecycle_state": "",
            "state_multiplier": 0.0,
            "breadth_score": 0.0,
            "lifecycle_quality_multiplier": 0.0,
            "score_7d": 0.0,
            "score_30d": 0.0,
            "score_31_60d": 0.0,
            "score_61_90d": 0.0,
            "score_90d": 0.0,
            "older_score": 0.0,
            "undated_score": 0.0,
            "event_count_30d": 0,
            "event_count_90d": 0,
            "source_org_count_90d": 0,
            "active_window_count": 0,
            "persistence_score": 0.0,
            "acceleration_delta_30d": 0.0,
            "acceleration_ratio_30d": 0.0,
            "lifecycle_reasons": [],
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
                "score": min(100.0, float(theme_item.get("mainline_score_v6") or 0.0) * 100),
                "evidence_count": int(theme_item.get("matched_allocated_event_count") or 0),
                "mainline_score_v6": theme_item.get("mainline_score_v6", 0.0),
                "theme_score_v5": theme_item.get("theme_score_v5", 0.0),
                "theme_score_v4_stance_adjusted": theme_item.get(
                    "theme_score_v4_stance_adjusted", theme_item.get("theme_score_v4", 0.0)
                ),
                "theme_score_v4": theme_item.get("theme_score_v4", 0.0),
                "theme_score_v3_dedup": theme_item.get("theme_score_v3_dedup", theme_item.get("theme_score_v3", 0.0)),
                "theme_score_v3": theme_item.get("theme_score_v3", 0.0),
                "theme_score_v2_raw": theme_item.get("theme_score_v2_raw", 0.0),
                "allocation_adjustment_effect": theme_item.get("allocation_adjustment_effect", 0.0),
                "matched_event_cluster_count": int(theme_item.get("matched_event_cluster_count") or 0),
                "matched_allocated_event_count": int(theme_item.get("matched_allocated_event_count") or 0),
                "matched_policy_count_raw": int(theme_item.get("matched_policy_count_raw") or 0),
                "deduplication_effect": theme_item.get("deduplication_effect", 0.0),
                "stance_adjustment_effect": theme_item.get("stance_adjustment_effect", 0.0),
                "primary_event_count": int(theme_item.get("primary_event_count") or 0),
                "co_primary_event_count": int(theme_item.get("co_primary_event_count") or 0),
                "secondary_event_count": int(theme_item.get("secondary_event_count") or 0),
                "peripheral_event_count": int(theme_item.get("peripheral_event_count") or 0),
                "supportive_cluster_count": int(theme_item.get("supportive_cluster_count") or 0),
                "mildly_supportive_cluster_count": int(theme_item.get("mildly_supportive_cluster_count") or 0),
                "neutral_or_mixed_cluster_count": int(theme_item.get("neutral_or_mixed_cluster_count") or 0),
                "mildly_restrictive_cluster_count": int(theme_item.get("mildly_restrictive_cluster_count") or 0),
                "restrictive_cluster_count": int(theme_item.get("restrictive_cluster_count") or 0),
                "avg_allocation_share": theme_item.get("avg_allocation_share", 0.0),
                "avg_cluster_relevance_score_v2": theme_item.get("avg_cluster_relevance_score_v2", 0.0),
                "avg_cluster_policy_score_v2": theme_item.get("avg_cluster_policy_score_v2", 0.0),
                "avg_cluster_stance_score_v2": theme_item.get("avg_cluster_stance_score_v2", 0.0),
                "lifecycle_state": theme_item.get("lifecycle_state", ""),
                "state_multiplier": theme_item.get("state_multiplier", 0.0),
                "breadth_score": theme_item.get("breadth_score", 0.0),
                "lifecycle_quality_multiplier": theme_item.get("lifecycle_quality_multiplier", 0.0),
                "score_7d": theme_item.get("score_7d", 0.0),
                "score_30d": theme_item.get("score_30d", 0.0),
                "score_31_60d": theme_item.get("score_31_60d", 0.0),
                "score_61_90d": theme_item.get("score_61_90d", 0.0),
                "score_90d": theme_item.get("score_90d", 0.0),
                "older_score": theme_item.get("older_score", 0.0),
                "undated_score": theme_item.get("undated_score", 0.0),
                "event_count_30d": int(theme_item.get("event_count_30d") or 0),
                "event_count_90d": int(theme_item.get("event_count_90d") or 0),
                "source_org_count_90d": int(theme_item.get("source_org_count_90d") or 0),
                "active_window_count": int(theme_item.get("active_window_count") or 0),
                "persistence_score": theme_item.get("persistence_score", 0.0),
                "acceleration_delta_30d": theme_item.get("acceleration_delta_30d", 0.0),
                "acceleration_ratio_30d": theme_item.get("acceleration_ratio_30d", 0.0),
                "lifecycle_reasons": theme_item.get("lifecycle_reasons", []),
                "top_policies": [
                    {
                        "id": row.get("primary_policy_id", row.get("policy_id", "")),
                        "event_cluster_id": row.get("event_cluster_id", ""),
                        "title": row.get("primary_policy_title", row.get("title", "")),
                        "source": row.get("source", ""),
                        "published_date": row.get("published_date", ""),
                        "url": row.get("url", ""),
                        "score": float(row.get("allocated_cluster_contribution", row.get("contribution", 0.0)) or 0.0) * 100,
                        "base_score": float(row.get("cluster_policy_score_v2", row.get("policy_score_v2", 0.0)) or 0.0) * 100,
                        "relevance_score_v2": row.get("cluster_relevance_score_v2", row.get("relevance_score_v2", 0.0)),
                        "contribution": row.get("allocated_cluster_contribution", row.get("contribution", 0.0)),
                        "pre_stance_cluster_contribution": row.get("pre_stance_cluster_contribution", 0.0),
                        "raw_stance_adjusted_cluster_contribution": row.get("raw_stance_adjusted_cluster_contribution", 0.0),
                        "stance_adjusted_cluster_contribution": row.get("stance_adjusted_cluster_contribution", 0.0),
                        "allocated_cluster_contribution": row.get("allocated_cluster_contribution", 0.0),
                        "allocation_share": row.get("allocation_share", 0.0),
                        "allocation_rank": row.get("allocation_rank", 0),
                        "allocation_role": row.get("allocation_role", ""),
                        "theme_allocation_reduction_effect": row.get("theme_allocation_reduction_effect", 0.0),
                        "allocation_capped": row.get("allocation_capped", False),
                        "event_activity_date": row.get("event_activity_date", ""),
                        "age_days": row.get("age_days"),
                        "age_bucket": row.get("age_bucket", ""),
                        "source_org_norm": row.get("source_org_norm", ""),
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
        signal_id = signal.get("id") or signal.get("policy_id")
        if not signal_id:
            errors.append(f"signal {index}: missing id")
        elif signal_id in seen:
            errors.append(f"signal {index}: duplicate id {signal_id}")
        else:
            seen.add(signal_id)
        field_aliases = {
            "title": ("title",),
            "source": ("source", "source_org"),
            "published_date": ("published_date", "publish_date"),
            "authority_level": ("authority_level",),
            "url": ("url", "source_url", "official_url"),
        }
        for field, aliases in field_aliases.items():
            if not any(signal.get(alias) for alias in aliases):
                errors.append(f"signal {signal_id or index}: missing {field}")
        published = signal.get("published_date") or signal.get("publish_date")
        if parse_date(published) is None:
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
