from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from policy_scoring import policy_score_components
from policy_event_clustering import compute_cluster_policy_score_v2
from policy_stance import build_policy_stance_summary, compute_cluster_theme_stance, compute_policy_theme_stance_v2
from theme_allocation import (
    allocate_event_theme_contributions,
    build_allocated_theme_summary,
    build_event_theme_claim_rows,
)
from mainline_lifecycle import build_lifecycle_adjusted_theme_summary, build_mainline_lifecycle_summary
from policy_field_provenance import field_provenance_for_policy


ROOT = Path(__file__).resolve().parents[1]
THEME_CONFIG_PATH = ROOT / "config" / "themes.json"
INPUT_RULES_PATH = ROOT / "config" / "theme_relevance_input_rules.json"
MIN_RELEVANCE_THRESHOLD = 0.25
STRICT_RELEVANCE_VERSION = "theme_relevance_strict_v1"
BENEFICIARY_FACT_FIELDS = ("extracted_entities", "extracted_measures", "extracted_targets")
OBJECTIVE_FIELD_NAMES = ("title", "official_summary", "official_key_points", "summary", "evidence", "key_points", "policy_text", "extracted_measures", "extracted_targets")
NEGATIVE_FIELD_NAMES = ("title", "official_summary", "official_key_points", "summary", "evidence", "key_points", "policy_text")


def flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(flatten_text(item) for _, item in sorted(value.items()))
    if isinstance(value, (list, tuple, set)):
        return " ".join(flatten_text(item) for item in value)
    return str(value)


def normalize_text(value: Any) -> str:
    return " ".join(flatten_text(value).replace("\u3000", " ").split()).lower()


def load_input_rules(path: Path = INPUT_RULES_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def collect_policy_text_fields(
    policy: dict[str, Any],
    *,
    include_inference: bool = False,
    rules: dict[str, Any] | None = None,
) -> dict[str, str]:
    active = rules or load_input_rules()
    allowed_fields = list(active.get("allowed_fields") or [])
    if include_inference:
        allowed_fields.extend(active.get("inference_only_fields") or [])
    allowed_sources = set(active.get("allowed_source_types") or [])
    if include_inference:
        allowed_sources.update({"llm_inference", "manual_annotation"})
    provenance = field_provenance_for_policy(policy)
    result: dict[str, str] = {}
    for field in allowed_fields:
        source_type = (provenance.get(field) or {}).get("source_type", "legacy_unknown")
        if source_type not in allowed_sources:
            continue
        value = policy.get(field)
        if field == "summary" and not value:
            value = policy.get("evidence")
        normalized = normalize_text(value)
        if normalized:
            result[field] = normalized
    return result


def load_theme_config(path: Path = THEME_CONFIG_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    themes = payload.get("themes", [])
    return themes if isinstance(themes, list) else []


def theme_keywords(themes: list[dict[str, Any]]) -> list[str]:
    fields = ("core_keywords", "industry_keywords", "beneficiary_keywords", "policy_objectives")
    keywords: list[str] = []
    seen: set[str] = set()
    for theme in themes:
        for field in fields:
            for keyword in theme.get(field, []) or []:
                normalized = normalize_text(keyword)
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    keywords.append(keyword)
    return keywords


def _keyword_hit(text: str, keyword: str) -> bool:
    needle = normalize_text(keyword)
    return bool(needle) and needle in text


def match_keywords(
    text_fields: dict[str, str],
    keywords: list[str] | tuple[str, ...],
    keyword_type: str,
    score_per_hit: float,
    score_component: str,
    *,
    fields: tuple[str, ...],
    field_weights: dict[str, float] | None = None,
) -> tuple[float, list[dict[str, Any]]]:
    score = 0.0
    evidence: list[dict[str, Any]] = []
    seen_keywords: set[str] = set()
    for keyword in keywords or []:
        normalized_keyword = normalize_text(keyword)
        if not normalized_keyword or normalized_keyword in seen_keywords:
            continue
        for field in fields:
            text = text_fields.get(field, "")
            if _keyword_hit(text, keyword):
                seen_keywords.add(normalized_keyword)
                contribution = score_per_hit * float((field_weights or {}).get(field, 1.0))
                score += contribution
                evidence.append(
                    {
                        "source_field": field,
                        "keyword": keyword,
                        "keyword_type": keyword_type,
                        "score_component": score_component,
                        "score_contribution": round(contribution, 4),
                    }
                )
                break
    return score, evidence


def compute_negative_filter(
    text_fields: dict[str, str], negative_keywords: list[str] | tuple[str, ...], fields: tuple[str, ...]
) -> tuple[float, list[dict[str, Any]]]:
    score, evidence = match_keywords(
        text_fields,
        negative_keywords,
        "negative_keywords",
        0.0,
        "negative_filter_score",
        fields=fields,
    )
    del score
    hit_count = len(evidence)
    if hit_count == 0:
        return 1.0, []
    if hit_count == 1:
        filter_score = 0.7
    elif hit_count == 2:
        filter_score = 0.4
    else:
        filter_score = 0.2
    for index, item in enumerate(evidence):
        item["score_contribution"] = -0.3 if index < 2 else -0.2
    return filter_score, evidence


def _round_score(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 4)


def compute_theme_contribution(policy_score_v2: float, relevance_score_v2: float) -> float:
    return round(policy_score_v2 * relevance_score_v2, 4)


def sort_theme_summary_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            -row["theme_score_v2"],
            -row["matched_policy_count"],
            -row["avg_relevance_score_v2"],
            row["theme_id"],
        ),
    )


