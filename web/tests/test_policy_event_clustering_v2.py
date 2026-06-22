import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from policy_event_clustering import (
    build_event_cluster_summary,
    build_policy_event_clusters,
    compute_cluster_policy_score_v2,
    should_cluster_policies,
)
from theme_relevance import (
    build_deduped_theme_summary,
    compute_theme_contribution,
    sort_theme_summary_v3_rows,
)


KEYWORDS = ["人工智能", "算力", "智能计算中心", "服务器", "光模块", "数字经济"]
AI_THEME = {
    "theme_id": "ai_compute_communications",
    "theme_name": "AI算力/通信",
    "core_keywords": ["人工智能", "算力"],
    "industry_keywords": ["服务器", "光模块"],
    "beneficiary_keywords": ["智能计算中心"],
    "policy_objectives": ["数字经济"],
    "negative_keywords": ["整治", "风险提示"],
}


def policy(**kwargs):
    base = {
        "id": "policy-a",
        "title": "人工智能算力基础设施建设政策",
        "source": "国家发展改革委",
        "published_date": "2026-06-03",
        "policy_score_v2": 0.8,
        "summary": "支持人工智能算力和数字经济。",
        "beneficiary_chain": ["智能计算中心", "服务器", "光模块"],
        "related_industries": ["数据中心"],
    }
    base.update(kwargs)
    return base


def test_same_source_url_must_cluster():
    policies = [
        policy(id="policy-a", source_url="https://example.gov/policy"),
        policy(id="policy-b", source_url="https://example.gov/policy"),
    ]

    clusters = build_policy_event_clusters(policies, KEYWORDS)

    assert len(clusters) == 1
    assert clusters[0]["cluster_size"] == 2
    assert "same_source_url" in clusters[0]["cluster_reason"]


def test_same_org_within_window_similar_title_clusters():
    left = policy(id="policy-a", source="国家发展改革委", published_date="2026-06-03")
    right = policy(id="policy-b", source="国家发改委", published_date="2026-06-05", title="人工智能算力基础设施建设通知")

    should_cluster, reasons, _ = should_cluster_policies(left, right, KEYWORDS)

    assert should_cluster is True
    assert "same_source_org" in reasons
    assert "publish_date_within_7_days" in reasons


def test_same_org_outside_window_does_not_standard_cluster():
    left = policy(id="policy-a", source="国家发展改革委", published_date="2026-06-03")
    right = policy(id="policy-b", source="国家发改委", published_date="2026-06-23", title="人工智能算力基础设施建设通知")

    should_cluster, _, _ = should_cluster_policies(left, right, KEYWORDS)

    assert should_cluster is False


def test_different_org_requires_strict_title_and_keyword_overlap():
    left = policy(id="policy-a", source="国家发展改革委", published_date="2026-06-03")
    right = policy(id="policy-b", source="工业和信息化部", published_date="2026-06-05")

    should_cluster, reasons, _ = should_cluster_policies(left, right, KEYWORDS)

    assert should_cluster is True
    assert "weak_source_match" in reasons

    weak = policy(
        id="policy-c",
        source="工业和信息化部",
        published_date="2026-06-05",
        summary="支持其他方向。",
        beneficiary_chain=[],
        related_industries=[],
    )
    should_cluster_weak, _, _ = should_cluster_policies(left, weak, KEYWORDS)
    assert should_cluster_weak is False


def test_missing_date_requires_strict_match():
    left = policy(id="policy-a", source="国家发展改革委", published_date="")
    right = policy(id="policy-b", source="国家发改委")

    should_cluster, reasons, _ = should_cluster_policies(left, right, KEYWORDS)

    assert should_cluster is True
    assert "missing_publish_date" in reasons

    weak = policy(id="policy-c", source="国家发改委", published_date="", beneficiary_chain=[], related_industries=[], summary="无关内容")
    should_cluster_weak, _, _ = should_cluster_policies(left, weak, KEYWORDS)
    assert should_cluster_weak is False


