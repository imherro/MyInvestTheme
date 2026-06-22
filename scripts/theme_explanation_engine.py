from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    from .trace_graph_builder import build_trace_graph, round6, validate_trace_graph
except ImportError:
    try:
        from trace_graph_builder import build_trace_graph, round6, validate_trace_graph
    except ModuleNotFoundError:
        from scripts.trace_graph_builder import build_trace_graph, round6, validate_trace_graph


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "research" / "mainline"
SCORING_VERSION = "explainability_trace_graph_v2"
TRACE_ROOT = "mainline_score_v6"
TRACE_PATH = [
    "policy_score_v2",
    "theme_relevance_v2",
    "policy_theme_stance_v2",
    "event_theme_allocation_v2",
    "mainline_lifecycle_v2",
    "mainline_score_v6",
]
CONTRIBUTION_TOLERANCE = 1e-6


class ThemeExplanationNotFound(KeyError):
    pass


def latest_report_path() -> Path:
    files = sorted(REPORT_DIR.glob("mainline_review_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError("No mainline report JSON files found.")
    return files[0]


def load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _round4(value: Any) -> float:
    return round(round6(value), 4)


def _theme_id(row: dict[str, Any]) -> str:
    return str(row.get("theme_id") or row.get("theme_name") or row.get("theme") or "")


def _find_theme(report: dict[str, Any], theme_id_or_name: str) -> dict[str, Any]:
    candidates = list((report.get("theme_summary") or {}).get("themes") or [])
    if not candidates:
        candidates = list(report.get("mainline_ranking") or [])
    for row in candidates:
        if not isinstance(row, dict):
            continue
        keys = {_theme_id(row), str(row.get("theme_name") or ""), str(row.get("theme") or "")}
        if theme_id_or_name in keys:
            return row
    raise ThemeExplanationNotFound(theme_id_or_name)


def _mainline_row(report: dict[str, Any], theme_id: str) -> dict[str, Any]:
    for row in report.get("mainline_ranking") or []:
        if isinstance(row, dict) and _theme_id(row) == theme_id:
            return row
    return {}


def _policy_lookup(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for section_name in ("policy_provenance_summary", "policy_snapshot_summary"):
        for policy in (report.get(section_name) or {}).get("policies") or []:
            if not isinstance(policy, dict):
                continue
            policy_id = str(policy.get("policy_id") or "")
            if policy_id:
                result[policy_id] = {**result.get(policy_id, {}), **policy}
    for cluster in (report.get("event_cluster_summary") or {}).get("clusters") or []:
        if not isinstance(cluster, dict):
            continue
        policy_id = str(cluster.get("primary_policy_id") or "")
        if policy_id:
            result[policy_id] = {
                **result.get(policy_id, {}),
                "policy_id": policy_id,
                "title": cluster.get("primary_policy_title", ""),
                "source_org_norm": cluster.get("source_org_norm", ""),
                "publish_date": cluster.get("publish_date_max") or cluster.get("publish_date_min") or "",
            }
    return result


def _unique_policy_ids(event: dict[str, Any]) -> list[str]:
    primary = str(event.get("primary_policy_id") or "")
    ids = [str(policy_id) for policy_id in event.get("member_policy_ids") or [] if policy_id]
    if primary and primary not in ids:
        ids.insert(0, primary)
    return [policy_id for index, policy_id in enumerate(ids) if policy_id and policy_id not in ids[:index]]


def _top_keywords(events: list[dict[str, Any]], limit: int = 5) -> list[str]:
    counts: Counter[str] = Counter()
    weights: defaultdict[str, float] = defaultdict(float)
    for event in events:
        for evidence in event.get("top_matched_evidence") or []:
            if not isinstance(evidence, dict):
                continue
            keyword = str(evidence.get("keyword") or "").strip()
            if not keyword:
                continue
            counts[keyword] += 1
            weights[keyword] += round6(evidence.get("score_contribution"))
    ordered = sorted(counts, key=lambda keyword: (-counts[keyword], -weights[keyword], keyword))
    return ordered[:limit]


def _evidence_digest(event: dict[str, Any]) -> dict[str, Any]:
    matched = []
    for evidence in event.get("top_matched_evidence") or []:
        if not isinstance(evidence, dict):
            continue
        matched.append(
            {
                "source_field": evidence.get("source_field", ""),
                "keyword": evidence.get("keyword", ""),
                "keyword_type": evidence.get("keyword_type", ""),
                "score_component": evidence.get("score_component", ""),
                "score_contribution": round6(evidence.get("score_contribution")),
            }
        )
    stance = []
    for evidence in event.get("top_stance_evidence") or []:
        if not isinstance(evidence, dict):
            continue
        stance.append(
            {
                "source_field": evidence.get("source_field", ""),
                "matched_theme_keyword": evidence.get("matched_theme_keyword", ""),
                "stance_keyword": evidence.get("stance_keyword", ""),
                "keyword_type": evidence.get("keyword_type", ""),
                "score_component": evidence.get("score_component", ""),
                "score_contribution": round6(evidence.get("score_contribution")),
            }
        )
    return {
        "top_matched_evidence": matched[:8],
        "top_stance_evidence": stance[:5],
        "top_keywords": _top_keywords([event]),
    }


def _event_breakdown(event: dict[str, Any], theme: dict[str, Any]) -> dict[str, Any]:
    contribution = round6(event.get("allocated_cluster_contribution"))
    lifecycle_multiplier = round6(theme.get("lifecycle_quality_multiplier"))
    theme_score = round6(theme.get("theme_score_v5"))
    return {
        "event_cluster_id": event.get("event_cluster_id", ""),
        "theme_id": theme.get("theme_id", ""),
        "theme_name": theme.get("theme_name", ""),
        "primary_policy_id": event.get("primary_policy_id", ""),
        "primary_policy_title": event.get("primary_policy_title", ""),
        "source": event.get("source", ""),
        "published_date": event.get("published_date", ""),
        "event_activity_date": event.get("event_activity_date") or event.get("published_date", ""),
        "age_days": event.get("age_days"),
        "age_bucket": event.get("age_bucket", ""),
        "allocation_role": event.get("allocation_role", ""),
        "contribution": contribution,
        "theme_score_v5_contribution": contribution,
        "mainline_score_v6_contribution": round6(contribution * lifecycle_multiplier),
        "contribution_share_of_theme": round6(contribution / theme_score) if theme_score else 0.0,
        "breakdown": {
            "policy_score_v2": round6(event.get("cluster_policy_score_v2")),
            "relevance_score_v2": round6(event.get("cluster_relevance_score_v2")),
            "stance_score_v2": round6(event.get("cluster_stance_score_v2")),
            "stance_label": event.get("cluster_stance_label", ""),
            "stance_multiplier": round6(event.get("direction_multiplier")),
            "pre_stance_contribution": round6(event.get("pre_stance_cluster_contribution")),
            "stance_adjusted_contribution": round6(event.get("stance_adjusted_cluster_contribution")),
            "raw_stance_adjusted_contribution": round6(event.get("raw_stance_adjusted_cluster_contribution")),
            "allocation_share": round6(event.get("allocation_share")),
            "allocated_cluster_contribution": contribution,
            "theme_allocation_reduction_effect": round6(event.get("theme_allocation_reduction_effect")),
            "allocation_capped": bool(event.get("allocation_capped")),
            "lifecycle_multiplier": lifecycle_multiplier,
        },
        "evidence": _evidence_digest(event),
    }


def _policy_paths(
    events: list[dict[str, Any]],
    event_breakdowns: list[dict[str, Any]],
    policy_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    breakdown_by_event = {str(row.get("event_cluster_id")): row for row in event_breakdowns}
    result: list[dict[str, Any]] = []
    for event in events:
        event_id = str(event.get("event_cluster_id") or "")
        event_breakdown = breakdown_by_event.get(event_id, {})
        policy_ids = _unique_policy_ids(event)
        if not policy_ids:
            continue
        path_contribution = round6(round6(event_breakdown.get("contribution")) / len(policy_ids))
        for policy_id in policy_ids:
            policy = policy_by_id.get(policy_id, {})
            result.append(
                {
                    "policy_id": policy_id,
                    "policy_title": policy.get("title") or event.get("primary_policy_title", ""),
                    "source_org": policy.get("source_org") or event.get("source", ""),
                    "source_org_norm": policy.get("source_org_norm", ""),
                    "source_url": policy.get("source_url") or event.get("url", ""),
                    "publish_date": policy.get("publish_date") or event.get("published_date", ""),
                    "provenance_status": policy.get("provenance_status", ""),
                    "event_cluster_id": event_id,
                    "theme_id": event.get("theme_id", ""),
                    "policy_score_v2": round6(event.get("cluster_policy_score_v2")),
                    "event_contribution": round6(event_breakdown.get("contribution")),
                    "path_contribution": path_contribution,
                    "contribution": path_contribution,
                    "path": list(TRACE_PATH),
                }
            )
    return sorted(result, key=lambda row: (-round6(row.get("path_contribution")), row.get("event_cluster_id", ""), row.get("policy_id", "")))


def _top_reasons(theme: dict[str, Any], event_breakdowns: list[dict[str, Any]]) -> list[str]:
    if not event_breakdowns:
        return ["当前报告没有可分配政策事件", f"生命周期状态为 {theme.get('lifecycle_state', 'unknown')}"]
    reasons: list[str] = []
    top_event = event_breakdowns[0]
    if top_event.get("primary_policy_title"):
        reasons.append(f"最大贡献政策事件：{top_event['primary_policy_title']}")
    keywords = _top_keywords([
        {
            "top_matched_evidence": event.get("evidence", {}).get("top_matched_evidence", [])
        }
        for event in event_breakdowns
    ])
    if keywords:
        reasons.append(f"高频匹配关键词：{'、'.join(keywords[:4])}")
    event_count_30d = int(theme.get("event_count_30d") or 0)
    event_count_90d = int(theme.get("event_count_90d") or 0)
    score_30d = round6(theme.get("score_30d"))
    score_90d = round6(theme.get("score_90d"))
    reasons.append(f"近30日贡献 {score_30d:.4f}，90日贡献 {score_90d:.4f}，90日事件数 {event_count_90d}")
    reasons.append(f"生命周期为 {theme.get('lifecycle_state', '')}，质量乘数 {round6(theme.get('lifecycle_quality_multiplier')):.4f}")
    if event_count_30d >= 1:
        reasons.append(f"近30日仍有 {event_count_30d} 个政策事件提供增量")
    return reasons[:5]


def _contribution_check(theme: dict[str, Any], event_breakdowns: list[dict[str, Any]]) -> dict[str, Any]:
    expected = round6(theme.get("theme_score_v5"))
    actual = round6(sum(round6(row.get("contribution")) for row in event_breakdowns))
    delta = round6(abs(actual - expected))
    return {
        "status": "pass" if delta <= CONTRIBUTION_TOLERANCE else "warning",
        "expected_theme_score_v5": expected,
        "event_contribution_sum": actual,
        "abs_delta": delta,
        "tolerance": CONTRIBUTION_TOLERANCE,
    }


def _mainline_formula_check(theme: dict[str, Any], event_breakdowns: list[dict[str, Any]]) -> dict[str, Any]:
    theme_score = round6(theme.get("theme_score_v5"))
    lifecycle_multiplier = round6(theme.get("lifecycle_quality_multiplier"))
    expected = _round4(theme_score * lifecycle_multiplier)
    actual = round6(theme.get("mainline_score_v6"))
    event_sum = round6(sum(round6(row.get("mainline_score_v6_contribution")) for row in event_breakdowns))
    delta = round6(abs(actual - expected))
    return {
        "status": "pass" if delta <= 0.0001 else "warning",
        "formula": "mainline_score_v6 = round4(theme_score_v5 * lifecycle_quality_multiplier)",
        "theme_score_v5": theme_score,
        "lifecycle_quality_multiplier": lifecycle_multiplier,
        "expected_mainline_score_v6": expected,
        "actual_mainline_score_v6": actual,
        "event_mainline_contribution_sum_unrounded": event_sum,
        "abs_delta": delta,
    }


def build_theme_explanation(report: dict[str, Any], theme_id_or_name: str) -> dict[str, Any]:
    source_theme = _find_theme(report, theme_id_or_name)
    theme_id = _theme_id(source_theme)
    theme = {
        **source_theme,
        **{key: value for key, value in _mainline_row(report, theme_id).items() if key not in {"all_event_contributors", "top_event_contributors"}},
    }
    theme["theme_id"] = theme_id
    events = sorted(
        [row for row in theme.get("all_event_contributors") or theme.get("top_event_contributors") or [] if isinstance(row, dict)],
        key=lambda row: (-round6(row.get("allocated_cluster_contribution")), row.get("event_cluster_id", "")),
    )
    event_breakdowns = [_event_breakdown(event, theme) for event in events]
    policy_paths = _policy_paths(events, event_breakdowns, _policy_lookup(report))
    trace_graph = build_trace_graph(theme, event_breakdowns, policy_paths)
    validation = validate_trace_graph(trace_graph)
    contribution_check = _contribution_check(theme, event_breakdowns)
    formula_check = _mainline_formula_check(theme, event_breakdowns)
    status = "pass" if validation["status"] == "pass" and contribution_check["status"] == "pass" else "warning"
    return {
        "scoring_version": SCORING_VERSION,
        "status": status,
        "report_id": report.get("report_id", ""),
        "basis_date": report.get("basis_date", ""),
        "generated_at": report.get("generated_at", ""),
        "theme_id": theme_id,
        "theme_name": theme.get("theme_name", ""),
        "trace_root": TRACE_ROOT,
        "mainline_score_v6": round6(theme.get("mainline_score_v6")),
        "theme_score_v5": round6(theme.get("theme_score_v5")),
        "lifecycle": {
            "state": theme.get("lifecycle_state", ""),
            "quality_multiplier": round6(theme.get("lifecycle_quality_multiplier")),
            "reasons": list(theme.get("lifecycle_reasons") or []),
            "score_30d": round6(theme.get("score_30d")),
            "score_90d": round6(theme.get("score_90d")),
            "event_count_30d": int(theme.get("event_count_30d") or 0),
            "event_count_90d": int(theme.get("event_count_90d") or 0),
            "source_org_count_90d": int(theme.get("source_org_count_90d") or 0),
        },
        "explanation_root": {
            "level": "summary",
            "top_reasons": _top_reasons(theme, event_breakdowns),
            "score_formula": formula_check,
            "contribution_check": contribution_check,
        },
        "trace_graph": trace_graph,
        "top_policy_paths": policy_paths[:10],
        "policy_paths": policy_paths,
        "event_breakdowns": event_breakdowns,
        "validation": {
            "graph": validation,
            "contribution": contribution_check,
            "mainline_formula": formula_check,
        },
        "levels": {
            "level_1_summary": "why_this_theme",
            "level_2_event": "event_breakdowns",
            "level_3_policy": "policy_paths",
            "level_4_formula": "validation.mainline_formula",
        },
    }


def build_all_theme_explanations(report: dict[str, Any]) -> dict[str, Any]:
    themes = list((report.get("theme_summary") or {}).get("themes") or report.get("mainline_ranking") or [])
    explanations = [build_theme_explanation(report, _theme_id(theme)) for theme in themes if isinstance(theme, dict)]
    return {
        "scoring_version": SCORING_VERSION,
        "report_id": report.get("report_id", ""),
        "theme_count": len(explanations),
        "themes": explanations,
    }
