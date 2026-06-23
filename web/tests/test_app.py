import asyncio
import re

import httpx

from web.main import app


def get(path: str) -> httpx.Response:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path)

    return asyncio.run(run())


def test_health_reports_latest_available():
    response = get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["read_only"] is True
    assert body["report_count"] >= 1
    assert body["latest_report_id"]


def test_latest_report_contract():
    response = get("/api/latest")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"report_id", "result"}
    assert body["report_id"].startswith("mainline_review_")
    assert body["result"]["theme_ranking"]
    assert body["result"].get("event_cluster_summary", {}).get("scoring_version") == "policy_event_clustering_v2"
    assert body["result"].get("policy_stance_summary", {}).get("scoring_version") == "policy_theme_stance_v2"
    assert body["result"].get("event_theme_allocation_summary", {}).get("scoring_version") == "event_theme_allocation_v2"
    assert body["result"].get("mainline_lifecycle_summary", {}).get("scoring_version") == "mainline_lifecycle_v2"
    assert body["result"].get("theme_summary", {}).get("scoring_version") == "mainline_score_v6_lifecycle_adjusted"
    assert body["result"].get("canonical_mainline_summary", {}).get("scoring_version") == "canonical_mainline_output_v2"
    assert body["result"].get("canonical_mainline_summary", {}).get("default_score_field") == "mainline_score_v6"
    assert body["result"].get("mainline_ranking", [])[0]["mainline_score_v6"] is not None
    assert body["result"].get("mainline_ranking", [])[0]["lifecycle_state"]
    assert body["result"].get("mainline_ranking", [])[0]["lifecycle_state_label"]
    assert body["result"].get("mainline_ranking", [])[0]["cycle_stage_label"]
    assert "cycle_review_remaining_days" in body["result"].get("mainline_ranking", [])[0]
    assert body["result"].get("mainline_ranking", [])[0]["supporting_events"]
    assert body["result"].get("mainline_ranking", [])[0]["supporting_events"][0]["title"]
    assert body["result"].get("mainline_cycle_stage_summary", {}).get("scoring_version") == "mainline_cycle_stage_v2"


