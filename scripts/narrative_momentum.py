from __future__ import annotations

from typing import Any


VERSION = "official_narrative_diffusion_v1"


def build_narrative_dimension(theme: dict[str, Any], secondary_themes: list[dict[str, Any]]) -> dict[str, Any]:
    events = list(theme.get("all_event_contributors") or theme.get("top_event_contributors") or [])
    sources = {str(item.get("source_org_norm") or item.get("source") or "") for item in events if item.get("source_org_norm") or item.get("source")}
    active_subthemes = [item for item in secondary_themes if float(item.get("market_confirmation_score") or 0) >= 35]
    titles = [str(item.get("primary_policy_title") or item.get("title") or "") for item in events]
    strategic_terms = {term for title in titles for term in ("规划", "战略", "行动", "体系", "工程", "试点", "标准") if term in title}
    breadth = min(100.0, len(sources) * 20.0)
    expansion = min(100.0, len(active_subthemes) * 16.0)
    terminology = min(100.0, len(strategic_terms) * 18.0)
    domain_names = {str(item.get("theme_name") or "") for item in secondary_themes if item.get("theme_name")}
    cross_domain = min(100.0, len(domain_names) * 14.0)
    fatigue = 55.0 if theme.get("lifecycle_state") in {"cooling", "legacy_tail"} else 10.0
    score = round(max(0.0, min(100.0, 0.35 * breadth + 0.25 * terminology + 0.20 * expansion + 0.20 * cross_domain - 0.20 * fatigue)), 2)
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
        "dimension_name": "official_narrative_diffusion",
        "dimension_label": "官方战略叙事扩散",
        "official_narrative_diffusion_score": score,
        "narrative_momentum_score": score,
        "narrative_stage": stage,
        "narrative_frequency": None,
        "narrative_breadth": round(breadth, 2),
        "narrative_acceleration": None,
        "strategic_terminology_diffusion": round(terminology, 2),
        "subtheme_expansion": round(expansion, 2),
        "cross_domain_penetration": round(cross_domain, 2),
        "narrative_fatigue": round(fatigue, 2),
        "replacement_pressure": 0.0,
        "source_org_count": len(sources),
        "active_subtheme_count": len(active_subthemes),
        "data_coverage": 1.0 if events else 0.0,
        "evidence_basis": "跨部门战略表述、战略术语和子主题覆盖扩散；不重复使用政策总分或政策事件数量。",
        "semantics": "当前叙事维度仅表示官方战略表述和子主题扩散，不代表社会舆论热度。",
    }
