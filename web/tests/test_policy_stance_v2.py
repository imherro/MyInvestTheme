import asyncio
import sys
from datetime import date
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from policy_event_clustering import build_policy_event_clusters
from policy_stance import compute_cluster_theme_stance, compute_policy_theme_stance_v2
from theme_relevance import build_deduped_theme_summary, sort_theme_summary_v4_rows
from web.main import app


AI_THEME = {
    "theme_id": "ai_compute_communications",
    "theme_name": "AI算力/通信",
    "stance_profile": "growth_support",
    "core_keywords": ["人工智能", "算力"],
    "industry_keywords": ["服务器", "光模块"],
    "beneficiary_keywords": ["智能计算中心"],
    "policy_objectives": ["数字经济"],
    "negative_keywords": [],
    "theme_specific_supportive_keywords": [],
    "theme_specific_restrictive_keywords": [],
}


def policy(**kwargs):
    base = {
        "id": "policy-a",
        "title": "人工智能算力政策",
        "source": "国家发展改革委",
        "published_date": "2026-06-03",
        "policy_score_v2": 0.8,
        "summary": "支持人工智能算力基础设施建设。",
        "beneficiary_chain": ["智能计算中心", "服务器", "光模块"],
        "related_industries": ["人工智能", "算力"],
    }
    base.update(kwargs)
    return base


def stance_row(item, theme=AI_THEME, relevance=0.8):
    row = compute_policy_theme_stance_v2(item, theme)
    row.update(
        {
            "policy_id": item["id"],
            "relevance_score_v2": relevance,
            "policy_score_v2": item.get("policy_score_v2", 0.8),
            "published_date": item.get("published_date", ""),
        }
    )
    return row


def get(path: str) -> httpx.Response:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path)

    return asyncio.run(run())


def test_supportive_policy_gets_supportive_label():
    item = policy(summary="加快推进人工智能和智能算力基础设施建设，支持智能计算中心项目落地。")

    result = compute_policy_theme_stance_v2(item, AI_THEME)

    assert result["support_score"] > 0
    assert result["constraint_score"] == 0
    assert result["stance_score_v2"] >= 0.45
    assert result["stance_label"] == "supportive"
    assert result["direction_multiplier"] == 1.0


def test_restrictive_policy_does_not_boost_theme_contribution():
    item = policy(summary="对人工智能算力领域开展专项整治，严厉打击违规建设和无序扩张。")
    result = compute_policy_theme_stance_v2(item, AI_THEME)

    assert result["constraint_score"] > 0
    assert result["stance_label"] in ["mildly_restrictive", "restrictive"]
    assert result["direction_multiplier"] in [0.25, 0.0]

    clusters = build_policy_event_clusters([item], ["人工智能", "算力"])
    summary = build_deduped_theme_summary([item], [AI_THEME], clusters, date(2026, 6, 22))
    contributor = summary["themes"][0]["top_event_contributors"][0]
    assert contributor["stance_adjusted_cluster_contribution"] < contributor["pre_stance_cluster_contribution"]


def test_standardized_development_is_not_misread_as_negative():
    item = policy(summary="支持人工智能产业规范发展、健康发展和高质量发展。")

    result = compute_policy_theme_stance_v2(item, AI_THEME)

    assert result["support_score"] > result["constraint_score"]
    assert result["stance_label"] in ["supportive", "mildly_supportive"]


def test_unrelated_risk_terms_do_not_penalize_ai_theme():
    item = policy(summary="支持人工智能和算力基础设施建设，同时防范房地产领域风险。")

    result = compute_policy_theme_stance_v2(item, AI_THEME)

    assert result["constraint_score"] == 0
    assert result["stance_label"] in ["supportive", "mildly_supportive"]


def test_mixed_policy_is_not_full_support():
    item = policy(summary="推动人工智能应用发展，同时严控无序建设和重复投资。")

    result = compute_policy_theme_stance_v2(item, AI_THEME)

    assert result["support_score"] > 0
    assert result["constraint_score"] > 0
    assert result["stance_label"] in ["neutral_or_mixed", "mildly_restrictive", "mildly_supportive"]
    assert result["direction_multiplier"] != 1.0


