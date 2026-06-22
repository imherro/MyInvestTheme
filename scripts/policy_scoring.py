from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any


DEFAULT_COMPONENT_SCORE = 0.5

KEY_AUTHORITY_SOURCES = (
    "国务院",
    "国家发展改革委",
    "国家发改委",
    "财政部",
    "中国证监会",
    "证监会",
)

AUTHORITY_LEVEL_SCORES = {
    "state_council": 1.0,
    "central_document": 1.0,
    "multi_ministry": 0.8,
    "national_ministry": 0.7,
    "national_regulator": 0.7,
    "ministry": 0.7,
    "exchange": 0.6,
    "provincial": 0.5,
    "municipal": 0.3,
    "city": 0.3,
    "industry_association": 0.3,
}

ACTIONABILITY_PATTERNS: tuple[tuple[float, tuple[str, ...]], ...] = (
    (0.3, ("资金", "投资", "预算", "专项资金")),
    (0.3, ("项目", "工程", "建设", "示范区")),
    (0.2, ("指标", "KPI", "kpi", "考核", "目标")),
    (0.2, ("时间节点", "年内")),
)

ECONOMIC_SCOPE_SCORES = {
    "national": 1.0,
    "cross_industry": 0.8,
    "industry": 0.6,
    "regional": 0.4,
    "local_pilot": 0.3,
}


def clamp_unit(value: Any, *, default: float = DEFAULT_COMPONENT_SCORE) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return max(0.0, min(1.0, number))


def parse_policy_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return None


def compute_authority_score(policy: dict[str, Any]) -> float:
    source = str(policy.get("source") or "")
    level = str(policy.get("authority_level") or "")
    if any(name in source for name in KEY_AUTHORITY_SOURCES):
        return 1.0 if "国务院" in source else 0.85
    if level:
        return AUTHORITY_LEVEL_SCORES.get(level, DEFAULT_COMPONENT_SCORE)
    return clamp_unit(policy.get("authority_score"), default=DEFAULT_COMPONENT_SCORE)


def compute_actionability_score(policy: dict[str, Any]) -> float:
    text_parts = [
        str(policy.get("title") or ""),
        str(policy.get("evidence") or ""),
        str(policy.get("policy_text") or ""),
    ]
    text = "\n".join(part for part in text_parts if part)
    if not text.strip():
        return clamp_unit(policy.get("actionability_score"), default=DEFAULT_COMPONENT_SCORE)

    score = 0.0
    for points, terms in ACTIONABILITY_PATTERNS:
        if any(term in text for term in terms):
            score += points
    if re.search(r"到20\d{2}|20\d{2}年|截至20\d{2}|力争20\d{2}", text):
        score += 0.2
    return min(1.0, score)


def compute_economic_scope_score(policy: dict[str, Any]) -> float:
    scope = str(policy.get("economic_scope") or "")
    if scope:
        return ECONOMIC_SCOPE_SCORES.get(scope, DEFAULT_COMPONENT_SCORE)
    return clamp_unit(policy.get("economic_scope_score"), default=DEFAULT_COMPONENT_SCORE)


def compute_time_decay_score(policy: dict[str, Any], basis: date | None = None) -> float:
    published = parse_policy_date(policy.get("published_date"))
    if published is None:
        return clamp_unit(policy.get("time_decay_score"), default=DEFAULT_COMPONENT_SCORE)
    basis = basis or date.today()
    days = (basis - published).days
    if days < 0:
        return 0.0
    return math.exp(-days / 30)


def policy_score_components(policy: dict[str, Any], basis: date | None = None) -> dict[str, float]:
    authority = compute_authority_score(policy)
    actionability = compute_actionability_score(policy)
    economic_scope = compute_economic_scope_score(policy)
    time_decay = compute_time_decay_score(policy, basis)
    score = (
        0.35 * authority
        + 0.25 * actionability
        + 0.20 * economic_scope
        + 0.20 * time_decay
    )
    return {
        "authority_score": authority,
        "actionability_score": actionability,
        "economic_scope_score": economic_scope,
        "time_decay_score": time_decay,
        "policy_score_v2": score,
    }


def compute_policy_score_v2(policy: dict[str, Any], basis: date | None = None) -> float:
    return policy_score_components(policy, basis)["policy_score_v2"]