def test_index_api_returns_homepage_content():
    response = get("/api/index")
    assert response.status_code == 200
    body = response.json()
    assert body["page"] == "index"
    assert body["latest_report"]["report_id"].startswith("mainline_review_")
    assert body["latest_report"]["basis_date"]
    assert body["latest_report"]["theme_scoring_version"] == "mainline_score_v6_lifecycle_adjusted"
    assert body["latest_report"]["mainline_lifecycle_version"] == "mainline_lifecycle_v2"
    assert body["latest_report"]["canonical_mainline_version"] == "canonical_mainline_output_v2"
    assert body["latest_report"]["default_score_field"] == "mainline_score_v6"
    assert body["latest_report"]["top_mainline_theme"]
    assert body["latest_report"]["top_theme"] == body["latest_report"]["top_mainline_theme"]
    assert body["latest_report"]["top_mainline_score"] is not None
    assert body["latest_report"]["top_mainline_theme_v6"] == body["latest_report"]["top_mainline_theme"]
    assert body["latest_report"]["top_mainline_score_v6"] == body["latest_report"]["top_mainline_score"]
    assert body["latest_report"]["top_mainline_lifecycle_state"]
    assert body["latest_report"]["top_mainline_lifecycle_state_label"]
    assert body["latest_report"]["top_mainline_cycle_stage"]
    assert body["latest_report"]["top_mainline_cycle_time_window"]
    assert "top_mainline_cycle_review_remaining_days" in body["latest_report"]
    assert body["mainline_ranking"]
    assert body["canonical_mainline_summary"]["scoring_version"] == "canonical_mainline_output_v2"
    assert body["legacy_theme_ranking"]
    assert body["theme_ranking"]
    assert body["event_cluster_summary"]["scoring_version"] == "policy_event_clustering_v2"
    assert body["policy_stance_summary"]["scoring_version"] == "policy_theme_stance_v2"
    assert body["event_theme_allocation_summary"]["scoring_version"] == "event_theme_allocation_v2"
    assert body["mainline_lifecycle_summary"]["scoring_version"] == "mainline_lifecycle_v2"
    assert body["theme_summary"]["scoring_version"] == "mainline_score_v6_lifecycle_adjusted"
    assert "mainline_score_v6" in body["reports"][0]["top_themes"][0]
    assert "lifecycle_state_label" in body["reports"][0]["top_themes"][0]
    assert "cycle_stage_label" in body["reports"][0]["top_themes"][0]
    assert "cycle_time_window" in body["reports"][0]["top_themes"][0]
    assert "cycle_review_remaining_days" in body["reports"][0]["top_themes"][0]
    assert "supporting_events" in body["reports"][0]["top_themes"][0]
    assert "theme_score_v5" in body["reports"][0]["top_themes"][0]
    assert "theme_score_v4_stance_adjusted" in body["reports"][0]["top_themes"][0]
    assert "theme_score_v4" in body["reports"][0]["top_themes"][0]
    assert "theme_score_v3_dedup" in body["reports"][0]["top_themes"][0]
    assert "theme_score_v3" in body["reports"][0]["top_themes"][0]
    assert "theme_score_v2_raw" in body["reports"][0]["top_themes"][0]
    assert "allocation_adjustment_effect" in body["reports"][0]["top_themes"][0]
    assert "matched_event_cluster_count" in body["reports"][0]["top_themes"][0]
    assert "matched_allocated_event_count" in body["reports"][0]["top_themes"][0]
    assert "deduplication_effect" in body["reports"][0]["top_themes"][0]
    assert "stance_adjustment_effect" in body["reports"][0]["top_themes"][0]
    assert "lifecycle_state" in body["reports"][0]["top_themes"][0]
    assert "lifecycle_quality_multiplier" in body["reports"][0]["top_themes"][0]
    assert "score_30d" in body["reports"][0]["top_themes"][0]
    assert "score_90d" in body["reports"][0]["top_themes"][0]
    first_mainline = body["mainline_ranking"][0]
    assert first_mainline["theme_name"] == body["latest_report"]["top_mainline_theme"]
    assert first_mainline["cycle_stage_label"] == body["latest_report"]["top_mainline_cycle_stage"]
    assert first_mainline["cycle_review_remaining_days"] == body["latest_report"]["top_mainline_cycle_review_remaining_days"]
    assert first_mainline["supporting_events"]
    assert first_mainline["supporting_events"][0]["title"]
    assert first_mainline["supporting_events"][0]["url"]
    first_theme = body["legacy_theme_ranking"][0]
    assert "evidence_breakdown" in first_theme
    labels = {item["label"] for item in first_theme["evidence_breakdown"]}
    assert {
        "申万行业",
        "同花顺主题",
        "ETF代理",
        "涨停结构",
        "资金排名",
    }.issubset(labels)
    if "policy_score" in first_theme:
        assert "政策信号" in labels
        assert "policy_score" in first_theme
    assert body["market"]["breadth"]
    assert body["market"]["broad_indexes"]
    assert body["score_series"]["report_count"] >= 1
    assert body["reports"]
    assert "A股主线研究报告" in body["markdown"]


