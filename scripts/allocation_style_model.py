from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "config" / "allocation_style_rules.json"


def evaluate_high_dividend(features: dict[str, Any], *, market_score: float = 0) -> dict[str, Any]:
    rules = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    spec = rules["styles"]["high_dividend"]
    components = {
        "rate_environment_score": float(features.get("rate_sensitivity") or 50),
        "dividend_stability_score": float(features.get("cash_flow_quality") or 65),
        "cash_flow_quality_score": float(features.get("cash_flow_quality") or 65),
        "relative_valuation_score": 60.0,
        "market_defensiveness_score": max(35.0, min(80.0, 100.0 - market_score * 0.35)),
    }
    score = round(sum(components[key] * weight for key, weight in spec["weights"].items()), 2)
    state = next(item["state"] for item in rules["states"] if score >= item["minimum"])
    return {
        "model_version": rules["version"], "style_id": "high_dividend", "style_name": spec["label"],
        "style_state": state, "allocation_style_score": score, **components,
        "supporting_evidence": ["现金流质量和分红稳定性提供配置基础", "风格有效性取决于利率与风险偏好"],
        "contradicting_evidence": ["缺少独立实时利率和估值序列，风格置信度受限"],
    }