def sort_theme_summary_v3_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            -row["theme_score_v3"],
            -row["matched_event_cluster_count"],
            -row["avg_cluster_relevance_score_v2"],
            -row["avg_cluster_policy_score_v2"],
            row["theme_id"],
        ),
    )


def sort_theme_summary_v4_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            -float(row.get("theme_score_v4") or 0.0),
            -int(row.get("matched_event_cluster_count") or 0),
            -int(row.get("supportive_cluster_count") or 0),
            -float(row.get("avg_cluster_stance_score_v2") or 0.0),
            -float(row.get("avg_cluster_relevance_score_v2") or 0.0),
            -float(row.get("avg_cluster_policy_score_v2") or 0.0),
            row.get("theme_id", ""),
        ),
    )


def _compute_theme_relevance(
    policy: dict[str, Any],
    theme: dict[str, Any],
    *,
    include_inference: bool,
    rules: dict[str, Any],
) -> dict[str, Any]:
    text_fields = collect_policy_text_fields(policy, include_inference=include_inference, rules=rules)
    keyword_fields = tuple(text_fields)
    beneficiary_fields = tuple(
        field for field in text_fields if field in BENEFICIARY_FACT_FIELDS or field in {"beneficiary_chain", "related_industries"}
    )
    objective_fields = tuple(field for field in text_fields if field in OBJECTIVE_FIELD_NAMES)
    negative_fields = tuple(field for field in text_fields if field in NEGATIVE_FIELD_NAMES)
    field_weights = {str(key): float(value) for key, value in (rules.get("field_weights") or {}).items()}
    matched_evidence: list[dict[str, Any]] = []

    core_score, core_evidence = match_keywords(
        text_fields,
        theme.get("core_keywords", []),
        "core_keywords",
        0.25,
        "keyword_score",
        fields=keyword_fields,
        field_weights=field_weights,
    )
    industry_score, industry_evidence = match_keywords(
        text_fields,
        theme.get("industry_keywords", []),
        "industry_keywords",
        0.15,
        "keyword_score",
        fields=keyword_fields,
        field_weights=field_weights,
    )
    beneficiary_keyword_score, beneficiary_keyword_evidence = match_keywords(
        text_fields,
        theme.get("beneficiary_keywords", []),
        "beneficiary_keywords",
        0.20,
        "keyword_score",
        fields=keyword_fields,
        field_weights=field_weights,
    )
    keyword_score = min(1.0, core_score + industry_score + beneficiary_keyword_score)
    matched_evidence.extend(core_evidence)
    matched_evidence.extend(industry_evidence)
    matched_evidence.extend(beneficiary_keyword_evidence)

    beneficiary_from_beneficiary, beneficiary_evidence = match_keywords(
        text_fields,
        theme.get("beneficiary_keywords", []),
        "beneficiary_keywords",
        0.30,
        "beneficiary_score",
        fields=beneficiary_fields,
        field_weights=field_weights,
    )
    industry_from_beneficiary, industry_beneficiary_evidence = match_keywords(
        text_fields,
        theme.get("industry_keywords", []),
        "industry_keywords",
        0.20,
        "beneficiary_score",
        fields=beneficiary_fields,
        field_weights=field_weights,
    )
    core_from_beneficiary, core_beneficiary_evidence = match_keywords(
        text_fields,
        theme.get("core_keywords", []),
        "core_keywords",
        0.15,
        "beneficiary_score",
        fields=beneficiary_fields,
        field_weights=field_weights,
    )
    beneficiary_score = min(1.0, beneficiary_from_beneficiary + industry_from_beneficiary + core_from_beneficiary)
    matched_evidence.extend(beneficiary_evidence)
    matched_evidence.extend(industry_beneficiary_evidence)
    matched_evidence.extend(core_beneficiary_evidence)

    objective_score_raw, objective_evidence = match_keywords(
        text_fields,
        theme.get("policy_objectives", []),
        "policy_objectives",
        0.25,
        "policy_objective_score",
        fields=objective_fields,
        field_weights=field_weights,
    )
    policy_objective_score = min(1.0, objective_score_raw)
    matched_evidence.extend(objective_evidence)

    negative_filter_score, negative_evidence = compute_negative_filter(text_fields, theme.get("negative_keywords", []), negative_fields)
    matched_evidence.extend(negative_evidence)

    base_relevance = 0.45 * keyword_score + 0.35 * beneficiary_score + 0.20 * policy_objective_score
    relevance_score = base_relevance * negative_filter_score

    return {
        "theme_id": theme.get("theme_id", ""),
        "theme_name": theme.get("theme_name", ""),
        "relevance_score_v2": _round_score(relevance_score),
        "base_relevance": _round_score(base_relevance),
        "keyword_score": _round_score(keyword_score),
        "beneficiary_score": _round_score(beneficiary_score),
        "policy_objective_score": _round_score(policy_objective_score),
        "negative_filter_score": _round_score(negative_filter_score),
        "matched_evidence": matched_evidence,
        "input_fields": sorted(text_fields),
    }


