from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request


ROOT_DIR = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT_DIR / "research" / "mainline"
REPORT_ID_RE = re.compile(r"^mainline_review_\d{4}-\d{2}-\d{2}_\d{6}$")

app = FastAPI(
    title="A股主线研究台",
    version="1.0.0",
    description="Read-only browser and API for A-share mainline research results.",
)

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")


def _json_files() -> list[Path]:
    if not REPORT_DIR.exists():
        return []
    return sorted(REPORT_DIR.glob("mainline_review_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"研究JSON无法解析: {path.name}") from exc


def _safe_report_path(report_id: str, suffix: str) -> Path:
    if not REPORT_ID_RE.match(report_id):
        raise HTTPException(status_code=404, detail="研究报告不存在")
    path = REPORT_DIR / f"{report_id}{suffix}"
    if not path.exists() or path.parent != REPORT_DIR:
        raise HTTPException(status_code=404, detail="研究报告不存在")
    return path


def _report_id(path: Path) -> str:
    return path.stem


def _latest_json_path() -> Path:
    files = _json_files()
    if not files:
        raise HTTPException(status_code=404, detail="还没有可读取的主线研究结果")
    return files[0]


def _parse_generated_at(value: str | None) -> str:
    if not value:
        return ""
    return value.replace(" CST", "")


def _report_summary(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    themes = payload.get("theme_ranking") or []
    top_theme = themes[0] if themes else {}
    md_path = path.with_suffix(".md")
    return {
        "report_id": _report_id(path),
        "generated_at": payload.get("generated_at", ""),
        "basis_date": payload.get("basis_date", ""),
        "theme_count": len(themes),
        "top_theme": top_theme.get("theme", ""),
        "top_stage": top_theme.get("stage", ""),
        "top_score": top_theme.get("evidence_score"),
        "has_markdown": md_path.exists(),
    }


def list_reports() -> list[dict[str, Any]]:
    return [_report_summary(path) for path in _json_files()]


def load_report(report_id: str) -> dict[str, Any]:
    path = _safe_report_path(report_id, ".json")
    return _load_json(path)


def load_markdown(report_id: str) -> str:
    path = _safe_report_path(report_id, ".md")
    return path.read_text(encoding="utf-8")


def load_latest_report() -> tuple[str, dict[str, Any], str]:
    path = _latest_json_path()
    report_id = _report_id(path)
    markdown = path.with_suffix(".md").read_text(encoding="utf-8") if path.with_suffix(".md").exists() else ""
    return report_id, _load_json(path), markdown


def build_score_series() -> dict[str, Any]:
    points_by_theme: dict[str, list[dict[str, Any]]] = {}
    reports = list_reports()
    for summary in reversed(reports):
        payload = load_report(summary["report_id"])
        x = payload.get("basis_date") or _parse_generated_at(payload.get("generated_at"))
        for rank, item in enumerate(payload.get("theme_ranking") or [], start=1):
            theme = item.get("theme", "")
            if not theme:
                continue
            points_by_theme.setdefault(theme, []).append(
                {
                    "x": x,
                    "basis_date": payload.get("basis_date", ""),
                    "generated_at": payload.get("generated_at", ""),
                    "score": item.get("evidence_score"),
                    "stage": item.get("stage", ""),
                    "rank": rank,
                    "report_id": summary["report_id"],
                }
            )
    return {
        "themes": [{"theme": theme, "points": points} for theme, points in sorted(points_by_theme.items())],
        "report_count": len(reports),
    }


@app.get("/", response_class=HTMLResponse)
def latest_page(request: Request) -> HTMLResponse:
    report_id, payload, markdown = load_latest_report()
    reports = list_reports()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "report_id": report_id,
            "report": payload,
            "markdown": markdown,
            "reports": reports,
            "page": "latest",
        },
    )


@app.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "reports.html", {"reports": list_reports(), "page": "reports"})


@app.get("/reports/{report_id}", response_class=HTMLResponse)
def report_page(request: Request, report_id: str) -> HTMLResponse:
    payload = load_report(report_id)
    markdown = load_markdown(report_id) if (REPORT_DIR / f"{report_id}.md").exists() else ""
    return templates.TemplateResponse(
        request,
        "report.html",
        {"report_id": report_id, "report": payload, "markdown": markdown, "page": "reports"},
    )


@app.get("/api/health")
def health() -> dict[str, Any]:
    reports = list_reports()
    return {
        "ok": True,
        "read_only": True,
        "report_count": len(reports),
        "latest_report_id": reports[0]["report_id"] if reports else None,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }


@app.get("/api/reports")
def api_reports() -> dict[str, Any]:
    return {"reports": list_reports()}


@app.get("/api/reports/{report_id}")
def api_report(report_id: str) -> dict[str, Any]:
    return {"report_id": report_id, "result": load_report(report_id)}


@app.get("/api/reports/{report_id}/markdown")
def api_report_markdown(report_id: str) -> Response:
    return PlainTextResponse(load_markdown(report_id), media_type="text/markdown; charset=utf-8")


@app.get("/api/score-series")
def api_score_series() -> dict[str, Any]:
    return build_score_series()


@app.get("/api/latest")
@app.get("/api/mainline/latest")
def api_latest() -> dict[str, Any]:
    report_id, payload, _ = load_latest_report()
    return {"report_id": report_id, "result": payload}
