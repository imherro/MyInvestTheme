import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from theme_relevance import (
    MIN_RELEVANCE_THRESHOLD,
    build_theme_summary,
    compute_theme_contribution,
    compute_theme_relevance_v2,
    load_theme_config,
    sort_theme_summary_rows,
)


def theme_by_name(name: str) -> dict:
    return next(theme for theme in load_theme_config() if theme["theme_name"] == name)


def test_strong_ai_policy_scores_high_with_evidence():
    policy = {
        "title": "人工智能算力基础设施建设",
        "summary": "支持智能计算中心和数字经济发展。",
        "key_points": ["人工智能", "算力", "智能计算中心", "数字经济"],
        "beneficiary_chain": ["智能计算中心", "服务器", "光模块"],
        "related_industries": ["服务器", "光模块", "数据中心"],
    }

    result = compute_theme_relevance_v2(policy, theme_by_name("AI算力/通信"))

    assert result["relevance_score_v2"] > 0.5
    assert len(result["matched_evidence"]) > 0


def test_weak_generic_keyword_is_filtered_from_theme_summary():
    policy = {
        "id": "weak-digital-policy",
        "title": "数字化工作安排",
        "summary": "推进数字化。",
        "source": "省级政府",
        "published_date": "2026-06-01",
        "authority_level": "provincial",
        "economic_scope": "regional",
    }
    theme = theme_by_name("AI算力/通信")

    relevance = compute_theme_relevance_v2(policy, theme)
    summary = build_theme_summary([policy], [theme], date(2026, 6, 22))

    assert relevance["relevance_score_v2"] < MIN_RELEVANCE_THRESHOLD
    assert summary["themes"][0]["matched_policy_count"] == 0


def test_beneficiary_chain_boosts_ai_theme():
    policy = {
        "title": "算力基础设施支持政策",
        "beneficiary_chain": ["智能计算中心", "服务器", "光模块"],
        "related_industries": ["数据中心"],
    }

    result = compute_theme_relevance_v2(policy, theme_by_name("AI算力/通信"))

    assert result["beneficiary_score"] > 0.5


def test_negative_policy_discounts_relevance():
    policy = {
        "title": "人工智能算力整治风险提示",
        "summary": "针对算力建设风险提示和整治要求。",
        "key_points": ["人工智能", "算力", "整治", "风险提示"],
        "beneficiary_chain": ["算力基础设施"],
    }

    result = compute_theme_relevance_v2(policy, theme_by_name("AI算力/通信"))

    assert result["negative_filter_score"] < 1.0
    assert result["relevance_score_v2"] < result["base_relevance"]


def test_theme_relevance_is_deterministic():
    policy = {
        "title": "人工智能算力基础设施建设",
        "summary": "支持智能计算中心和数字经济发展。",
        "beneficiary_chain": ["智能计算中心", "服务器", "光模块"],
    }
    theme = theme_by_name("AI算力/通信")

    results = [compute_theme_relevance_v2(policy, theme) for _ in range(10)]

    assert all(result == results[0] for result in results)


def test_missing_policy_fields_do_not_crash():
    result = compute_theme_relevance_v2({"title": "政策"}, theme_by_name("AI算力/通信"))

    assert set(result) >= {
        "relevance_score_v2",
        "keyword_score",
        "beneficiary_score",
        "policy_objective_score",
        "negative_filter_score",
        "matched_evidence",
    }


def test_theme_contribution_formula():
    assert compute_theme_contribution(0.8, 0.5) == 0.4


def test_theme_summary_sorting_is_deterministic():
    rows = [
        {
            "theme_id": "theme_a",
            "theme_score_v2": 1.0,
            "matched_policy_count": 2,
            "avg_relevance_score_v2": 0.8,
        },
        {
            "theme_id": "theme_b",
            "theme_score_v2": 1.0,
            "matched_policy_count": 3,
            "avg_relevance_score_v2": 0.7,
        },
    ]

    assert sort_theme_summary_rows(rows)[0]["theme_id"] == "theme_b"


def test_empty_theme_config_returns_empty_summary():
    summary = build_theme_summary([], [], date(2026, 6, 22))
    assert summary["themes"] == []
