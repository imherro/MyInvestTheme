from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "config" / "policy_stance_rules.json"

TEXT_FIELD_ORDER = (
    "title",
    "summary",
    "policy_text",
    "key_points",
    "beneficiary_chain",
    "related_industries",
)

THEME_KEYWORD_FIELDS = (
    "core_keywords",
    "industry_keywords",
    "beneficiary_keywords",
    "policy_objectives",
    "theme_specific_supportive_keywords",
    "theme_specific_restrictive_keywords",
)

STANCE_LABELS = (
    "supportive",
    "mildly_supportive",
    "neutral_or_mixed",
    "mildly_restrictive",
    "restrictive",
)

NEGATED_SUPPORT_MARKERS = ("无序", "重复", "违规", "违法", "盲目", "过剩", "严控", "限制", "压降")


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
    return " ".join(flatten_text(value).replace("\u3000", " ").lower().split())


def collect_policy_text_fields(policy: dict[str, Any]) -> dict[str, str]:
    return {field: flatten_text(policy.get(field)).strip() for field in TEXT_FIELD_ORDER}


def split_sentences(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"[。！？!?；;\n\r]+", text or "") if item.strip()]


def load_stance_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _unique_keywords(values: list[str] | tuple[str, ...] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        normalized = normalize_text(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(str(value))
    return result


def _theme_keywords(theme: dict[str, Any]) -> list[str]:
    keywords: list[str] = []
    for field in THEME_KEYWORD_FIELDS:
        keywords.extend(theme.get(field, []) or [])
    return _unique_keywords(keywords)


def extract_theme_context_sentences(policy: dict[str, Any], theme: dict[str, Any]) -> list[dict[str, Any]]:
    keywords = _theme_keywords(theme)
    if not keywords:
        return []
    contexts: list[dict[str, Any]] = []
    for field, raw_text in collect_policy_text_fields(policy).items():
        for sentence in split_sentences(raw_text):
            normalized_sentence = normalize_text(sentence)
            matched = [keyword for keyword in keywords if normalize_text(keyword) in normalized_sentence]
            if matched:
                contexts.append(
                    {
                        "source_field": field,
                        "sentence": sentence,
                        "matched_theme_keywords": matched,
                    }
                )
    return contexts


def compute_direction_label_and_multiplier(
    stance_score: float,
    stance_rules: dict[str, Any] | None = None,
) -> tuple[str, float]:
    rules = stance_rules or load_stance_rules()
    thresholds = rules.get("label_thresholds", {})
    multipliers = rules.get("direction_multipliers", {})
    supportive = float(thresholds.get("supportive", 0.45))
    mildly_supportive = float(thresholds.get("mildly_supportive", 0.15))
    neutral_lower = float(thresholds.get("neutral_lower", -0.15))
    mildly_restrictive = float(thresholds.get("mildly_restrictive", -0.45))

    if stance_score >= supportive:
        label = "supportive"
    elif stance_score >= mildly_supportive:
        label = "mildly_supportive"
    elif stance_score > neutral_lower:
        label = "neutral_or_mixed"
    elif stance_score > mildly_restrictive:
        label = "mildly_restrictive"
    else:
        label = "restrictive"
    multiplier = float(multipliers.get(label, 0.5))
    return label, round(max(0.0, min(1.0, multiplier)), 4)


def _append_keyword_evidence(
    contexts: list[dict[str, Any]],
    keywords: list[str],
    keyword_type: str,
    score_component: str,
    score_per_hit: float,
    seen: set[str],
) -> tuple[float, list[dict[str, Any]]]:
    score = 0.0
    evidence: list[dict[str, Any]] = []
    for keyword in keywords:
        normalized_keyword = normalize_text(keyword)
        if not normalized_keyword or normalized_keyword in seen:
            continue
        for context in contexts:
            sentence = context.get("sentence", "")
            normalized_sentence = normalize_text(sentence)
            if normalized_keyword not in normalized_sentence:
                continue
            if score_component == "support_score" and keyword_type == "implementation_support_keywords":
                if _has_negated_support_context(normalized_sentence, normalized_keyword):
                    continue
            seen.add(normalized_keyword)
            score += score_per_hit
            matched_theme_keywords = context.get("matched_theme_keywords") or []
            evidence.append(
                {
                    "source_field": context.get("source_field", ""),
                    "sentence": context.get("sentence", ""),
                    "matched_theme_keyword": matched_theme_keywords[0] if matched_theme_keywords else "",
                    "matched_theme_keywords": matched_theme_keywords,
                    "stance_keyword": keyword,
                    "keyword_type": keyword_type,
                    "score_component": score_component,
                    "score_contribution": round(score_per_hit, 4),
                }
            )
            break
    return score, evidence


def _has_negated_support_context(normalized_sentence: str, normalized_keyword: str) -> bool:
    keyword_index = normalized_sentence.find(normalized_keyword)
    if keyword_index < 0:
        return False
    window_start = max(0, keyword_index - 8)
    window_end = min(len(normalized_sentence), keyword_index + len(normalized_keyword) + 8)
    local_window = normalized_sentence[window_start:window_end]
    return any(marker in local_window for marker in NEGATED_SUPPORT_MARKERS)


def compute_policy_theme_stance_v2(
    policy: dict[str, Any],
    theme: dict[str, Any],
    stance_rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rules = stance_rules or load_stance_rules()
    contexts = extract_theme_context_sentences(policy, theme)
    profile = theme.get("stance_profile") or rules.get("default_stance_profile") or "growth_support"

    support_seen: set[str] = set()
    constraint_seen: set[str] = set()
    evidence: list[dict[str, Any]] = []

    support_score = 0.0
    for field, score_per_hit in (
        ("supportive_action_keywords", 0.20),
        ("implementation_support_keywords", 0.15),
        ("positive_phrase_overrides", 0.25),
    ):
        score, field_evidence = _append_keyword_evidence(
            contexts,
            _unique_keywords(rules.get(field, [])),
            field,
            "support_score",
            score_per_hit,
            support_seen,
        )
        support_score += score
        evidence.extend(field_evidence)
    score, field_evidence = _append_keyword_evidence(
        contexts,
        _unique_keywords(theme.get("theme_specific_supportive_keywords", [])),
        "theme_specific_supportive_keywords",
        "support_score",
        0.20,
        support_seen,
    )
    support_score += score
    evidence.extend(field_evidence)

    constraint_score = 0.0
    for field, score_per_hit in (
        ("restrictive_action_keywords", 0.25),
        ("risk_constraint_keywords", 0.15),
        ("negative_phrase_overrides", 0.30),
    ):
        score, field_evidence = _append_keyword_evidence(
            contexts,
            _unique_keywords(rules.get(field, [])),
            field,
            "constraint_score",
            score_per_hit,
            constraint_seen,
        )
        constraint_score += score
        evidence.extend(field_evidence)
    score, field_evidence = _append_keyword_evidence(
        contexts,
        _unique_keywords(theme.get("theme_specific_restrictive_keywords", [])),
        "theme_specific_restrictive_keywords",
        "constraint_score",
        0.25,
        constraint_seen,
    )
    constraint_score += score
    evidence.extend(field_evidence)

    support_score = round(min(support_score, 1.0), 4)
    constraint_score = round(min(constraint_score, 1.0), 4)
    stance_score = round(support_score - constraint_score, 4)
    stance_label, direction_multiplier = compute_direction_label_and_multiplier(stance_score, rules)

    return {
        "policy_id": str(policy.get("id") or policy.get("policy_id") or ""),
        "theme_id": theme.get("theme_id", ""),
        "theme_name": theme.get("theme_name", ""),
        "stance_profile": profile,
        "support_score": support_score,
        "constraint_score": constraint_score,
        "stance_score_v2": stance_score,
        "stance_label": stance_label,
        "direction_multiplier": direction_multiplier,
        "stance_evidence": evidence,
    }


def _date_number(value: Any) -> int:
    digits = re.sub(r"\D+", "", str(value or ""))
    if len(digits) >= 8:
        return int(digits[:8])
    return 0


def compute_cluster_theme_stance(
    cluster: dict[str, Any],
    member_policy_theme_stances: list[dict[str, Any]],
) -> dict[str, Any]:
    if not member_policy_theme_stances:
        label, multiplier = compute_direction_label_and_multiplier(0.0)
        return {
            "event_cluster_id": cluster.get("event_cluster_id", ""),
            "theme_id": "",
            "theme_name": "",
            "cluster_support_score": 0.0,
            "cluster_constraint_score": 0.0,
            "cluster_stance_score_v2": 0.0,
            "cluster_stance_label": label,
            "direction_multiplier": multiplier,
            "selected_stance_policy_id": "",
            "top_stance_evidence": [],
        }

    cluster_support_score = round(max(float(row.get("support_score") or 0.0) for row in member_policy_theme_stances), 4)
    cluster_constraint_score = round(max(float(row.get("constraint_score") or 0.0) for row in member_policy_theme_stances), 4)
    cluster_stance_score = round(cluster_support_score - cluster_constraint_score, 4)
    label, multiplier = compute_direction_label_and_multiplier(cluster_stance_score)

    focus_field = "constraint_score" if label in {"restrictive", "mildly_restrictive"} else "support_score"
    selected = sorted(
        member_policy_theme_stances,
        key=lambda row: (
            -float(row.get(focus_field) or 0.0),
            -float(row.get("relevance_score_v2") or 0.0),
            -float(row.get("policy_score_v2") or 0.0),
            -_date_number(row.get("published_date")),
            str(row.get("policy_id") or ""),
        ),
    )[0]

    return {
        "event_cluster_id": cluster.get("event_cluster_id", ""),
        "theme_id": selected.get("theme_id", ""),
        "theme_name": selected.get("theme_name", ""),
        "cluster_support_score": cluster_support_score,
        "cluster_constraint_score": cluster_constraint_score,
        "cluster_stance_score_v2": cluster_stance_score,
        "cluster_stance_label": label,
        "direction_multiplier": multiplier,
        "selected_stance_policy_id": selected.get("policy_id", ""),
        "top_stance_evidence": selected.get("stance_evidence", [])[:8],
    }


def build_policy_stance_summary(
    policy_theme_stance_rows: list[dict[str, Any]],
    cluster_theme_stance_rows: list[dict[str, Any]] | None = None,
    stance_rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rules = stance_rules or load_stance_rules()
    cluster_rows = cluster_theme_stance_rows if cluster_theme_stance_rows is not None else policy_theme_stance_rows
    counts = {label: 0 for label in STANCE_LABELS}
    for row in cluster_rows:
        label = str(row.get("cluster_stance_label") or row.get("stance_label") or "neutral_or_mixed")
        if label in counts:
            counts[label] += 1
    return {
        "scoring_version": rules.get("version", "policy_theme_stance_v2"),
        "default_stance_profile": rules.get("default_stance_profile", "growth_support"),
        "direction_multipliers": rules.get("direction_multipliers", {}),
        "policy_theme_pair_count": len(policy_theme_stance_rows),
        "cluster_theme_pair_count": len(cluster_rows),
        "supportive_count": counts["supportive"],
        "mildly_supportive_count": counts["mildly_supportive"],
        "neutral_or_mixed_count": counts["neutral_or_mixed"],
        "mildly_restrictive_count": counts["mildly_restrictive"],
        "restrictive_count": counts["restrictive"],
    }