def test_cluster_stance_keeps_restrictive_member_information():
    supportive = policy(id="policy-a", summary="支持人工智能算力建设。")
    restrictive = policy(
        id="policy-b",
        summary="对人工智能算力无序建设开展专项整治。",
        published_date="2026-06-04",
    )
    cluster = {"event_cluster_id": "event-ai", "member_policy_ids": ["policy-a", "policy-b"]}

    result = compute_cluster_theme_stance(cluster, [stance_row(supportive), stance_row(restrictive)])

    assert result["cluster_support_score"] > 0
    assert result["cluster_constraint_score"] > 0
    assert result["cluster_stance_score_v2"] == round(
        result["cluster_support_score"] - result["cluster_constraint_score"], 4
    )
    assert result["direction_multiplier"] < 1.0


def test_theme_score_v4_is_not_higher_than_v3_dedup():
    policies = [
        policy(id="policy-a", source_url="https://example.gov/policy", summary="支持人工智能算力建设。"),
        policy(
            id="policy-b",
            source_url="https://example.gov/policy",
            summary="对人工智能算力无序建设开展专项整治。",
            published_date="2026-06-04",
        ),
    ]
    clusters = build_policy_event_clusters(policies, ["人工智能", "算力"])
    summary = build_deduped_theme_summary(policies, [AI_THEME], clusters, date(2026, 6, 22))
    theme = summary["themes"][0]

    assert theme["theme_score_v4"] <= theme["theme_score_v3_dedup"]
    assert theme["stance_adjustment_effect"] > 0


def test_theme_sorting_uses_v4_not_v3_dedup():
    rows = [
        {
            "theme_id": "theme_a",
            "theme_score_v3_dedup": 2.0,
            "theme_score_v4": 0.8,
            "matched_event_cluster_count": 1,
            "supportive_cluster_count": 1,
            "avg_cluster_stance_score_v2": 0.8,
            "avg_cluster_relevance_score_v2": 0.8,
            "avg_cluster_policy_score_v2": 0.8,
        },
        {
            "theme_id": "theme_b",
            "theme_score_v3_dedup": 1.2,
            "theme_score_v4": 1.0,
            "matched_event_cluster_count": 1,
            "supportive_cluster_count": 1,
            "avg_cluster_stance_score_v2": 0.7,
            "avg_cluster_relevance_score_v2": 0.7,
            "avg_cluster_policy_score_v2": 0.7,
        },
    ]

    assert sort_theme_summary_v4_rows(rows)[0]["theme_id"] == "theme_b"


def test_no_theme_context_defaults_to_neutral_or_mixed():
    item = policy(summary="支持房地产风险化解。", beneficiary_chain=[], related_industries=[])

    result = compute_policy_theme_stance_v2(item, AI_THEME)

    assert result["support_score"] == 0.0
    assert result["constraint_score"] == 0.0
    assert result["stance_label"] == "neutral_or_mixed"
    assert result["direction_multiplier"] == 0.5


def test_missing_fields_do_not_raise():
    item = {"id": "policy-missing", "title": "人工智能政策"}

    result = compute_policy_theme_stance_v2(item, AI_THEME)

    for field in (
        "support_score",
        "constraint_score",
        "stance_score_v2",
        "stance_label",
        "direction_multiplier",
        "stance_evidence",
    ):
        assert field in result


def test_policy_stance_pipeline_is_deterministic():
    policies = [
        policy(id="policy-a", source_url="https://example.gov/policy", summary="支持人工智能算力建设。"),
        policy(
            id="policy-b",
            source_url="https://example.gov/policy",
            summary="对人工智能算力无序建设开展专项整治。",
            published_date="2026-06-04",
        ),
    ]
    outputs = []
    for _ in range(10):
        clusters = build_policy_event_clusters(policies, ["人工智能", "算力"])
        outputs.append(build_deduped_theme_summary(policies, [AI_THEME], clusters, date(2026, 6, 22)))

    assert all(item == outputs[0] for item in outputs)


def test_api_latest_exposes_policy_stance_versions():
    response = get("/api/latest")
    assert response.status_code == 200
    result = response.json()["result"]

    assert result["policy_stance_summary"]["scoring_version"] == "policy_theme_stance_v2"
    assert result["theme_summary"]["policy_stance_version"] == "policy_theme_stance_v2"
