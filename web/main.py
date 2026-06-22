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

from scripts.canonical_mainline import (
    DEFAULT_SCORE_FIELD,
    SCORING_VERSION as CANONICAL_MAINLINE_VERSION,
    build_canonical_mainline_summary,
    build_legacy_theme_ranking,
    build_mainline_ranking,
)


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


def _mainline_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("mainline_ranking") or []
    if rows:
        return rows
    theme_summary = payload.get("theme_summary") or {}
    if theme_summary.get("themes"):
        return build_mainline_ranking(theme_summary)
    return []


def _canonical_summary(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("canonical_mainline_summary") or {}
    if summary:
        return summary
    theme_summary = payload.get("theme_summary") or {}
    if theme_summary.get("themes"):
        return build_canonical_mainline_summary(theme_summary)
    return {
        "scoring_version": CANONICAL_MAINLINE_VERSION,
        "default_score_field": DEFAULT_SCORE_FIELD,
        "source_summary": "theme_summary",
        "source_scoring_version": "",
        "theme_count": 0,
        "top_mainline": {},
        "state_counts": {},
    }


def _legacy_theme_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("legacy_theme_ranking") or []
    if rows:
        return rows
    return build_legacy_theme_ranking(payload.get("theme_ranking") or [])


def _legacy_by_theme(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in _legacy_theme_rows(payload):
        for key in (row.get("theme_id"), row.get("theme"), row.get("theme_name")):
            if key:
                result[str(key)] = row
    return result


def _with_canonical_fields(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    theme_summary = result.get("theme_summary") or {}
    if theme_summary.get("themes"):
        result.setdefault("mainline_ranking", build_mainline_ranking(theme_summary))
        result.setdefault("canonical_mainline_summary", build_canonical_mainline_summary(theme_summary))
    if result.get("theme_ranking"):
        result.setdefault("legacy_theme_ranking", build_legacy_theme_ranking(result.get("theme_ranking") or []))
    return result


def _report_summary(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    mainline_rows = _mainline_rows(payload)
    legacy_rows = _legacy_theme_rows(payload)
    top_mainline = mainline_rows[0] if mainline_rows else {}
    legacy_top = legacy_rows[0] if legacy_rows else {}
    md_path = path.with_suffix(".md")
    event_cluster_summary = payload.get("event_cluster_summary") or {}
    theme_summary = payload.get("theme_summary") or {}
    policy_stance_summary = payload.get("policy_stance_summary") or {}
    event_theme_allocation_summary = payload.get("event_theme_allocation_summary") or {}
    mainline_lifecycle_summary = payload.get("mainline_lifecycle_summary") or {}
    canonical_mainline_summary = _canonical_summary(payload)
    return {
        "report_id": _report_id(path),
        "generated_at": payload.get("generated_at", ""),
        "basis_date": payload.get("basis_date", ""),
        "theme_count": len(mainline_rows) or len(legacy_rows),
        "top_theme": top_mainline.get("theme_name") or legacy_top.get("theme", ""),
        "top_stage": top_mainline.get("lifecycle_state") or legacy_top.get("stage", ""),
        "top_score": top_mainline.get("mainline_score_v6", legacy_top.get("evidence_score")),
        "top_mainline_theme": top_mainline.get("theme_name", ""),
        "top_mainline_score": top_mainline.get("mainline_score_v6"),
        "top_mainline_lifecycle_state": top_mainline.get("lifecycle_state", ""),
        "default_score_field": DEFAULT_SCORE_FIELD if mainline_rows else "legacy_evidence_score",
        "canonical_mainline_version": canonical_mainline_summary.get("scoring_version", "") if mainline_rows else "",
        "legacy_top_theme": legacy_top.get("theme", ""),
        "legacy_top_score": legacy_top.get("evidence_score"),
        "has_markdown": md_path.exists(),
        "canonical_mainline_summary": canonical_mainline_summary,
        "event_cluster_summary": {
            "raw_policy_count": event_cluster_summary.get("raw_policy_count", 0),
            "cluster_count": event_cluster_summary.get("cluster_count", 0),
            "deduplication_ratio": event_cluster_summary.get("deduplication_ratio", 0.0),
        },
        "policy_stance_summary": {
            "scoring_version": policy_stance_summary.get("scoring_version", ""),
            "cluster_theme_pair_count": policy_stance_summary.get("cluster_theme_pair_count", 0),
            "supportive_count": policy_stance_summary.get("supportive_count", 0),
            "neutral_or_mixed_count": policy_stance_summary.get("neutral_or_mixed_count", 0),
            "restrictive_count": policy_stance_summary.get("restrictive_count", 0),
        },
        "event_theme_allocation_summary": {
            "scoring_version": event_theme_allocation_summary.get("scoring_version", ""),
            "event_theme_claim_count": event_theme_allocation_summary.get("event_theme_claim_count", 0),
            "multi_theme_event_count": event_theme_allocation_summary.get("multi_theme_event_count", 0),
            "capped_event_count": event_theme_allocation_summary.get("capped_event_count", 0),
            "allocation_reduction_effect": event_theme_allocation_summary.get("allocation_reduction_effect", 0.0),
        },
        "mainline_lifecycle_summary": {
            "scoring_version": mainline_lifecycle_summary.get("scoring_version", ""),
            "accelerating_count": mainline_lifecycle_summary.get("accelerating_count", 0),
            "sustained_count": mainline_lifecycle_summary.get("sustained_count", 0),
            "emerging_count": mainline_lifecycle_summary.get("emerging_count", 0),
            "cooling_count": mainline_lifecycle_summary.get("cooling_count", 0),
        },
        "theme_summary": {
            "scoring_version": theme_summary.get("scoring_version", ""),
            "policy_stance_version": theme_summary.get("policy_stance_version", ""),
            "event_theme_allocation_version": theme_summary.get("event_theme_allocation_version", ""),
            "mainline_lifecycle_version": theme_summary.get("mainline_lifecycle_version", ""),
        },
        "top_themes": [
            {
                "theme_id": item.get("theme_id", ""),
                "theme_name": item.get("theme_name", ""),
                "theme": item.get("theme_name", ""),
                "mainline_score_v6": item.get("mainline_score_v6"),
                "theme_score_v5": item.get("theme_score_v5"),
                "theme_score_v4_stance_adjusted": item.get("theme_score_v4_stance_adjusted"),
                "theme_score_v4": item.get("theme_score_v4"),
                "theme_score_v3_dedup": item.get("theme_score_v3_dedup"),
                "theme_score_v3": item.get("theme_score_v3"),
                "theme_score_v2_raw": item.get("theme_score_v2_raw"),
                "allocation_adjustment_effect": item.get("allocation_adjustment_effect"),
                "matched_event_cluster_count": item.get("matched_event_cluster_count"),
                "matched_allocated_event_count": item.get("matched_allocated_event_count"),
                "matched_policy_count_raw": item.get("matched_policy_count_raw"),
                "deduplication_effect": item.get("deduplication_effect"),
                "stance_adjustment_effect": item.get("stance_adjustment_effect"),
                "primary_event_count": item.get("primary_event_count"),
                "lifecycle_state": item.get("lifecycle_state"),
                "lifecycle_quality_multiplier": item.get("lifecycle_quality_multiplier"),
                "score_30d": item.get("score_30d"),
                "score_90d": item.get("score_90d"),
                "event_count_30d": item.get("event_count_30d"),
                "event_count_90d": item.get("event_count_90d"),
                "source_org_count_90d": item.get("source_org_count_90d"),
                "supportive_cluster_count": item.get("supportive_cluster_count"),
                "restrictive_cluster_count": item.get("restrictive_cluster_count"),
            }
            for item in mainline_rows[:3]
        ],
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
        legacy_by_theme = _legacy_by_theme(payload)
        mainline_rows = _mainline_rows(payload)
        source_rows = mainline_rows or _legacy_theme_rows(payload)
        for rank, item in enumerate(source_rows, start=1):
            theme = item.get("theme_name") or item.get("theme", "")
            if not theme:
                continue
            legacy = (
                legacy_by_theme.get(str(item.get("theme_id") or ""))
                or legacy_by_theme.get(str(theme))
                or {}
            )
            mainline_score = _float_or_none(item.get("mainline_score_v6"))
            if mainline_score is None:
                continue
            default_score = mainline_score
            default_score_field = DEFAULT_SCORE_FIELD
            points_by_theme.setdefault(theme, []).append(
                {
                    "x": x,
                    "basis_date": payload.get("basis_date", ""),
                    "generated_at": payload.get("generated_at", ""),
                    "score": default_score,
                    "default_score": default_score,
                    "default_score_field": default_score_field,
                    "score_field": default_score_field,
                    "legacy_evidence_score": legacy.get("evidence_score") if legacy else item.get("evidence_score"),
                    "legacy_market_score": legacy.get("market_score") if legacy else item.get("market_score"),
                    "legacy_policy_score": legacy.get("policy_score") if legacy else item.get("policy_score"),
                    "evidence_score": legacy.get("evidence_score") if legacy else item.get("evidence_score"),
                    "theme_score": legacy.get("ths_score") if legacy else item.get("ths_score"),
                    "etf_score": legacy.get("etf_score") if legacy else item.get("etf_score"),
                    "industry_score": legacy.get("sw_score") if legacy else item.get("sw_score"),
                    "market_score": legacy.get("market_score") if legacy else item.get("market_score"),
                    "policy_score": legacy.get("policy_score") if legacy else item.get("policy_score"),
                    "mainline_score_v6": item.get("mainline_score_v6"),
                    "theme_score_v5": item.get("theme_score_v5"),
                    "theme_score_v4_stance_adjusted": item.get("theme_score_v4_stance_adjusted"),
                    "theme_score_v4": item.get("theme_score_v4"),
                    "theme_score_v3_dedup": item.get("theme_score_v3_dedup"),
                    "theme_score_v3": item.get("theme_score_v3"),
                    "theme_score_v2_raw": item.get("theme_score_v2_raw"),
                    "allocation_adjustment_effect": item.get("allocation_adjustment_effect"),
                    "matched_event_cluster_count": item.get("matched_event_cluster_count"),
                    "matched_allocated_event_count": item.get("matched_allocated_event_count"),
                    "matched_policy_count_raw": item.get("matched_policy_count_raw"),
                    "deduplication_effect": item.get("deduplication_effect"),
                    "stance_adjustment_effect": item.get("stance_adjustment_effect"),
                    "primary_event_count": item.get("primary_event_count"),
                    "avg_allocation_share": item.get("avg_allocation_share"),
                    "lifecycle_state": item.get("lifecycle_state"),
                    "lifecycle_quality_multiplier": item.get("lifecycle_quality_multiplier"),
                    "score_30d": item.get("score_30d"),
                    "score_90d": item.get("score_90d"),
                    "supportive_cluster_count": item.get("supportive_cluster_count"),
                    "restrictive_cluster_count": item.get("restrictive_cluster_count"),
                    "avg_cluster_stance_score_v2": item.get("avg_cluster_stance_score_v2"),
                    "avg_cluster_relevance_score_v2": item.get("avg_cluster_relevance_score_v2"),
                    "resonance_score": _resonance_score(legacy or item),
                    "triple_confirmation": all(
                        (_float_or_none((legacy or item).get(key)) or 0) >= 75
                        for key in ("evidence_score", "ths_score", "etf_score")
                    ),
                    "stage": item.get("lifecycle_state") or item.get("stage", ""),
                    "rank": item.get("rank", rank),
                    "report_id": summary["report_id"],
                }
            )
    return {
        "themes": [{"theme": theme, "points": points} for theme, points in sorted(points_by_theme.items())],
        "report_count": len(reports),
    }


def build_index_payload(report_id: str, payload: dict[str, Any], markdown: str) -> dict[str, Any]:
    mainline_ranking = _mainline_rows(payload)
    canonical_mainline_summary = _canonical_summary(payload)
    legacy_theme_ranking = enrich_theme_ranking(_legacy_theme_rows(payload))
    legacy_top = legacy_theme_ranking[0] if legacy_theme_ranking else {}
    theme_summary = payload.get("theme_summary") or {}
    top_mainline = mainline_ranking[0] if mainline_ranking else {}
    breadth = payload.get("breadth") or {}
    top_mainline_theme = top_mainline.get("theme_name", "")
    top_mainline_score = top_mainline.get("mainline_score_v6")
    return {
        "page": "index",
        "latest_report": {
            "report_id": report_id,
            "basis_date": payload.get("basis_date", ""),
            "generated_at": payload.get("generated_at", ""),
            "theme_count": len(mainline_ranking) or len(legacy_theme_ranking),
            "top_theme": top_mainline_theme,
            "top_stage": top_mainline.get("lifecycle_state", ""),
            "top_score": top_mainline_score,
            "top_mainline_theme": top_mainline_theme,
            "top_mainline_score": top_mainline_score,
            "top_mainline_lifecycle_state": top_mainline.get("lifecycle_state", ""),
            "default_score_field": canonical_mainline_summary.get("default_score_field", DEFAULT_SCORE_FIELD),
            "canonical_mainline_version": canonical_mainline_summary.get("scoring_version", ""),
            "legacy_top_theme": legacy_top.get("theme", ""),
            "legacy_top_score": legacy_top.get("evidence_score"),
            "theme_scoring_version": theme_summary.get("scoring_version", ""),
            "mainline_lifecycle_version": theme_summary.get("mainline_lifecycle_version", ""),
            "top_mainline_theme_v6": top_mainline_theme,
            "top_mainline_score_v6": top_mainline_score,
            "up_ratio": breadth.get("up_ratio"),
        },
        "mainline_ranking": mainline_ranking,
        "canonical_mainline_summary": canonical_mainline_summary,
        "theme_ranking": legacy_theme_ranking,
        "legacy_theme_ranking": legacy_theme_ranking,
        "event_cluster_summary": payload.get("event_cluster_summary") or {},
        "policy_stance_summary": payload.get("policy_stance_summary") or {},
        "event_theme_allocation_summary": payload.get("event_theme_allocation_summary") or {},
        "mainline_lifecycle_summary": payload.get("mainline_lifecycle_summary") or {},
        "theme_summary": theme_summary,
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
    page_report["mainline_ranking"] = _mainline_rows(payload)
    page_report["canonical_mainline_summary"] = _canonical_summary(payload)
    page_report["legacy_theme_ranking"] = enrich_theme_ranking(_legacy_theme_rows(payload))
    page_report["theme_ranking"] = page_report["legacy_theme_ranking"]
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
    return {"report_id": report_id, "result": _with_canonical_fields(payload)}