def test_cluster_policy_score_takes_max_not_sum():
    policies_by_id = {
        "policy-a": policy(id="policy-a", policy_score_v2=0.6),
        "policy-b": policy(id="policy-b", policy_score_v2=0.9),
    }
    cluster = {"member_policy_ids": ["policy-a", "policy-b"]}

    assert compute_cluster_policy_score_v2(cluster, policies_by_id) == 0.9


def test_same_cluster_contributes_once_per_theme():
    cluster_policy_score = 0.8
    relevance = max(0.7, 0.6)

    assert compute_theme_contribution(cluster_policy_score, relevance) == 0.56


def test_theme_score_v3_is_less_than_raw_when_duplicate_policies_exist():
    policies = [
        policy(id="policy-a", source_url="https://example.gov/policy", policy_score_v2=0.8),
        policy(id="policy-b", source_url="https://example.gov/policy", policy_score_v2=0.8),
    ]
    clusters = build_policy_event_clusters(policies, KEYWORDS)
    summary = build_deduped_theme_summary(policies, [AI_THEME], clusters, date(2026, 6, 22))
    theme = summary["themes"][0]

    assert theme["theme_score_v2_raw"] > theme["theme_score_v3"]
    assert theme["deduplication_effect"] > 0
    assert theme["theme_score_v3"] <= theme["theme_score_v2_raw"]


def test_no_duplicate_has_zero_deduplication_effect():
    policies = [
        policy(id="policy-a", title="人工智能算力基础设施建设政策"),
        policy(
            id="policy-b",
            title="医药创新发展政策",
            source="中国证监会",
            published_date="2026-04-10",
            summary="支持创新药和医疗器械。",
            beneficiary_chain=["创新药"],
            related_industries=["医药"],
        ),
    ]
    clusters = build_policy_event_clusters(policies, KEYWORDS)
    summary = build_deduped_theme_summary(policies[:1], [AI_THEME], clusters[:1], date(2026, 6, 22))
    theme = summary["themes"][0]

    assert len(clusters) == len(policies)
    assert theme["deduplication_effect"] == 0.0


def test_theme_sorting_uses_v3_not_raw_score():
    rows = [
        {
            "theme_id": "theme_a",
            "theme_score_v2_raw": 2.0,
            "theme_score_v3": 0.8,
            "matched_event_cluster_count": 1,
            "avg_cluster_relevance_score_v2": 0.8,
            "avg_cluster_policy_score_v2": 0.8,
        },
        {
            "theme_id": "theme_b",
            "theme_score_v2_raw": 1.2,
            "theme_score_v3": 1.0,
            "matched_event_cluster_count": 1,
            "avg_cluster_relevance_score_v2": 0.7,
            "avg_cluster_policy_score_v2": 0.7,
        },
    ]

    assert sort_theme_summary_v3_rows(rows)[0]["theme_id"] == "theme_b"


def test_policy_event_clustering_is_deterministic():
    policies = [
        policy(id="policy-a", source_url="https://example.gov/policy"),
        policy(id="policy-b", source_url="https://example.gov/policy"),
    ]
    clusters = [build_policy_event_clusters(policies, KEYWORDS) for _ in range(10)]
    summaries = [build_deduped_theme_summary(policies, [AI_THEME], item, date(2026, 6, 22)) for item in clusters]

    assert all(item == clusters[0] for item in clusters)
    assert all(item == summaries[0] for item in summaries)


def test_empty_policy_event_input():
    clusters = build_policy_event_clusters([], KEYWORDS)
    event_summary = build_event_cluster_summary([], clusters)
    theme_summary = build_deduped_theme_summary([], [], clusters, date(2026, 6, 22))

    assert clusters == []
    assert event_summary["raw_policy_count"] == 0
    assert event_summary["cluster_count"] == 0
    assert event_summary["deduplication_ratio"] == 0.0
    assert theme_summary["themes"] == []
