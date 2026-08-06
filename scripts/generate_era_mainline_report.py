from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from era_mainline_model import build_era_mainline_report
    from era_mainline_validator import validate
except ModuleNotFoundError:
    from scripts.era_mainline_model import build_era_mainline_report
    from scripts.era_mainline_validator import validate


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "research" / "mainline"
OUTPUT_DIR = ROOT / "research" / "era_mainline"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def source_reports() -> list[tuple[str, dict[str, Any]]]:
    by_basis_date: dict[str, tuple[str, dict[str, Any]]] = {}
    for path in sorted(SOURCE_DIR.glob("mainline_review_*.json")):
        try:
            payload = _read(path)
            basis_date = str(payload.get("basis_date") or path.stem)
            candidate = (path.stem, payload)
            existing = by_basis_date.get(basis_date)
            if existing is None or (str(payload.get("generated_at") or ""), path.stem) > (str(existing[1].get("generated_at") or ""), existing[0]):
                by_basis_date[basis_date] = candidate
        except (json.JSONDecodeError, OSError):
            continue
    rows = list(by_basis_date.values())
    rows.sort(key=lambda item: (str(item[1].get("basis_date") or ""), str(item[1].get("generated_at") or ""), item[0]))
    return rows


def _state_title(state: dict[str, Any] | None) -> str:
    if not state:
        return "暂无"
    return f"{state['theme_name']}（{state['lifecycle_stage_label']}，确信度 {state['era_mainline_confidence']:.0f}）"


def render_markdown(payload: dict[str, Any]) -> str:
    primary = payload.get("primary_mainline")
    secondary = payload.get("secondary_mainline")
    emerging = payload.get("emerging_candidates") or []
    declining = payload.get("declining_mainlines") or []
    lines = [
        "# 时代主线研究报告",
        "",
        "## 当前结论",
        "",
        payload.get("summary", ""),
        "",
        "## 当前主线格局",
        "",
        f"- 格局：{payload.get('mainline_regime', '')}",
        f"- 第一时代主线：{_state_title(primary)}",
        f"- 第二时代主线：{_state_title(secondary)}",
        f"- 潜在新主线：{'、'.join(item['theme_name'] for item in emerging[:3]) or '暂无明确候选'}",
        f"- 正在退潮的主线：{'、'.join(item['theme_name'] for item in declining) or '暂无'}",
        "",
        "## 第一时代主线",
        "",
    ]
    lines.extend(_theme_markdown(primary))
    lines.extend(["", "## 第二时代主线", ""])
    lines.extend(_theme_markdown(secondary))
    lines.extend(["", "## 潜在新主线", "", "、".join(item["theme_name"] for item in emerging) or "暂无明确候选。"])
    lines.extend(["", "## 正在退潮的主线", "", "、".join(item["theme_name"] for item in declining) or "暂无明确退潮主线。"])
    lines.extend(["", "## 主线生命周期总览", "", "| 排名 | 主题 | 状态 | 阶段 | 开始 | 确认 | 强化 | 分数 | 置信度 |", "|---:|---|---|---|---|---|---|---:|---:|"])
    for state in payload.get("theme_states") or []:
        lines.append(
            f"| {state['era_rank']} | {state['theme_name']} | {state['era_mainline_status']} | {state['lifecycle_stage_label']} | "
            f"{state.get('estimated_start_date') or '未知'} | {state.get('confirmation_date') or '未确认'} | {state.get('latest_reinforcement_date') or '未知'} | "
            f"{state['era_mainline_score']:.1f} | {state['era_mainline_confidence']:.0f} |"
        )
    lines.extend(["", "## 四维证据拆解", ""])
    for state in payload.get("theme_states") or []:
        industry = "未知" if state.get("industry_score") is None else f"{state['industry_score']:.1f}"
        lines.append(f"- {state['theme_name']}：政策 {state['policy_score']:.1f}；产业 {industry}；市场 {state['market_score']:.1f}；叙事 {state['narrative_score']:.1f}。")
    lines.extend(["", "## 主线开始时间判断", "", "开始日期取政策信号持续并出现市场或产业响应后的形成时点，不直接使用第一条相关政策日期。"])
    lines.extend(["", "## 主线结束风险判断", "", "结束需要政策、产业、市场和叙事多个观察期共同转弱；短期市场调整或90天无新增政策均不足以单独判定结束。"])
    lines.extend(["", "## 支撑证据", ""])
    for state in payload.get("theme_states") or []:
        lines.append(f"- {state['theme_name']}：{'；'.join(state.get('supporting_evidence') or [])}")
    lines.extend(["", "## 反面证据", ""])
    for state in payload.get("theme_states") or []:
        lines.append(f"- {state['theme_name']}：{'；'.join(state.get('contradicting_evidence') or []) or '暂无明确反证'}")
    lines.extend(["", "## 失效条件", ""])
    for state in payload.get("theme_states") or []:
        lines.append(f"- {state['theme_name']}：{'；'.join(state.get('invalidating_conditions') or [])}")
    lines.extend(["", "## 二级主题内部结构", ""])
    for state in payload.get("theme_states") or []:
        secondary_rows = state.get("market_dimension", {}).get("secondary_themes") or []
        detail = "、".join(f"{item['theme_name']}({float(item.get('market_confirmation_score') or 0):.1f})" for item in secondary_rows[:6]) or "暂无"
        lines.append(f"- {state['theme_name']}：{detail}")
    lines.extend(["", "## 历史阶段变化", ""])
    for item in payload.get("transitions") or []:
        lines.append(f"- {item['change_date']} {item['theme_id']}：{item['from_stage']} → {item['to_stage']}。")
    lines.extend(["", "## 数据不足和不确定性", "", "- 产业层多数代理指标尚无可靠观测，统一显示未知并降低置信度。", "- 市场层当前使用现有申万、同花顺、ETF、涨停与资金流数据，长期相对强度和回撤指标仍待补充。", "- 历史阶段由既有报告确定性回放，不等同于当时已发布的时代主线结论。", "", payload.get("score_semantics", "")])
    return "\n".join(lines) + "\n"


