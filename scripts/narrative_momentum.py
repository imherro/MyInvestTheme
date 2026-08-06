from __future__ import annotations

from typing import Any


VERSION = "narrative_momentum_v1"


def build_narrative_dimension(theme: dict[str, Any], secondary_themes: list[dict[str, Any]]) -> dict[str, Any]:
    events = list(theme.get("all_event_contributors") or theme.get("top_event_contributors") or [])
    sources = {str(item.get("source_org_norm") or item.get("source") or "") for item in events if item.get("source_org_norm") or item.get("source")}
    active_subthemes = [item for item in secondary_themes if float(item.get("market_confirmation_score") or 0) >= 35]
    frequency = min(100.0, len(events) * 12.0)
    breadth = min(100.0, len(sources) * 22.0)
    expansion = min(100.0, len(active_subthemes) * 18.0)
    recent = float(theme.get("score_30d") or 0)
    prior = float(theme.get("score_31_60d") or 0)
    acceleration = 60.0 if recent > prior * 1.15 else (35.0 if recent > 0 else 10.0)
    fatigue = 55.0 if theme.get("lifecycle_state") in {"cooling", "legacy_tail"} else 10.0
    score = round(max(0.0, min(100.0, 0.30 * frequency + 0.25 * breadth + 0.25 * expansion + 0.20 * acceleration - 0.20 * fatigue)), 2)
    if not events:
        stage = "absent"
    elif fatigue >= 50:
        stage = "fading"
    elif score >= 72:
        stage = "mainstream"
    elif score >= 52:
        stage = "accelerating"
    else:
        stage = "forming"
    return {
        "scoring_version": VERSION,
        "narrative_momentum_score": score,
        "narrative_stage": stage,
        "narrative_frequency": round(frequency, 2),
        "narrative_breadth": round(breadth, 2),
        "narrative_acceleration": round(acceleration, 2),
        "subtheme_expansion": round(expansion, 2),
        "cross_domain_penetration": round((breadth + expansion) / 2, 2),
        "narrative_fatigue": round(fatigue, 2),
        "replacement_pressure": 0.0,
        "source_org_count": len(sources),
        "active_subtheme_count": len(active_subthemes),
        "data_coverage": 1.0 if events else 0.0,
        "evidence_basis": "官方政策事件、跨部门来源和二级主题扩散；未使用新闻文章数量。",
    }
