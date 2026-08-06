from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from theme_taxonomy_v2 import build_taxonomy_v2_backfill
except ModuleNotFoundError:
    from scripts.theme_taxonomy_v2 import build_taxonomy_v2_backfill


VERSION = "market_confirmation_v1"
ROOT = Path(__file__).resolve().parents[1]
ERA_TAXONOMY_PATH = ROOT / "config" / "era_theme_taxonomy.json"


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def build_market_dimensions(report_id: str, report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    taxonomy = build_taxonomy_v2_backfill(report_id, report)
    secondary_by_id = {str(row.get("theme_id") or ""): row for row in taxonomy.get("themes") or []}
    era_taxonomy = json.loads(ERA_TAXONOMY_PATH.read_text(encoding="utf-8"))
    grouped: dict[str, list[dict[str, Any]]] = {
        str(primary.get("theme_id") or ""): [
            secondary_by_id[secondary_id]
            for secondary_id in primary.get("secondary_theme_ids") or []
            if secondary_id in secondary_by_id
        ]
        for primary in era_taxonomy.get("primary_themes") or []
    }
    result: dict[str, dict[str, Any]] = {}
    for theme_id, rows in grouped.items():
        scores = [_number(row.get("market_heat_score")) for row in rows]
        direct = [row for row in rows if not row.get("is_backfilled") or "直接命中" in str(row.get("confidence_reason"))]
        score = round(0.65 * max(scores or [0]) + 0.35 * (sum(scores) / len(scores) if scores else 0), 2)
        breadth = round(sum(1 for value in scores if value >= 45) / len(scores), 4) if scores else 0.0
        if score >= 78:
            stage = "crowded"
        elif score >= 68 and breadth >= 0.45:
            stage = "broadening"
        elif score >= 55:
            stage = "confirmed"
        elif score >= 35:
            stage = "early_pricing"
        else:
            stage = "unconfirmed"
        result[theme_id] = {
            "scoring_version": VERSION,
            "market_confirmation_score": score,
            "market_stage": stage,
            "relative_strength_20d": None,
            "relative_strength_60d": None,
            "relative_strength_120d": None,
            "trend_persistence": round(min(100.0, score * (0.65 + 0.35 * breadth)), 2),
            "breadth_score": round(breadth * 100, 2),
            "leader_participation": None,
            "etf_flow_persistence": None,
            "turnover_expansion": None,
            "drawdown_state": "unknown",
            "crowding_risk": round(max(0.0, score - 72) * 3.0, 2),
            "secondary_themes": [
                {
                    "theme_id": row.get("theme_id"),
                    "theme_name": row.get("theme_name"),
                    "market_confirmation_score": row.get("market_heat_score"),
                    "confidence": row.get("confidence_score"),
                }
                for row in sorted(rows, key=lambda item: -_number(item.get("market_heat_score")))
            ],
            "data_coverage": round(len(direct) / len(rows), 4) if rows else 0.0,
            "interpretation": "市场层用于验证长期方向是否获得资本市场持续认同，不代表交易信号。",
        }
    return result
