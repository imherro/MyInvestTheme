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


def test_latest_shadow_payload_contract():
    response = get("/api/shadow-account/latest")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "mainline_latest_for_shadow_account.v1"
    assert body["constraints"]["read_only"] is True
    assert body["constraints"]["ratio_only"] is True
    assert body["constraints"]["contains_trade_orders"] is False
    assert body["theme_signals"]
    assert "latest_result" in body


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


def test_pages_render():
    latest = get("/")
    assert latest.status_code == 200
    assert "A股主线研究台" in latest.text

    reports = get("/reports")
    assert reports.status_code == 200
    assert "历次研究结果" in reports.text
