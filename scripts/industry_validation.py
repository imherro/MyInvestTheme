from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "industry_indicator_mapping.json"
VERSION = "industry_validation_v1"


def load_mapping(path: Path = CONFIG_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_industry_dimension(theme_id: str, *, mapping: dict[str, Any] | None = None) -> dict[str, Any]:
    active = mapping or load_mapping()
    indicator_ids = list((active.get("themes") or {}).get(theme_id) or [])
    observations = active.get("observations") or {}
    indicators: list[dict[str, Any]] = []
    for indicator_id in indicator_ids:
        item = observations.get(indicator_id)
        if not isinstance(item, dict):
            indicators.append(
                {
                    "indicator_id": indicator_id,
                    "indicator_name": indicator_id,
                    "value": None,
                    "change_1m": None,
                    "change_3m": None,
                    "change_12m": None,
                    "trend": "unknown",
                    "data_date": "",
                    "data_source": "",
                    "confidence": 0,
                    "status": "unknown",
                }
            )
        else:
            indicators.append({"indicator_id": indicator_id, "status": "observed", **item})
    observed = [item for item in indicators if item["status"] == "observed"]
    scores = [float(item.get("score")) for item in observed if item.get("score") is not None]
    score = round(sum(scores) / len(scores), 2) if scores else None
    if not observed:
        stage = "no_validation"
        label = "产业验证不足"
    elif score is not None and score >= 70:
        stage, label = "broad_validation", "广泛验证"
    elif score is not None and score >= 50:
        stage, label = "partial_validation", "部分验证"
    else:
        stage, label = "early_validation", "早期验证"
    return {
        "scoring_version": VERSION,
        "industry_validation_score": score,
        "industry_stage": stage,
        "industry_stage_label": label,
        "indicators": indicators,
        "configured_indicator_count": len(indicators),
        "observed_indicator_count": len(observed),
        "coverage": round(len(observed) / len(indicators), 4) if indicators else 0.0,
        "is_unknown": not observed,
        "notes": ["产业验证不足：尚无可靠、可比的产业代理观测。"] if not observed else [],
    }
