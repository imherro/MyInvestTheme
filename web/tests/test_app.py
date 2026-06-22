import asyncio

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
    assert body["result"].get("theme_summary", {}).get("scoring_version") == "theme_score_v3_event_dedup"


def test_index_api_returns_homepage_content():
    response = get("/api/index")
    assert response.status_code == 200
    body = response.json()
    assert body["page"] == "index"
    assert body["latest_report"]["report_id"].startswith("mainline_review_")
    assert body["latest_report"]["basis_date"]
    assert body["theme_ranking"]
    assert body["event_cluster_summary"]["scoring_version"] == "policy_event_clustering_v2"
    assert body["theme_summary"]["scoring_version"] == "theme_score_v3_event_dedup"
    assert "theme_score_v3" in body["reports"][0]["top_themes"][0]
    assert "theme_score_v2_raw" in body["reports"][0]["top_themes"][0]
    assert "matched_event_cluster_count" in body["reports"][0]["top_themes"][0]
    assert "deduplication_effect" in body["reports"][0]["top_themes"][0]
    first_theme = body["theme_ranking"][0]
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
    first_point = next(item["points"][0] for item in series["themes"] if item["points"])
    assert "theme_score" in first_point
    assert "etf_score" in first_point
    assert "policy_score" in first_point
    assert "theme_score_v3" in first_point
    assert "theme_score_v2_raw" in first_point
    assert "deduplication_effect" in first_point
    assert "resonance_score" in first_point
    assert "triple_confirmation" in first_point


def test_pages_render():
    latest = get("/")
    assert latest.status_code == 200
    assert "A股主线研究台" in latest.text
    assert "证据项/拆解" in latest.text
    assert "资金排名" in latest.text
    assert "政策分" in latest.text

    reports = get("/reports")
    assert reports.status_code == 200
    assert "历次研究结果" in reports.text
