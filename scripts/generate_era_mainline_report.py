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
    return f"{state['theme_name']}（{state['lifecycle_stage_label']}，状态置信度 {state['current_state_confidence']:.0f}）"


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
        "> 这是当前模型对历史数据的回放，不等于当时发布的研究判断。",
        "",
        "## 分类型研究结论",
        "",
    ]
    class_sections = [
        ("era_industrial_ranking", "长期时代产业主线"), ("strategic_growth_ranking", "战略成长主线"),
        ("policy_profit_repair_ranking", "政策盈利修复主线"), ("macro_cycle_ranking", "宏观阶段主线"),
        ("trading_branch_ranking", "交易支线"), ("allocation_style_ranking", "配置风格"),
    ]
    for key, title in class_sections:
        lines.extend([f"### {title}", ""])
        rows = payload.get(key) or []
        if not rows:
            lines.append("当前无达到该类型识别门槛的研究对象。")
        for item in rows:
            lines.append(
                f"- {item['theme_name']}：结构{item.get('structural_stage_label', '不适用')}（{item.get('structural_conviction_score', 0):.1f}），"
                f"市场表达{item.get('market_expression_stage_label', '未知')}（{item.get('market_expression_score', 0):.1f}），"
                f"周期强度 {item.get('cycle_strength_score', 0):.1f}；类型置信度 {item.get('class_confidence', 0):.0f}。"
            )
    comparison = payload.get("research_hypothesis_comparison", {}).get("system_assessment", {})
    lines.extend(["", "## 人工研究框架对照", ""])
    for key, title in (("agreements", "一致"), ("partial_agreements", "部分一致"), ("disagreements", "分歧"), ("recommended_reframing", "建议重构")):
        lines.append(f"- {title}：{'；'.join(comparison.get(key) or []) or '无'}")
    lines.extend([
        "",
        "## 双生命周期总览",
        "",
        "| 类型 | 主题 | 结构阶段 | 市场表达阶段 | 结构分 | 周期分 | 市场分 | 典型时间尺度 |",
        "|---|---|---|---|---:|---:|---:|---|",
    ])
    for item in payload.get("research_objects") or []:
        horizon = item.get("time_horizon") or {}
        typical = "随市场环境" if horizon.get("unit") == "regime_dependent" else f"{horizon.get('typical', '未知')} {horizon.get('unit', '')}"
        lines.append(f"| {item.get('mainline_class_label')} | {item['theme_name']} | {item.get('structural_stage_label')} | {item.get('market_expression_stage_label')} | {item.get('structural_conviction_score', 0):.1f} | {item.get('cycle_strength_score', 0):.1f} | {item.get('market_expression_score', 0):.1f} | {typical} |")
    lines.extend([
        "",
        "## 当前主线格局（兼容市场证据）",
        "",
        f"- 格局：{payload.get('mainline_regime', '')}",
        f"- 第一时代主线：{_state_title(primary)}",
        f"- 第二时代主线：{_state_title(secondary)}",
        f"- 潜在新主线：{'、'.join(item['theme_name'] for item in emerging[:3]) or '暂无明确候选'}",
        f"- 正在退潮的主线：{'、'.join(item['theme_name'] for item in declining) or '暂无'}",
        "",
        "## 原一级主题第一候选",
        "",
    ])
    lines.extend(_theme_markdown(primary))
    lines.extend(["", "## 第二时代主线", ""])
    lines.extend(_theme_markdown(secondary))
    lines.extend(["", "## 潜在新主线", "", "、".join(item["theme_name"] for item in emerging) or "暂无明确候选。"])
    lines.extend(["", "## 正在退潮的主线", "", "、".join(item["theme_name"] for item in declining) or "暂无明确退潮主线。"])
    lines.extend(["", "## 数据覆盖限制", "", "- 产业维度暂无可靠观测。本期综合分使用政策、市场和官方战略叙事三维动态重算，因此不属于完整四维确认。", f"- {payload.get('history_semantics', {}).get('description', '')}"])
    lines.extend(["", "## 主线生命周期总览", "", "| 排名 | 主题 | 主线资格 | 证据阶段 | 短期动量 | 阶段停留 | 周期 | 开始 | 确认 | 最近强化 | 分数 | 状态置信度 | 阶段置信度 |", "|---:|---|---|---|---|---:|---|---|---|---|---:|---:|---:|"])
    for state in payload.get("theme_states") or []:
        lines.append(
            f"| {state['era_rank']} | {state['theme_name']} | {state['mainline_qualification_label']} | {state['lifecycle_stage_label']} | {state.get('momentum_state_label', '未知')} | "
            f"{state.get('stage_dwell_days', 0)}天 | 第{state.get('cycle_sequence', 0)}轮 | {state.get('estimated_start_date') or ('早于覆盖范围' if state.get('start_date_status') == 'before_available_history' else '未知')} | {state.get('confirmation_date') or '未确认'} | "
            f"{state.get('latest_reinforcement_date') or '无'}({state.get('latest_reinforcement_type', 'none')}) | "
            f"{state['era_mainline_score']:.1f} | {state['current_state_confidence']:.0f} | {state['lifecycle_stage_confidence']:.0f} |"
        )
    lines.extend(["", "## 四维证据拆解", ""])
    for state in payload.get("theme_states") or []:
        industry = "未知" if state.get("industry_score") is None else f"{state['industry_score']:.1f}"
        weights = state.get("effective_dimension_weights") or {}
        lines.append(f"- {state['theme_name']}：政策 {state['policy_score']:.1f}；产业 {industry}；市场 {state['market_score']:.1f}；官方战略叙事 {state['narrative_score']:.1f}。实际权重：政策 {weights.get('policy', 0):.1%}、产业 {weights.get('industry', 0):.1%}、市场 {weights.get('market', 0):.1%}、叙事 {weights.get('narrative', 0):.1%}。")
    lines.extend(["", "## 主线开始时间判断", "", "开始日期仅在形成条件达到最低持续周期后回填形成窗口起点，并同时记录模型决定日期；历史左侧截断时不输出伪精确起点。"])
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
        dwell = "满足停留" if item.get("stage_dwell_satisfied") else ("严重证据豁免" if item.get("dwell_override") else "未满足停留")
        skipped = f"；跳过 {','.join(item.get('skipped_stages') or [])}" if item.get("skipped_stages") else ""
        lines.append(f"- {item['change_date']} {item['theme_id']}：{item['from_stage']} → {item['to_stage']}（{item.get('transition_type')}；{dwell}{skipped}）。{'；'.join(item.get('change_reasons') or [])}")
    lines.extend(["", "## 数据不足和不确定性", "", "- 产业层多数代理指标尚无可靠观测，统一显示未知并降低置信度。", "- 市场层当前使用现有申万、同花顺、ETF、涨停与资金流数据，长期相对强度和回撤指标仍待补充。", "- 历史阶段由既有报告确定性回放，不等同于当时已发布的时代主线结论。", "- 当前叙事维度仅表示官方战略表述和子主题扩散，不代表社会舆论热度。", "", payload.get("score_semantics", "")])
    return "\n".join(lines) + "\n"


