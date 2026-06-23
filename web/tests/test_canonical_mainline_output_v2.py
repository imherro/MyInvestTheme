import asyncio
import sys
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from canonical_mainline import (
    assert_canonical_mainline_contract,
    build_canonical_mainline_summary,
    build_legacy_theme_ranking,
    build_mainline_ranking,
)
from generate_mainline_report import render_markdown
from web.main import app


def get(path: str) -> httpx.Response:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path)

    return asyncio.run(run())


def theme(theme_id, name, score, state="sustained", theme_score_v5=1.0):
    return {
        "theme_id": theme_id,
        "theme_name": name,
        "mainline_score_v6": score,
        "theme_score_v5": theme_score_v5,
        "theme_score_v4_stance_adjusted": 0.9,
        "theme_score_v3_dedup": 0.95,
        "theme_score_v2_raw": 0.95,
        "lifecycle_state": state,
        "lifecycle_quality_multiplier": 0.9,
        "state_multiplier": 0.95,
        "breadth_score": 0.7,
        "score_30d": 0.3,
        "score_90d": 0.6,
        "event_count_30d": 1,
        "event_count_90d": 2,
        "source_org_count_90d": 2,
        "matched_allocated_event_count": 2,
        "primary_event_count": 1,
        "co_primary_event_count": 0,
        "secondary_event_count": 1,
        "peripheral_event_count": 0,
        "avg_allocation_share": 0.5,
        "deduplication_effect": 0.0,
        "stance_adjustment_effect": 0.1,
        "allocation_adjustment_effect": 0.2,
        "all_event_contributors": [
            {"event_cluster_id": f"event-{theme_id}", "age_days": 44, "allocated_cluster_contribution": score},
            {"event_cluster_id": f"event-{theme_id}-recent", "age_days": 5, "allocated_cluster_contribution": 0.2},
        ],
        "top_event_contributors": [
            {"event_cluster_id": f"event-{theme_id}", "age_days": 44, "allocated_cluster_contribution": score}
        ],
    }


def theme_summary(themes):
    return {
        "scoring_version": "mainline_score_v6_lifecycle_adjusted",
        "mainline_lifecycle_version": "mainline_lifecycle_v2",
        "themes": themes,
    }


def legacy_row(name, score):
    return {
        "theme": name,
        "stage": "主线确认",
        "evidence_score": score,
        "market_score": score,
        "policy_score": 10.0,
        "evidence_count": 5,
        "policy_evidence_count": 1,
        "top_sw": "电子",
        "top_ths": "半导体",
        "top_etf": "ETF",
        "top_policy": "policy",
    }


def markdown_payload():
    summary = theme_summary(
        [
            theme("ai", "AI算力/通信", 0.526, "sustained", 0.5983),
            theme("semi", "硬科技电子/半导体", 0.4966, "accelerating", 0.5137),
        ]
    )
    mainline = build_mainline_ranking(summary)
    canonical = build_canonical_mainline_summary(summary)
    legacy = build_legacy_theme_ranking([legacy_row("硬科技电子/半导体", 83.15), legacy_row("AI算力/通信", 80.83)])
    return {
        "generated_at": "2026-06-22 16:00:00 CST",
        "basis_date": "2026-06-18",
        "nominal_today": "2026-06-22",
        "data_sources_root": "",
        "completeness": {"daily_rows": 1, "daily_basic_rows": 1},
        "breadth": {
            "up_ratio": 36.0,
            "median_pct_chg": -0.7,
            "r5_positive_ratio": 55.0,
            "r20_positive_ratio": 21.0,
            "gt_5_count": 1,
            "lt_minus_5_count": 1,
        },
        "broad_indexes": [],
        "policy_summary": {"policy_weight": 0.15, "signals_count": 1, "min_relevance_threshold": 0.25},
        "event_cluster_summary": {},
        "policy_stance_summary": {},
        "event_theme_allocation_summary": {},
        "mainline_lifecycle_summary": {"scoring_version": "mainline_lifecycle_v2"},
        "canonical_mainline_summary": canonical,
        "mainline_ranking": mainline,
        "theme_summary": summary,
        "theme_ranking": legacy,
        "legacy_theme_ranking": legacy,
        "sw_top": [],
        "ths_top": [],
        "etf_top": [],
        "limit_up_top": [],
        "moneyflow_top": [],
        "baostock_check": [],
    }


def section(markdown, title):
    start = markdown.index(title)
    rest = markdown[start + len(title) :]
    marker = rest.find("\n## ")
    return rest if marker == -1 else rest[:marker]


def test_canonical_mainline_uses_mainline_score_v6_sorting():
    rows = build_mainline_ranking(theme_summary([theme("a", "A", 0.8), theme("b", "B", 1.0)]))
    assert rows[0]["theme_id"] == "b"


def test_mainline_ranking_top_matches_sorted_theme_summary_top():
    summary = theme_summary([theme("b", "B", 1.0), theme("a", "A", 0.8)])
    report = {
        "theme_summary": summary,
        "mainline_ranking": build_mainline_ranking(summary),
        "canonical_mainline_summary": build_canonical_mainline_summary(summary),
        "theme_ranking": [legacy_row("B", 90.0)],
        "legacy_theme_ranking": build_legacy_theme_ranking([legacy_row("B", 90.0)]),
    }
    assert report["mainline_ranking"][0]["theme_id"] == summary["themes"][0]["theme_id"]
    assert assert_canonical_mainline_contract(report) == []
    broken = dict(report)
    broken["theme_summary"] = theme_summary([theme("a", "A", 0.8), theme("b", "B", 1.0)])
    assert "mainline_ranking_top_not_equal_theme_summary_top" in assert_canonical_mainline_contract(broken)