def compute_theme_relevance_v2(policy: dict[str, Any], theme: dict[str, Any]) -> dict[str, Any]:
    rules = load_input_rules()
    strict = _compute_theme_relevance(policy, theme, include_inference=False, rules=rules)
    comparison = _compute_theme_relevance(policy, theme, include_inference=True, rules=rules)
    strict_score = float(strict["relevance_score_v2"])
    comparison_score = float(comparison["relevance_score_v2"])
    inference_lift = round(comparison_score - strict_score, 4)
    threshold = float(rules.get("high_inference_dependency_threshold") or 0.15)
    result = dict(strict)
    result.update(
        {
            "scoring_version": STRICT_RELEVANCE_VERSION,
            "input_rules_version": rules.get("version", "theme_relevance_input_v1"),
            "production_mode": rules.get("production_mode", "strict_point_in_time"),
            "theme_relevance_strict": strict_score,
            "theme_relevance_with_inference": comparison_score,
            "inference_lift": inference_lift,
            "high_inference_dependency": inference_lift >= threshold,
            "warnings": ["HIGH_INFERENCE_DEPENDENCY"] if inference_lift >= threshold else [],
            "strict_input_fields": strict["input_fields"],
            "inference_input_fields": comparison["input_fields"],
            "relevance_score_v2": strict_score,
        }
    )
    return result