def _theme_markdown(state: dict[str, Any] | None) -> list[str]:
    if not state:
        return ["当前没有满足确认门槛的主题。"]
    lines = [
        f"- 主题：{state['theme_name']}",
        f"- 主线资格：{state['mainline_qualification_label']}",
        f"- 证据阶段：{state['lifecycle_stage_label']}",
        f"- 短期动量：{state.get('momentum_state_label', '未知')}",
        f"- 生命周期轮次：第{state.get('cycle_sequence', 0)}轮（{state.get('cycle_id', '')}）",
        f"- 当前阶段停留：{state.get('stage_dwell_days', 0)}天；最低要求 {state.get('minimum_stage_dwell_days', 0)}天",
        f"- 当前状态置信度：{state['current_state_confidence']:.0f}",
        f"- 阶段置信度：{state['lifecycle_stage_confidence']:.0f}",
        f"- 估计开始时间：{state.get('estimated_start_date') or ('早于当前历史覆盖范围' if state.get('start_date_status') == 'before_available_history' else '证据不足')}（日期置信度 {state.get('estimated_start_date_confidence', 0):.0f}）",
        f"- 开始日期决定于：{state.get('start_date_decided_at') or '尚未决定'}",
        f"- 确认时间：{state.get('confirmation_date') or '持续条件尚未满足'}（日期置信度 {state.get('confirmation_date_confidence', 0):.0f}）",
        f"- 最新政策事件：{state.get('latest_policy_event_date') or '未知'}",
        f"- 最近政策强化事件：{state.get('latest_policy_reinforcement_event_date') or '无'}",
        f"- 最近市场强化：{state.get('latest_market_reinforcement_date') or '无'}",
        f"- 最近综合强化：{state.get('latest_composite_reinforcement_date') or '无'}",
        f"- 最终采用强化：{state.get('latest_reinforcement_date') or '近期无达到定义门槛的强化事件'}（{state.get('latest_reinforcement_type', 'none')}）",
        f"- 强化理由：{'；'.join(state.get('latest_reinforcement_reasons') or [])}",
        f"- 核心驱动力：{'；'.join(state.get('core_drivers') or [])}",
        f"- 最大反面证据：{(state.get('contradicting_evidence') or ['暂无明确反证'])[0]}",
        f"- 失效条件：{'；'.join(state.get('invalidating_conditions') or [])}",
    ]
    if state.get("evidence_stage") == "cooling" and float(state.get("era_mainline_score") or 0) >= 50:
        lines.insert(6, "- 阶段说明：当前仍是高分候选，但近期证据相对本轮峰值持续减弱，处于降温观察；不等同于结构性衰退。")
    return lines


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