def test_canonical_summary_top_mainline_is_correct():
    summary = theme_summary([theme("ai", "AI算力/通信", 0.526)])
    canonical = build_canonical_mainline_summary(summary)
    assert canonical["top_mainline"]["theme_name"] == "AI算力/通信"
    assert canonical["default_score_field"] == "mainline_score_v6"
    assert canonical["top_mainline"]["lifecycle_state"] == "sustained"
    assert canonical["top_mainline"]["lifecycle_state_label"] == "持续有效"
    assert canonical["mainline_cycle_stage_version"] == "mainline_cycle_stage_v2"
    assert canonical["top_mainline"]["cycle_stage_label"]
    assert canonical["top_mainline"]["cycle_elapsed_days"] == 44
    assert canonical["top_mainline"]["cycle_review_remaining_days"] == 46
    assert "距90天复核约46天" in canonical["top_mainline"]["cycle_time_window"]


def test_markdown_conclusion_does_not_use_legacy_evidence_top():
    markdown = render_markdown(markdown_payload())
    conclusion = section(markdown, "## 一句话结论")
    assert "AI算力/通信" in conclusion
    assert "综合证据分83.15" not in conclusion


def test_markdown_mainline_table_uses_v6_fields():
    markdown = render_markdown(markdown_payload())
    mainline = section(markdown, "## 政策主线")
    assert "mainline_score_v6" in mainline
    assert "生命周期" in mainline
    assert "周期阶段" in mainline
    assert "theme_score_v5" in mainline
    assert "30日分数" in mainline
    assert "90日分数" in mainline
    assert "| 主题 | 阶段 | 证据分 | 市场分 | 政策分 |" not in mainline


def test_api_latest_exposes_canonical_mainline_summary():
    body = get("/api/latest").json()["result"]
    assert body["canonical_mainline_summary"]["scoring_version"] == "canonical_mainline_output_v2"
    assert body["canonical_mainline_summary"]["default_score_field"] == "mainline_score_v6"
    assert body["mainline_ranking"][0]["mainline_score_v6"] is not None
    assert body["mainline_ranking"][0]["lifecycle_state"]
    assert body["mainline_ranking"][0]["lifecycle_state_label"]
    assert body["mainline_ranking"][0]["cycle_stage_label"]
    assert "cycle_review_remaining_days" in body["mainline_ranking"][0]
    assert body["mainline_cycle_stage_summary"]["scoring_version"] == "mainline_cycle_stage_v2"


def test_api_index_default_top_mainline_uses_v6():
    payload = get("/api/index").json()
    assert payload["latest_report"]["top_mainline_theme"] == payload["mainline_ranking"][0]["theme_name"]
    assert payload["latest_report"]["top_theme"] == payload["latest_report"]["top_mainline_theme"]
    assert payload["latest_report"]["default_score_field"] == "mainline_score_v6"
    assert payload["latest_report"]["top_mainline_cycle_stage"]
    assert "top_mainline_cycle_review_remaining_days" in payload["latest_report"]


def test_score_series_uses_mainline_score_as_default_score():
    payload = get("/api/score-series").json()
    points = [point for theme in payload["themes"] for point in theme["points"]]
    assert points
    for point in points:
        assert point["default_score_field"] == "mainline_score_v6"
        assert point["score"] == point["mainline_score_v6"]
        assert point["default_score"] == point["mainline_score_v6"]
        assert "legacy_evidence_score" in point
        assert "legacy_market_score" in point
        assert "legacy_policy_score" in point
        assert "lifecycle_state_label" in point
        assert "cycle_stage_label" in point
        assert "cycle_review_remaining_days" in point


def test_api_reports_summary_uses_canonical_top():
    reports = get("/api/reports").json()["reports"]
    latest = reports[0]
    assert latest["top_theme"] == latest["top_mainline_theme"]
    assert latest["top_score"] == latest["top_mainline_score"]
    assert latest["default_score_field"] == "mainline_score_v6"
    assert latest["canonical_mainline_version"] == "canonical_mainline_output_v2"


def test_readme_uses_canonical_mainline_wording():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Mainline score is `market_score * 85% + policy_score * 15%`" not in readme
    assert "mainline_score_v6" in readme
    assert "canonical mainline" in readme.lower()


def test_no_prohibited_api_routes_added():
    route_paths = {getattr(route, "path", "") for route in app.routes}
    prohibited = ("order", "position", "account", "trade", "portfolio", "backtest")
    assert not any(term in path.lower() for path in route_paths for term in prohibited)


def test_canonical_output_is_deterministic():
    summary = theme_summary([theme("b", "B", 1.0), theme("a", "A", 0.8)])
    outputs = [(build_mainline_ranking(summary), build_canonical_mainline_summary(summary)) for _ in range(10)]
    assert all(item == outputs[0] for item in outputs)


def test_empty_canonical_input():
    summary = theme_summary([])
    assert build_mainline_ranking(summary) == []
    canonical = build_canonical_mainline_summary(summary)
    assert canonical["theme_count"] == 0
    assert canonical["top_mainline"] == {}