def build_theme_relevance_input_summary(theme_summary: dict[str, Any]) -> dict[str, Any]:
    rules = load_input_rules()
    dependency_rows: list[dict[str, Any]] = []
    comparison_count = 0
    for theme in theme_summary.get("themes", []) or []:
        for event in theme.get("all_event_contributors", []) or []:
            comparison_count += 1
            lift = float(event.get("inference_lift") or 0.0)
            if "HIGH_INFERENCE_DEPENDENCY" in (event.get("relevance_warnings") or []):
                dependency_rows.append(
                    {
                        "theme_id": theme.get("theme_id", ""),
                        "theme_name": theme.get("theme_name", ""),
                        "policy_id": event.get("selected_relevance_policy_id", ""),
                        "event_cluster_id": event.get("event_cluster_id", ""),
                        "theme_relevance_strict": event.get("theme_relevance_strict", 0.0),
                        "theme_relevance_with_inference": event.get("theme_relevance_with_inference", 0.0),
                        "inference_lift": lift,
                        "warning": "HIGH_INFERENCE_DEPENDENCY",
                    }
                )
    dependency_rows.sort(key=lambda item: (-item["inference_lift"], item["theme_id"], item["policy_id"]))
    return {
        "scoring_version": rules.get("version", "theme_relevance_input_v1"),
        "production_mode": rules.get("production_mode", "strict_point_in_time"),
        "production_score_field": "theme_relevance_strict",
        "comparison_score_field": "theme_relevance_with_inference",
        "forbidden_production_fields": list(rules.get("forbidden_production_fields") or []),
        "comparison_count": comparison_count,
        "high_inference_dependency_count": len(dependency_rows),
        "high_inference_dependency_threshold": rules.get("high_inference_dependency_threshold", 0.15),
        "warnings": dependency_rows,
    }


