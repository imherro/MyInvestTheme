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


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resonance_score(item: dict[str, Any]) -> float | None:
    scores = [
        _float_or_none(item.get("evidence_score")),
        _float_or_none(item.get("ths_score")),
        _float_or_none(item.get("etf_score")),
    ]
    valid_scores = [score for score in scores if score is not None]
    if not valid_scores:
        return None
    return sum(valid_scores) / len(valid_scores)


def _moneyflow_score(item: dict[str, Any]) -> float | None:
    flow_rank = _float_or_none(item.get("flow_rank"))
    if flow_rank is None:
        return None
    return flow_rank * 100


def _moneyflow_note(item: dict[str, Any]) -> str:
    large_net = _float_or_none(item.get("large_net"))
    if large_net is None:
        return "大单/特大单净额无数据"
    return f"大单/特大单净额 {large_net:.2f}"


def _policy_note(item: dict[str, Any]) -> str:
    top_policy = item.get("top_policy")
    if top_policy:
        return str(top_policy)
    return "无政策映射"


def _evidence_breakdown(item: dict[str, Any]) -> list[dict[str, Any]]:
    limit_count = int(_float_or_none(item.get("limit_count")) or 0)
    large_net = _float_or_none(item.get("large_net")) or 0
    rows = [
        {
            "label": "申万行业",
            "score": _float_or_none(item.get("sw_score")),
            "active": (_float_or_none(item.get("sw_score")) or 0) > 0,
            "note": item.get("top_sw") or "无映射",
        },
        {
            "label": "同花顺主题",
            "score": _float_or_none(item.get("ths_score")),
            "active": (_float_or_none(item.get("ths_score")) or 0) > 0,
            "note": item.get("top_ths") or "无主题匹配",
        },
        {
            "label": "ETF代理",
            "score": _float_or_none(item.get("etf_score")),
            "active": (_float_or_none(item.get("etf_score")) or 0) > 0,
            "note": item.get("top_etf") or "无ETF匹配",
        },
        {
            "label": "涨停结构",
            "score": _float_or_none(item.get("limit_score")),
            "active": limit_count > 0,
            "note": f"匹配涨停 {limit_count} 个",
        },
        {
            "label": "资金排名",
            "score": _moneyflow_score(item),
            "active": large_net > 0,
            "note": _moneyflow_note(item),
        },
    ]
    if "policy_score" in item or item.get("top_policy"):
        rows.append(
            {
                "label": "政策信号",
                "score": _float_or_none(item.get("policy_score")),
                "active": int(_float_or_none(item.get("policy_evidence_count")) or 0) > 0,
                "note": _policy_note(item),
            }
        )
    return rows


def enrich_theme_ranking(themes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched = []
    for item in themes:
        enriched_item = dict(item)
        enriched_item["evidence_breakdown"] = _evidence_breakdown(item)
        enriched_item["evidence_total"] = len(enriched_item["evidence_breakdown"])
        enriched.append(enriched_item)
    return enriched


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
                    "evidence_score": item.get("evidence_score"),
                    "theme_score": item.get("ths_score"),
                    "etf_score": item.get("etf_score"),
                    "industry_score": item.get("sw_score"),
                    "market_score": item.get("market_score"),
                    "policy_score": item.get("policy_score"),
                    "theme_score_v2": item.get("theme_score_v2"),
                    "matched_policy_count": item.get("matched_policy_count"),
                    "avg_relevance_score_v2": item.get("avg_relevance_score_v2"),
                    "resonance_score": _resonance_score(item),
                    "triple_confirmation": all(
                        (_float_or_none(item.get(key)) or 0) >= 75
                        for key in ("evidence_score", "ths_score", "etf_score")
                    ),
                    "stage": item.get("stage", ""),
                    "rank": rank,
                    "report_id": summary["report_id"],
                }
            )
    return {
        "themes": [{"theme": theme, "points": points} for theme, points in sorted(points_by_theme.items())],
        "report_count": len(reports),
    }


def build_index_payload(report_id: str, payload: dict[str, Any], markdown: str) -> dict[str, Any]:
    themes = enrich_theme_ranking(payload.get("theme_ranking") or [])
    top_theme = themes[0] if themes else {}
    breadth = payload.get("breadth") or {}
    return {
        "page": "index",
        "latest_report": {
            "report_id": report_id,
            "basis_date": payload.get("basis_date", ""),
            "generated_at": payload.get("generated_at", ""),
            "theme_count": len(themes),
            "top_theme": top_theme.get("theme", ""),
            "top_stage": top_theme.get("stage", ""),
            "top_score": top_theme.get("evidence_score"),
            "up_ratio": breadth.get("up_ratio"),
        },
        "theme_ranking": themes,
        "theme_summary": payload.get("theme_summary") or {},
        "market": {
            "breadth": breadth,
            "broad_indexes": payload.get("broad_indexes") or [],
        },
        "score_series": build_score_series(),
        "reports": list_reports(),
        "markdown": markdown,
    }


@app.get("/", response_class=HTMLResponse)
def latest_page(request: Request) -> HTMLResponse:
    report_id, payload, markdown = load_latest_report()
    page_report = dict(payload)
    page_report["theme_ranking"] = enrich_theme_ranking(payload.get("theme_ranking") or [])
    reports = list_reports()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "report_id": report_id,
            "report": page_report,
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


@app.get("/api/index")
def api_index() -> dict[str, Any]:
    report_id, payload, markdown = load_latest_report()
    return build_index_payload(report_id, payload, markdown)


@app.get("/api/latest")
@app.get("/api/mainline/latest")
def api_latest() -> dict[str, Any]:
    report_id, payload, _ = load_latest_report()
    return {"report_id": report_id, "result": payload}
