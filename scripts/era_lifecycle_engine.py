from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "config" / "era_lifecycle_rules.json"
VERSION = "era_lifecycle_engine_v1"


def load_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _date(value: Any) -> date | None:
    text = str(value or "")[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def target_stage(snapshot: dict[str, Any], previous_stage: str = "dormant") -> tuple[str, list[str]]:
    policy = float(snapshot.get("policy_score") or 0)
    market = float(snapshot.get("market_score") or 0)
    narrative = float(snapshot.get("narrative_score") or 0)
    industry = snapshot.get("industry_score")
    era_score = float(snapshot.get("era_mainline_score") or 0)
    confirmed = policy >= 50 and (market >= 52 or (industry is not None and float(industry) >= 50))
    reasons: list[str] = []
    if snapshot.get("source_lifecycle_state") == "legacy_tail":
        return "cooling", ["旧政策贡献仍可识别，但近期强化不足，进入旧线降温观察"]
    if previous_stage == "ended" and policy >= 35 and (market >= 35 or narrative >= 45):
        return "restarting", ["结束后出现新的政策、市场或叙事驱动力"]
    if previous_stage in {"cooling", "declining"} and policy < 30 and market < 30 and era_score < 35:
        return "declining", ["政策、市场和综合证据持续转弱"]
    if previous_stage in {"confirmed", "expanding", "mature"} and market < 40 and era_score < 48:
        return "cooling", ["市场持续确认转弱且时代主线综合分下降"]
    if policy < 10 and market < 20:
        return "dormant", ["政策和市场均未形成结构性证据"]
    if policy < 30 and market < 35:
        return "incubating", ["已有早期信号，但多层证据尚未形成"]
    if not confirmed and policy >= 30 and market < 35:
        return "emerging", ["政策信号增强，市场尚未开始持续定价"]
    if not confirmed:
        return "launching", ["政策信号与至少一项外部验证开始共振，但尚未达到确认门槛"]
    if confirmed and market >= 68 and narrative >= 52:
        return "expanding", ["政策得到市场确认，二级主题和官方叙事同步扩散"]
    reasons.append("政策达到门槛，产业或市场至少一层形成确认")
    return "confirmed", reasons


def apply_transition(previous: str, desired: str, *, rules: dict[str, Any] | None = None) -> tuple[str, bool]:
    active = rules or load_rules()
    legal = set((active.get("legal_transitions") or {}).get(previous) or [])
    if desired in legal:
        return desired, True
    order = ["dormant", "incubating", "emerging", "launching", "confirmed", "expanding", "mature", "cooling", "declining", "ended"]
    if previous in order and desired in order:
        step = 1 if order.index(desired) > order.index(previous) else -1
        adjacent = order[order.index(previous) + step]
        return adjacent, False
    return "uncertain", False


def enrich_lifecycle(history: list[dict[str, Any]], *, rules: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    active = rules or load_rules()
    labels = active.get("labels") or {}
    previous = "dormant"
    weak_observations = 0
    transitions: list[dict[str, Any]] = []
    output: list[dict[str, Any]] = []
    for snapshot in history:
        weak = float(snapshot.get("era_mainline_score") or 0) < float(active.get("ending_score_threshold") or 30)
        weak_observations = weak_observations + 1 if weak else 0
        desired, reasons = target_stage(snapshot, previous)
        if previous == "declining" and weak_observations >= int(active.get("minimum_end_observations") or 3):
            desired, reasons = "ended", ["连续多个观察期多维证据低于结束阈值"]
        stage, direct = apply_transition(previous, desired, rules=active)
        row = {**snapshot, "lifecycle_stage": stage, "lifecycle_stage_label": labels.get(stage, stage), "stage_reasons": reasons, "direct_transition": direct}
        if stage != previous:
            transitions.append(
                {
                    "theme_id": snapshot.get("theme_id", ""),
                    "from_stage": previous,
                    "to_stage": stage,
                    "change_date": snapshot.get("date", ""),
                    "change_reasons": reasons,
                    "dimension_changes": snapshot.get("dimension_changes", {}),
                    "confidence": snapshot.get("confidence", 0),
                    "legal_direct_transition": direct,
                }
            )
        output.append(row)
        previous = stage
    return output, transitions


def lifecycle_dates(history: list[dict[str, Any]], events: list[dict[str, Any]], basis_date: str) -> dict[str, Any]:
    signal_dates = sorted(
        item
        for item in (_date(event.get("event_date") or event.get("event_activity_date") or event.get("published_date")) for event in events)
        if item
    )
    first_forming = next((row for row in history if row.get("lifecycle_stage") in {"emerging", "launching", "confirmed", "expanding", "mature"}), None)
    first_confirmed = next((row for row in history if row.get("lifecycle_stage") in {"confirmed", "expanding", "mature"}), None)
    weakening = next((row for row in history if row.get("lifecycle_stage") in {"cooling", "declining"}), None)
    ended = next((row for row in history if row.get("lifecycle_stage") == "ended"), None)
    estimated = _date(first_forming.get("date")) if first_forming else None
    if estimated and signal_dates and estimated < signal_dates[0]:
        estimated = signal_dates[0]
    confirmation = _date(first_confirmed.get("date")) if first_confirmed else None
    if confirmation and estimated and confirmation < estimated:
        confirmation = estimated
    basis = _date(basis_date)
    return {
        "signal_start_date": signal_dates[0].isoformat() if signal_dates else "",
        "estimated_start_date": estimated.isoformat() if estimated else "",
        "confirmation_date": confirmation.isoformat() if confirmation else "",
        "latest_reinforcement_date": signal_dates[-1].isoformat() if signal_dates else "",
        "weakening_start_date": str(weakening.get("date") or "") if weakening else "",
        "estimated_end_date": str(ended.get("date") or "") if ended else "",
        "duration_days": (basis - estimated).days if basis and estimated and basis >= estimated else 0,
    }