def build_theme_summary(
    signals: list[dict[str, Any]],
    themes: list[dict[str, Any]],
    basis: date,
    *,
    min_threshold: float = MIN_RELEVANCE_THRESHOLD,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for theme in themes:
        contributors: list[dict[str, Any]] = []
        for policy in signals:
            relevance = compute_theme_relevance_v2(policy, theme)
            relevance_score = float(relevance["relevance_score_v2"])
            if (
                relevance_score < min_threshold
                and float(relevance.get("base_relevance") or 0.0) < min_threshold
                and float(relevance.get("keyword_score") or 0.0) < 0.5
            ):
                continue
            policy_components = policy_score_components(policy, basis)
            policy_score = round(policy_components["policy_score_v2"], 4)
            contribution = compute_theme_contribution(policy_score, relevance_score)
            contributors.append(
                {
                    "policy_id": policy.get("id", ""),
                    "title": policy.get("title", ""),
                    "source": policy.get("source", ""),
                    "published_date": policy.get("published_date", ""),
                    "url": policy.get("url", ""),
                    "relevance_score_v2": relevance_score,
                    "theme_relevance_strict": relevance["theme_relevance_strict"],
                    "theme_relevance_with_inference": relevance["theme_relevance_with_inference"],
                    "inference_lift": relevance["inference_lift"],
                    "relevance_warnings": relevance["warnings"],
                    "contribution": contribution,
                    "keyword_score": relevance["keyword_score"],
                    "beneficiary_score": relevance["beneficiary_score"],
                    "policy_objective_score": relevance["policy_objective_score"],
                    "negative_filter_score": relevance["negative_filter_score"],
                    "base_relevance": relevance["base_relevance"],
                    "matched_evidence": relevance["matched_evidence"],
                    **policy_components,
                    "policy_score_v2": policy_score,
                }
            )
        contributors.sort(key=lambda row: (-row["contribution"], row["policy_id"]))
        matched_count = len(contributors)
        theme_score = round(sum(row["contribution"] for row in contributors), 4)
        avg_relevance = round(sum(row["relevance_score_v2"] for row in contributors) / matched_count, 4) if matched_count else 0.0
        avg_policy = round(sum(row["policy_score_v2"] for row in contributors) / matched_count, 4) if matched_count else 0.0
        rows.append(
            {
                "theme_id": theme.get("theme_id", ""),
                "theme_name": theme.get("theme_name", ""),
                "theme_score_v2": theme_score,
                "matched_policy_count": matched_count,
                "avg_relevance_score_v2": avg_relevance,
                "avg_policy_score_v2": avg_policy,
                "top_policy_contributors": contributors[:3],
            }
        )

    rows = sort_theme_summary_rows(rows)
    return {
        "scoring_version": "theme_relevance_v2",
        "min_relevance_threshold": min_threshold,
        "themes": rows,
    }


def build_deduped_theme_summary(
    signals: list[dict[str, Any]],
    themes: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    basis: date,
    *,
    min_threshold: float = MIN_RELEVANCE_THRESHOLD,
) -> dict[str, Any]:
    policies_by_id = {
        str(policy.get("id") or policy.get("policy_id") or f"policy_{index:04d}"): policy
        for index, policy in enumerate(signals)
    }
    cluster_by_policy: dict[str, dict[str, Any]] = {}
    for cluster in clusters:
        for policy_id in cluster.get("member_policy_ids", []) or []:
            cluster_by_policy[str(policy_id)] = cluster

    rows: list[dict[str, Any]] = []
    policy_theme_stance_rows: list[dict[str, Any]] = []
    cluster_theme_stance_rows: list[dict[str, Any]] = []
    for theme in themes:
        raw_contributors: list[dict[str, Any]] = []
        for policy_id, policy in policies_by_id.items():
            relevance = compute_theme_relevance_v2(policy, theme)
            relevance_score = float(relevance["relevance_score_v2"])
            if (
                relevance_score < min_threshold
                and float(relevance.get("base_relevance") or 0.0) < min_threshold
                and float(relevance.get("keyword_score") or 0.0) < 0.5
            ):
                continue
            policy_components = policy_score_components(policy, basis)
            policy_score = round(policy_components["policy_score_v2"], 4)
            stance = compute_policy_theme_stance_v2(policy, theme)
            stance_row = {
                **stance,
                "policy_id": policy_id,
                "relevance_score_v2": relevance_score,
                "policy_score_v2": policy_score,
                "published_date": policy.get("published_date", policy.get("publish_date", "")),
            }
            policy_theme_stance_rows.append(stance_row)
            raw_contributors.append(
                {
                    "policy_id": policy_id,
                    "title": policy.get("title", ""),
                    "source": policy.get("source", ""),
                    "published_date": policy.get("published_date", ""),
                    "url": policy.get("url", ""),
                    "policy_score_v2": policy_score,
                    "relevance_score_v2": relevance_score,
                    "theme_relevance_strict": relevance["theme_relevance_strict"],
                    "theme_relevance_with_inference": relevance["theme_relevance_with_inference"],
                    "inference_lift": relevance["inference_lift"],
                    "relevance_warnings": relevance["warnings"],
                    "contribution": compute_theme_contribution(policy_score, relevance_score),
                    "keyword_score": relevance["keyword_score"],
                    "beneficiary_score": relevance["beneficiary_score"],
                    "policy_objective_score": relevance["policy_objective_score"],
                    "negative_filter_score": relevance["negative_filter_score"],
                    "base_relevance": relevance["base_relevance"],
                    "matched_evidence": relevance["matched_evidence"],
                    "support_score": stance_row["support_score"],
                    "constraint_score": stance_row["constraint_score"],
                    "stance_score_v2": stance_row["stance_score_v2"],
                    "stance_label": stance_row["stance_label"],
                    "direction_multiplier": stance_row["direction_multiplier"],
                    "stance_evidence": stance_row["stance_evidence"],
                    "stance_profile": stance_row["stance_profile"],
                    **policy_components,
                    "policy_score_v2": policy_score,
                }
            )

        raw_by_cluster: dict[str, list[dict[str, Any]]] = {}
        for contributor in raw_contributors:
            cluster = cluster_by_policy.get(contributor["policy_id"])
            if not cluster:
                continue
            raw_by_cluster.setdefault(cluster["event_cluster_id"], []).append(contributor)

        event_contributors: list[dict[str, Any]] = []
        for cluster in clusters:
            cluster_id = cluster["event_cluster_id"]
            members = raw_by_cluster.get(cluster_id, [])
            if not members:
                continue
            primary_policy = policies_by_id.get(str(cluster.get("primary_policy_id", "")), {})
            cluster_policy_score = compute_cluster_policy_score_v2(cluster, policies_by_id)
            selected = sorted(members, key=lambda row: (-row["relevance_score_v2"], row["policy_id"]))[0]
            cluster_relevance = selected["relevance_score_v2"]
            cluster_stance = compute_cluster_theme_stance(cluster, members)
            cluster_theme_stance_rows.append(cluster_stance)
            raw_cluster_contribution = round(sum(float(member.get("contribution") or 0.0) for member in members), 4)
            uncapped_pre_stance_contribution = compute_theme_contribution(cluster_policy_score, cluster_relevance)
            pre_stance_contribution = min(uncapped_pre_stance_contribution, raw_cluster_contribution)
            direction_multiplier = float(cluster_stance.get("direction_multiplier") or 0.0)
            adjusted_contribution = round(pre_stance_contribution * min(direction_multiplier, 1.0), 4)
            event_contributors.append(
                {
                    "event_cluster_id": cluster_id,
                    "theme_id": theme.get("theme_id", ""),
                    "theme_name": theme.get("theme_name", ""),
                    "primary_policy_id": cluster.get("primary_policy_id", ""),
                    "primary_policy_title": cluster.get("primary_policy_title", ""),
                    "source": primary_policy.get("source", ""),
                    "published_date": primary_policy.get("published_date", primary_policy.get("publish_date", "")),
                    "url": primary_policy.get("url", primary_policy.get("source_url", primary_policy.get("official_url", ""))),
                    "member_policy_ids": cluster.get("member_policy_ids", []),
                    "cluster_size": cluster.get("cluster_size", 0),
                    "cluster_policy_score_v2": cluster_policy_score,
                    "cluster_relevance_score_v2": cluster_relevance,
                    "theme_relevance_strict": selected.get("theme_relevance_strict", cluster_relevance),
                    "theme_relevance_with_inference": selected.get("theme_relevance_with_inference", cluster_relevance),
                    "inference_lift": selected.get("inference_lift", 0.0),
                    "relevance_warnings": selected.get("relevance_warnings", []),
                    "cluster_support_score": cluster_stance.get("cluster_support_score", 0.0),
                    "cluster_constraint_score": cluster_stance.get("cluster_constraint_score", 0.0),
                    "cluster_stance_score_v2": cluster_stance.get("cluster_stance_score_v2", 0.0),
                    "cluster_stance_label": cluster_stance.get("cluster_stance_label", "neutral_or_mixed"),
                    "direction_multiplier": direction_multiplier,
                    "pre_stance_cluster_contribution": pre_stance_contribution,
                    "uncapped_pre_stance_cluster_contribution": uncapped_pre_stance_contribution,
                    "deduplication_cap_applied": uncapped_pre_stance_contribution > raw_cluster_contribution,
                    "stance_adjusted_cluster_contribution": adjusted_contribution,
                    "stance_adjustment_effect": round(max(pre_stance_contribution - adjusted_contribution, 0.0), 4),
                    "cluster_contribution": pre_stance_contribution,
                    "selected_relevance_policy_id": selected["policy_id"],
                    "selected_stance_policy_id": cluster_stance.get("selected_stance_policy_id", ""),
                    "cluster_reason": cluster.get("cluster_reason", []),
                    "metrics": cluster.get("metrics", {}),
                    "top_matched_evidence": selected.get("matched_evidence", []),
                    "top_stance_evidence": cluster_stance.get("top_stance_evidence", []),
                }
            )

        event_contributors.sort(key=lambda row: (-row["stance_adjusted_cluster_contribution"], row["event_cluster_id"]))
        theme_score_v2_raw = round(sum(row["contribution"] for row in raw_contributors), 4)
        theme_score_v3_dedup = round(sum(row["pre_stance_cluster_contribution"] for row in event_contributors), 4)
        theme_score_v4 = round(sum(row["stance_adjusted_cluster_contribution"] for row in event_contributors), 4)
        deduplication_effect = round(max(theme_score_v2_raw - theme_score_v3_dedup, 0.0), 4)
        stance_adjustment_effect = round(max(theme_score_v3_dedup - theme_score_v4, 0.0), 4)
        event_count = len(event_contributors)
        avg_cluster_relevance = (
            round(sum(row["cluster_relevance_score_v2"] for row in event_contributors) / event_count, 4)
            if event_count
            else 0.0
        )
        avg_cluster_policy = (
            round(sum(row["cluster_policy_score_v2"] for row in event_contributors) / event_count, 4)
            if event_count
            else 0.0
        )
        avg_cluster_stance = (
            round(sum(row["cluster_stance_score_v2"] for row in event_contributors) / event_count, 4)
            if event_count
            else 0.0
        )
        label_counts = {
            "supportive": 0,
            "mildly_supportive": 0,
            "neutral_or_mixed": 0,
            "mildly_restrictive": 0,
            "restrictive": 0,
        }
        for contributor in event_contributors:
            label = contributor.get("cluster_stance_label", "neutral_or_mixed")
            if label in label_counts:
                label_counts[label] += 1
        rows.append(
            {
                "theme_id": theme.get("theme_id", ""),
                "theme_name": theme.get("theme_name", ""),
                "theme_score_v4": theme_score_v4,
                "theme_score_v3_dedup": theme_score_v3_dedup,
                "theme_score_v3": theme_score_v3_dedup,
                "theme_score_v2_raw": theme_score_v2_raw,
                "matched_event_cluster_count": event_count,
                "matched_policy_count_raw": len(raw_contributors),
                "deduplication_effect": deduplication_effect,
                "stance_adjustment_effect": stance_adjustment_effect,
                "supportive_cluster_count": label_counts["supportive"],
                "mildly_supportive_cluster_count": label_counts["mildly_supportive"],
                "neutral_or_mixed_cluster_count": label_counts["neutral_or_mixed"],
                "mildly_restrictive_cluster_count": label_counts["mildly_restrictive"],
                "restrictive_cluster_count": label_counts["restrictive"],
                "avg_cluster_relevance_score_v2": avg_cluster_relevance,
                "avg_cluster_policy_score_v2": avg_cluster_policy,
                "avg_cluster_stance_score_v2": avg_cluster_stance,
                "top_event_contributors": event_contributors[:3],
                "all_event_contributors": event_contributors,
            }
        )

    v4_summary = {
        "scoring_version": "theme_score_v4_stance_adjusted",
        "base_relevance_version": STRICT_RELEVANCE_VERSION,
        "theme_relevance_input_rules_version": load_input_rules().get("version", "theme_relevance_input_v1"),
        "production_relevance_field": "theme_relevance_strict",
        "event_clustering_version": "policy_event_clustering_v2",
        "policy_stance_version": "policy_theme_stance_v2",
        "min_relevance_threshold": min_threshold,
        "policy_stance_summary": build_policy_stance_summary(policy_theme_stance_rows, cluster_theme_stance_rows),
        "themes": sort_theme_summary_v4_rows(rows),
    }
    allocation_summary = allocate_event_theme_contributions(build_event_theme_claim_rows(v4_summary))
    allocated_summary = build_allocated_theme_summary(v4_summary, allocation_summary)
    lifecycle_summary = build_mainline_lifecycle_summary(allocated_summary, basis)
    return build_lifecycle_adjusted_theme_summary(allocated_summary, lifecycle_summary)