def _theme_markdown(state: dict[str, Any] | None) -> list[str]:
    if not state:
        return ["当前没有满足确认门槛的主题。"]
    return [
        f"- 主题：{state['theme_name']}",
        f"- 当前阶段：{state['lifecycle_stage_label']}",
        f"- 估计开始时间：{state.get('estimated_start_date') or '证据不足'}",
        f"- 确认时间：{state.get('confirmation_date') or '尚未确认'}",
        f"- 最近强化：{state.get('latest_reinforcement_date') or '未知'}",
        f"- 核心驱动力：{'；'.join(state.get('core_drivers') or [])}",
        f"- 最大反面证据：{(state.get('contradicting_evidence') or ['暂无明确反证'])[0]}",
        f"- 失效条件：{'；'.join(state.get('invalidating_conditions') or [])}",
    ]


def generate(*, write: bool = False, now: datetime | None = None) -> tuple[dict[str, Any], Path, Path]:
    reports = source_reports()
    if not reports:
        raise FileNotFoundError("No source mainline reports found")
    source_id, source = reports[-1]
    payload = build_era_mainline_report(source_id, source, reports)
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    report_id = f"era_mainline_review_{stamp}"
    payload["report_id"] = report_id
    payload["validation_summary"] = validate(payload)
    if payload["validation_summary"]["error_count"]:
        raise RuntimeError("Era-mainline report validation failed before write")
    json_path = OUTPUT_DIR / f"{report_id}.json"
    md_path = OUTPUT_DIR / f"{report_id}.md"
    if write:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        md_path.write_text(render_markdown(payload), encoding="utf-8")
    return payload, json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the deterministic era-mainline research report.")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    payload, json_path, md_path = generate(write=args.write)
    print(json.dumps({"report_id": payload["report_id"], "regime": payload["mainline_regime"], "json_path": str(json_path), "markdown_path": str(md_path), "write": args.write}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