def test_reports_and_score_series():
    reports_response = get("/api/reports")
    assert reports_response.status_code == 200
    reports = reports_response.json()["reports"]
    assert reports

    report_id = reports[0]["report_id"]
    report_response = get(f"/api/reports/{report_id}")
    assert report_response.status_code == 200
    assert report_response.json()["result"]["theme_ranking"]

    markdown_response = get(f"/api/reports/{report_id}/markdown")
    assert markdown_response.status_code == 200
    assert "A股主线研究报告" in markdown_response.text

    series_response = get("/api/score-series")
    assert series_response.status_code == 200
    series = series_response.json()
    assert series["report_count"] >= 1
    assert any(item["points"] for item in series["themes"])
    first_theme_points = next(item["points"] for item in series["themes"] if item["points"])
    first_point = first_theme_points[0]
    assert all(point["x"] != point["basis_date"] for point in first_theme_points)
    assert len({point["x"] for point in first_theme_points}) == len(first_theme_points)
    assert "theme_score" in first_point
    assert "etf_score" in first_point
    assert "policy_score" in first_point
    assert "default_score" in first_point
    assert "default_score_field" in first_point
    assert "legacy_evidence_score" in first_point
    assert "legacy_market_score" in first_point
    assert "legacy_policy_score" in first_point
    assert first_point["default_score_field"] == "mainline_score_v6"
    assert first_point["score"] == first_point["mainline_score_v6"]
    assert "mainline_score_v6" in first_point
    assert "theme_score_v5" in first_point
    assert "theme_score_v4_stance_adjusted" in first_point
    assert "theme_score_v4" in first_point
    assert "theme_score_v3_dedup" in first_point
    assert "theme_score_v3" in first_point
    assert "theme_score_v2_raw" in first_point
    assert "allocation_adjustment_effect" in first_point
    assert "deduplication_effect" in first_point
    assert "stance_adjustment_effect" in first_point
    assert "lifecycle_state" in first_point
    assert "lifecycle_state_label" in first_point
    assert "lifecycle_quality_multiplier" in first_point
    assert "cycle_stage" in first_point
    assert "cycle_stage_label" in first_point
    assert "cycle_time_window" in first_point
    assert "cycle_review_remaining_days" in first_point
    assert "score_30d" in first_point
    assert "score_90d" in first_point
    assert "resonance_score" in first_point
    assert "triple_confirmation" in first_point


def test_pages_render():
    latest = get("/")
    assert latest.status_code == 200
    assert "A股主线研究台" in latest.text
    assert "政策主线分 vs 市场热度观察分" in latest.text
    assert "上方看当前强弱对比" in latest.text
    assert "下方分两张图看不同主题的历史强弱变化" in latest.text
    assert "点大小/外圈" not in latest.text
    assert "mainline_score_v6" in latest.text
    assert "主线分" in latest.text
    assert "主题分" in latest.text
    assert "政策贡献" in latest.text
    assert "政策依据" in latest.text
    assert "support-details" in latest.text
    assert "周期阶段" in latest.text
    assert "周期阶段优先级" in latest.text
    assert "距90天复核" in latest.text
    assert "启动确认期" in latest.text or "主升扩散期" in latest.text or "政策孕育期" in latest.text
    assert "证据项/拆解" in latest.text
    assert "资金排名" in latest.text
    assert "政策分" in latest.text
    assert "生命周期优先级" in latest.text
    assert "热度阶段优先级" in latest.text
    assert "升温加速" in latest.text
    assert "持续有效" in latest.text
    assert "政策主线靠前且市场热度靠前" in latest.text
    assert latest.text.count('class="hint"') == 2
    reports = get("/reports")
    assert reports.status_code == 200
    assert "历次研究结果" in reports.text

    app_js = get("/static/app.js")
    assert app_js.status_code == 200
    assert "最新强弱对比" in app_js.text
    assert "时间走势" in app_js.text
    assert "政策主线分历史变化" in app_js.text
    assert "市场热度观察分历史变化" in app_js.text
    assert "颜色=主题" in app_js.text
    assert "点大小/外圈" not in app_js.text


def test_index_table_column_contract():
    latest = get("/")
    assert latest.status_code == 200
    tables = re.findall(r"<table(?:\s+[^>]*)?>.*?</table>", latest.text, flags=re.S)
    assert len(tables) >= 2
    assert "event_" not in tables[0]
    assert "主要支撑事件" not in tables[0]
    for table in tables[:2]:
        assert table.count("<col ") == table.count("<th>")
