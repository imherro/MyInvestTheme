from __future__ import annotations

import argparse
import json
import math
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    from canonical_mainline import build_mainline_ranking, sort_mainline_ranking
except ModuleNotFoundError:
    from scripts.canonical_mainline import build_mainline_ranking, sort_mainline_ranking


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "research" / "mainline"
RULES_PATH = ROOT / "config" / "mainline_contract_rules.json"
TZ = ZoneInfo("Asia/Shanghai")

SCORING_VERSION = "mainline_contract_validator_v2"
SELF_SECTION = "contract_validation_summary"
MISSING = object()


def round4(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return round(number, 4)


def approx_equal(left: Any, right: Any, tolerance: float = 0.0001) -> bool:
    return abs(round4(left) - round4(right)) <= tolerance


def get_path(data: Any, path: str, default: Any = MISSING) -> Any:
    current = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        if isinstance(current, list):
            try:
                current = current[int(part)]
                continue
            except (ValueError, IndexError):
                pass
        return default
    return current


def add_issue(
    issues: list[dict[str, Any]],
    severity: str,
    code: str,
    path: str,
    message: str,
    expected: Any = None,
    actual: Any = None,
) -> None:
    issues.append(
        {
            "severity": severity,
            "code": code,
            "path": path,
            "message": message,
            "expected": expected,
            "actual": actual,
        }
    )


def load_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _theme_key(row: dict[str, Any]) -> str:
    return str(row.get("theme_id") or row.get("theme_name") or row.get("theme") or "")


def _event_key(row: dict[str, Any]) -> str:
    return str(row.get("event_cluster_id") or "")


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _same_legacy_rows(theme_ranking: list[dict[str, Any]], legacy_ranking: list[dict[str, Any]]) -> bool:
    def normalized(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for row in rows:
            item = dict(row)
            item.pop("rank", None)
            item.pop("legacy_status", None)
            result.append(item)
        return result

    return normalized(theme_ranking) == normalized(legacy_ranking)


def _issue_counts(issues: list[dict[str, Any]]) -> tuple[int, int]:
    errors = sum(1 for issue in issues if issue.get("severity") == "error")
    warnings = sum(1 for issue in issues if issue.get("severity") == "warning")
    return errors, warnings


def validate_required_sections(
    report: dict[str, Any],
    rules: dict[str, Any],
    issues: list[dict[str, Any]],
    *,
    require_self_section: bool = False,
) -> None:
    for section in rules.get("required_sections", []):
        if section == SELF_SECTION and not require_self_section:
            continue
        value = report.get(section, MISSING)
        if value is MISSING:
            add_issue(
                issues,
                "error",
                "MISSING_REQUIRED_SECTION",
                section,
                "Required report section is missing.",
                expected="present",
                actual="missing",
            )


def validate_version_contract(report: dict[str, Any], rules: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    for path, expected in (rules.get("version_contract") or {}).items():
        actual = get_path(report, path)
        if actual is MISSING:
            add_issue(
                issues,
                "error",
                "MISSING_VERSION_FIELD",
                path,
                "Version field is missing.",
                expected=expected,
                actual="missing",
            )
        elif actual != expected:
            add_issue(
                issues,
                "error",
                "VERSION_MISMATCH",
                path,
                "Version field does not match the contract.",
                expected=expected,
                actual=actual,
            )


def validate_canonical_contract(report: dict[str, Any], rules: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    theme_summary = _as_dict(report.get("theme_summary"))
    themes = _as_list(theme_summary.get("themes"))
    mainline_ranking = _as_list(report.get("mainline_ranking"))
    canonical_summary = _as_dict(report.get("canonical_mainline_summary"))
    default_score_field = rules.get("default_score_field", "mainline_score_v6")

    if canonical_summary.get("default_score_field") != default_score_field:
        add_issue(
            issues,
            "error",
            "CANONICAL_DEFAULT_FIELD_MISMATCH",
            "canonical_mainline_summary.default_score_field",
            "Canonical default score field must be mainline_score_v6.",
            expected=default_score_field,
            actual=canonical_summary.get("default_score_field"),
        )

    if themes and mainline_ranking and _theme_key(themes[0]) != _theme_key(mainline_ranking[0]):
        add_issue(
            issues,
            "error",
            "MAINLINE_TOP_NOT_THEME_SUMMARY_TOP",
            "mainline_ranking.0",
            "mainline_ranking[0] must match theme_summary.themes[0].",
            expected=_theme_key(themes[0]),
            actual=_theme_key(mainline_ranking[0]),
        )

    if themes and mainline_ranking:
        expected_order = [_theme_key(row) for row in build_mainline_ranking(theme_summary)]
        actual_order = [_theme_key(row) for row in mainline_ranking]
        if expected_order != actual_order:
            add_issue(
                issues,
                "error",
                "MAINLINE_RANKING_ORDER_MISMATCH",
                "mainline_ranking",
                "mainline_ranking must be sorted by canonical mainline order.",
                expected=expected_order,
                actual=actual_order,
            )

    if mainline_ranking:
        sorted_order = [_theme_key(row) for row in sort_mainline_ranking(mainline_ranking)]
        actual_order = [_theme_key(row) for row in mainline_ranking]
        if sorted_order != actual_order:
            add_issue(
                issues,
                "error",
                "MAINLINE_RANKING_SORT_MISMATCH",
                "mainline_ranking",
                "mainline_ranking is not sorted by mainline_score_v6 and canonical tie-breakers.",
                expected=sorted_order,
                actual=actual_order,
            )
        top_summary = _as_dict(canonical_summary.get("top_mainline"))
        if _theme_key(top_summary) != _theme_key(mainline_ranking[0]):
            add_issue(
                issues,
                "error",
                "CANONICAL_TOP_MISMATCH",
                "canonical_mainline_summary.top_mainline",
                "Canonical top mainline must match mainline_ranking[0].",
                expected=_theme_key(mainline_ranking[0]),
                actual=_theme_key(top_summary),
            )


def validate_score_monotonicity(report: dict[str, Any], rules: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    tolerance = float(rules.get("tolerance", 0.0001))
    themes = _as_list(get_path(report, "theme_summary.themes", []))
    checks = [
        ("mainline_score_v6", "theme_score_v5"),
        ("theme_score_v5", "theme_score_v4_stance_adjusted"),
        ("theme_score_v4_stance_adjusted", "theme_score_v3_dedup"),
        ("theme_score_v3_dedup", "theme_score_v2_raw"),
    ]
    for index, theme in enumerate(themes):
        if not isinstance(theme, dict):
            continue
        for left_key, right_key in checks:
            left = round4(theme.get(left_key))
            right = round4(theme.get(right_key))
            if left > right + tolerance:
                add_issue(
                    issues,
                    "error",
                    "SCORE_MONOTONICITY_BROKEN",
                    f"theme_summary.themes.{index}.{left_key}",
                    f"{left_key} must not exceed {right_key}.",
                    expected=f"<= {right_key} ({right})",
                    actual=left,
                )


def validate_score_formulas(report: dict[str, Any], rules: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    tolerance = float(rules.get("tolerance", 0.0001))
    themes = _as_list(get_path(report, "theme_summary.themes", []))
    for index, theme in enumerate(themes):
        if not isinstance(theme, dict):
            continue
        path = f"theme_summary.themes.{index}"
        contributors = _as_list(theme.get("all_event_contributors"))
        if round4(theme.get("theme_score_v5")) > 0 and not contributors:
            add_issue(
                issues,
                "error",
                "MISSING_ALL_EVENT_CONTRIBUTORS",
                f"{path}.all_event_contributors",
                "Positive theme_score_v5 requires full all_event_contributors.",
                expected="non-empty list",
                actual="missing or empty",
            )
            contributors = _as_list(theme.get("top_event_contributors"))
        elif not contributors:
            contributors = _as_list(theme.get("top_event_contributors"))

        allocated_sum = round4(sum(round4(item.get("allocated_cluster_contribution")) for item in contributors if isinstance(item, dict)))
        raw_stance_sum = round4(
            sum(
                round4(item.get("raw_stance_adjusted_cluster_contribution", item.get("stance_adjusted_cluster_contribution")))
                for item in contributors
                if isinstance(item, dict)
            )
        )
        theme_score_v5 = round4(theme.get("theme_score_v5"))
        theme_score_v4 = round4(theme.get("theme_score_v4_stance_adjusted", theme.get("theme_score_v4")))
        theme_score_v3 = round4(theme.get("theme_score_v3_dedup", theme.get("theme_score_v3")))
        theme_score_v2 = round4(theme.get("theme_score_v2_raw"))
        multiplier = round4(theme.get("lifecycle_quality_multiplier"))
        expected_v6 = round4(theme_score_v5 * multiplier)
        expected_allocation_effect = round4(max(theme_score_v4 - theme_score_v5, 0.0))
        expected_stance_effect = round4(max(theme_score_v3 - theme_score_v4, 0.0))
        expected_dedup_effect = round4(max(theme_score_v2 - theme_score_v3, 0.0))

        if not approx_equal(theme_score_v5, allocated_sum, tolerance):
            add_issue(
                issues,
                "error",
                "THEME_SCORE_V5_FORMULA_MISMATCH",
                f"{path}.theme_score_v5",
                "theme_score_v5 must equal the sum of allocated cluster contributions.",
                expected=allocated_sum,
                actual=theme_score_v5,
            )
        if not approx_equal(theme_score_v4, raw_stance_sum, tolerance):
            add_issue(
                issues,
                "error",
                "THEME_SCORE_V4_FORMULA_MISMATCH",
                f"{path}.theme_score_v4_stance_adjusted",
                "theme_score_v4_stance_adjusted must equal raw stance-adjusted contribution sum.",
                expected=raw_stance_sum,
                actual=theme_score_v4,
            )
        if not approx_equal(theme.get("mainline_score_v6"), expected_v6, tolerance):
            add_issue(
                issues,
                "error",
                "MAINLINE_SCORE_V6_FORMULA_MISMATCH",
                f"{path}.mainline_score_v6",
                "mainline_score_v6 must equal theme_score_v5 * lifecycle_quality_multiplier.",
                expected=expected_v6,
                actual=round4(theme.get("mainline_score_v6")),
            )
        formula_checks = [
            ("allocation_adjustment_effect", expected_allocation_effect),
            ("stance_adjustment_effect", expected_stance_effect),
            ("deduplication_effect", expected_dedup_effect),
        ]
        for key, expected in formula_checks:
            if not approx_equal(theme.get(key), expected, tolerance):
                add_issue(
                    issues,
                    "error",
                    "SCORE_EFFECT_FORMULA_MISMATCH",
                    f"{path}.{key}",
                    f"{key} does not match the score formula.",
                    expected=expected,
                    actual=round4(theme.get(key)),
                )
        if not _as_list(theme.get("top_event_contributors")) and theme_score_v5 == 0:
            add_issue(
                issues,
                "warning",
                "ZERO_SCORE_WITHOUT_TOP_EVENT_CONTRIBUTORS",
                f"{path}.top_event_contributors",
                "Theme has zero score and no top event contributors.",
                expected="empty only for inactive themes",
                actual="empty",
            )


def validate_event_allocation_contract(report: dict[str, Any], rules: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    tolerance = float(rules.get("tolerance", 0.0001))
    allocation = _as_dict(report.get("event_theme_allocation_summary"))
    events = _as_list(allocation.get("events"))
    raw_total = 0.0
    allocated_total = 0.0
    for event_index, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        path = f"event_theme_allocation_summary.events.{event_index}"
        themes = _as_list(event.get("allocated_themes"))
        budget = round4(event.get("event_contribution_budget"))
        used = round4(event.get("allocation_budget_used"))
        raw_sum = round4(event.get("raw_contribution_sum_v4"))
        allocated_sum = round4(sum(round4(theme.get("allocated_cluster_contribution")) for theme in themes if isinstance(theme, dict)))
        raw_theme_sum = round4(sum(round4(theme.get("raw_stance_adjusted_cluster_contribution")) for theme in themes if isinstance(theme, dict)))
        raw_total += raw_sum
        allocated_total += allocated_sum

        if used > budget + tolerance:
            add_issue(
                issues,
                "error",
                "EVENT_BUDGET_OVERUSED",
                f"{path}.allocation_budget_used",
                "Event allocation budget used must not exceed event contribution budget.",
                expected=f"<= {budget}",
                actual=used,
            )
        if allocated_sum > budget + tolerance:
            add_issue(
                issues,
                "error",
                "EVENT_ALLOCATED_SUM_OVER_BUDGET",
                f"{path}.allocated_themes",
                "Sum of allocated theme contributions must not exceed event budget.",
                expected=f"<= {budget}",
                actual=allocated_sum,
            )
        if not approx_equal(used, allocated_sum, tolerance):
            add_issue(
                issues,
                "error",
                "EVENT_USED_NOT_ALLOCATED_SUM",
                f"{path}.allocation_budget_used",
                "Event allocation_budget_used must equal allocated theme contribution sum.",
                expected=allocated_sum,
                actual=used,
            )
        if not approx_equal(raw_sum, raw_theme_sum, tolerance):
            add_issue(
                issues,
                "error",
                "EVENT_RAW_SUM_MISMATCH",
                f"{path}.raw_contribution_sum_v4",
                "Event raw contribution sum must equal the raw theme contribution sum.",
                expected=raw_theme_sum,
                actual=raw_sum,
            )

        expected_reduction = round4(max(raw_sum - used, 0.0))
        if not approx_equal(event.get("allocation_reduction_effect"), expected_reduction, tolerance):
            add_issue(
                issues,
                "error",
                "EVENT_ALLOCATION_REDUCTION_MISMATCH",
                f"{path}.allocation_reduction_effect",
                "Event allocation reduction effect does not match formula.",
                expected=expected_reduction,
                actual=round4(event.get("allocation_reduction_effect")),
            )

        capped = bool(event.get("allocation_capped"))
        if capped:
            if not approx_equal(used, budget, tolerance) or raw_sum <= budget + tolerance:
                add_issue(
                    issues,
                    "error",
                    "CAPPED_EVENT_CONTRACT_BROKEN",
                    path,
                    "Capped events must use the full budget and have raw contribution above budget.",
                    expected={"used": budget, "raw_sum_gt_budget": True},
                    actual={"used": used, "raw_sum": raw_sum, "budget": budget},
                )
        elif not approx_equal(used, raw_sum, tolerance) or raw_sum > budget + tolerance:
            add_issue(
                issues,
                "error",
                "UNCAPPED_EVENT_CONTRACT_BROKEN",
                path,
                "Uncapped events must use raw contribution and stay within budget.",
                expected={"used": raw_sum, "raw_sum_lte_budget": True},
                actual={"used": used, "raw_sum": raw_sum, "budget": budget},
            )

        for theme_index, theme in enumerate(themes):
            if not isinstance(theme, dict):
                continue
            theme_path = f"{path}.allocated_themes.{theme_index}"
            raw = round4(theme.get("raw_stance_adjusted_cluster_contribution"))
            allocated = round4(theme.get("allocated_cluster_contribution"))
            share = round4(theme.get("allocation_share"))
            expected_theme_reduction = round4(max(raw - allocated, 0.0))
            if allocated > raw + tolerance:
                add_issue(
                    issues,
                    "error",
                    "THEME_ALLOCATED_OVER_RAW",
                    f"{theme_path}.allocated_cluster_contribution",
                    "Allocated contribution must not exceed raw stance-adjusted contribution.",
                    expected=f"<= {raw}",
                    actual=allocated,
                )
            if not approx_equal(theme.get("theme_allocation_reduction_effect"), expected_theme_reduction, tolerance):
                add_issue(
                    issues,
                    "error",
                    "THEME_ALLOCATION_REDUCTION_MISMATCH",
                    f"{theme_path}.theme_allocation_reduction_effect",
                    "Theme allocation reduction effect does not match formula.",
                    expected=expected_theme_reduction,
                    actual=round4(theme.get("theme_allocation_reduction_effect")),
                )
            if share < -tolerance or share > 1.0 + tolerance:
                add_issue(
                    issues,
                    "error",
                    "ALLOCATION_SHARE_OUT_OF_RANGE",
                    f"{theme_path}.allocation_share",
                    "Allocation share must be between 0 and 1.",
                    expected="0 <= allocation_share <= 1",
                    actual=share,
                )

    if events:
        expected_reduction_total = round4(max(raw_total - allocated_total, 0.0))
        summary_checks = [
            ("raw_contribution_total_v4", raw_total),
            ("allocated_contribution_total_v5", allocated_total),
            ("allocation_reduction_effect", expected_reduction_total),
        ]
        for key, expected in summary_checks:
            if not approx_equal(allocation.get(key), expected, tolerance):
                add_issue(
                    issues,
                    "error",
                    "ALLOCATION_SUMMARY_TOTAL_MISMATCH",
                    f"event_theme_allocation_summary.{key}",
                    f"{key} does not match event totals.",
                    expected=round4(expected),
                    actual=round4(allocation.get(key)),
                )


def validate_lifecycle_contract(report: dict[str, Any], rules: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    tolerance = float(rules.get("tolerance", 0.0001))
    valid_states = set(rules.get("valid_lifecycle_states") or [])
    themes = _as_list(get_path(report, "theme_summary.themes", []))
    lifecycle_summary = _as_dict(report.get("mainline_lifecycle_summary"))
    state_counts = {state: 0 for state in valid_states}
    for index, theme in enumerate(themes):
        if not isinstance(theme, dict):
            continue
        path = f"theme_summary.themes.{index}"
        state = str(theme.get("lifecycle_state") or "")
        if state not in valid_states:
            add_issue(
                issues,
                "error",
                "INVALID_LIFECYCLE_STATE",
                f"{path}.lifecycle_state",
                "Lifecycle state is not in the allowed state list.",
                expected=sorted(valid_states),
                actual=state,
            )
        else:
            state_counts[state] += 1
        expected_90d = round4(round4(theme.get("score_30d")) + round4(theme.get("score_31_60d")) + round4(theme.get("score_61_90d")))
        if not approx_equal(theme.get("score_90d"), expected_90d, tolerance):
            add_issue(
                issues,
                "error",
                "LIFECYCLE_SCORE_90D_MISMATCH",
                f"{path}.score_90d",
                "score_90d must equal score_30d + score_31_60d + score_61_90d.",
                expected=expected_90d,
                actual=round4(theme.get("score_90d")),
            )
        expected_persistence = round4(_int(theme.get("active_window_count")) / 3)
        if not approx_equal(theme.get("persistence_score"), expected_persistence, tolerance):
            add_issue(
                issues,
                "error",
                "PERSISTENCE_SCORE_MISMATCH",
                f"{path}.persistence_score",
                "persistence_score must equal active_window_count / 3.",
                expected=expected_persistence,
                actual=round4(theme.get("persistence_score")),
            )
        if state and not _as_list(theme.get("lifecycle_reasons")):
            add_issue(
                issues,
                "warning",
                "LIFECYCLE_REASONS_EMPTY",
                f"{path}.lifecycle_reasons",
                "Lifecycle state is present but lifecycle_reasons is empty.",
                expected="non-empty list",
                actual="empty",
            )

    for state in sorted(valid_states):
        summary_key = f"{state}_count"
        expected = state_counts.get(state, 0)
        actual = _int(lifecycle_summary.get(summary_key))
        if actual != expected:
            add_issue(
                issues,
                "error",
                "LIFECYCLE_STATE_COUNT_MISMATCH",
                f"mainline_lifecycle_summary.{summary_key}",
                "Lifecycle state count must match theme_summary.themes.",
                expected=expected,
                actual=actual,
            )


def validate_counts_contract(report: dict[str, Any], rules: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    themes = _as_list(get_path(report, "theme_summary.themes", []))
    mainline_ranking = _as_list(report.get("mainline_ranking"))
    canonical_summary = _as_dict(report.get("canonical_mainline_summary"))
    lifecycle_summary = _as_dict(report.get("mainline_lifecycle_summary"))
    event_cluster = _as_dict(report.get("event_cluster_summary"))
    allocation = _as_dict(report.get("event_theme_allocation_summary"))
    events = _as_list(allocation.get("events"))

    count_checks = [
        ("canonical_mainline_summary.theme_count", canonical_summary.get("theme_count"), len(mainline_ranking)),
        ("mainline_lifecycle_summary.theme_count", lifecycle_summary.get("theme_count"), len(themes)),
    ]
    for path, actual, expected in count_checks:
        if _int(actual) != expected:
            add_issue(
                issues,
                "error",
                "COUNT_CONTRACT_MISMATCH",
                path,
                "Summary count does not match source rows.",
                expected=expected,
                actual=_int(actual),
            )

    raw_policy_count = _int(event_cluster.get("raw_policy_count"))
    cluster_count = _int(event_cluster.get("cluster_count"))
    expected_dedup = raw_policy_count - cluster_count
    if _int(event_cluster.get("deduplicated_policy_count")) != expected_dedup:
        add_issue(
            issues,
            "error",
            "EVENT_DEDUP_COUNT_MISMATCH",
            "event_cluster_summary.deduplicated_policy_count",
            "deduplicated_policy_count must equal raw_policy_count - cluster_count.",
            expected=expected_dedup,
            actual=_int(event_cluster.get("deduplicated_policy_count")),
        )

    if events:
        claim_count = sum(len(_as_list(event.get("allocated_themes"))) for event in events if isinstance(event, dict))
        multi_count = sum(1 for event in events if isinstance(event, dict) and _int(event.get("matched_theme_count")) > 1)
        capped_count = sum(1 for event in events if isinstance(event, dict) and bool(event.get("allocation_capped")))
        allocation_checks = [
            ("event_cluster_count", len(events)),
            ("event_theme_claim_count", claim_count),
            ("multi_theme_event_count", multi_count),
            ("capped_event_count", capped_count),
        ]
        for key, expected in allocation_checks:
            actual = _int(allocation.get(key))
            if actual != expected:
                add_issue(
                    issues,
                    "error",
                    "ALLOCATION_COUNT_MISMATCH",
                    f"event_theme_allocation_summary.{key}",
                    f"{key} does not match allocation events.",
                    expected=expected,
                    actual=actual,
                )


def validate_no_legacy_default_leak(report: dict[str, Any], rules: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    default_score_field = rules.get("default_score_field", "mainline_score_v6")
    canonical_summary = _as_dict(report.get("canonical_mainline_summary"))
    mainline_ranking = _as_list(report.get("mainline_ranking"))
    legacy_ranking = _as_list(report.get("legacy_theme_ranking"))
    theme_ranking = _as_list(report.get("theme_ranking"))

    if canonical_summary.get("default_score_field") != default_score_field:
        add_issue(
            issues,
            "error",
            "LEGACY_DEFAULT_FIELD_LEAK",
            "canonical_mainline_summary.default_score_field",
            "Default score field must not use legacy evidence fields.",
            expected=default_score_field,
            actual=canonical_summary.get("default_score_field"),
        )

    evidence_sorted = sorted(
        mainline_ranking,
        key=lambda row: (-round4(row.get("evidence_score")), _theme_key(row)),
    )
    if mainline_ranking and any("evidence_score" in row for row in mainline_ranking):
        actual_order = [_theme_key(row) for row in mainline_ranking]
        evidence_order = [_theme_key(row) for row in evidence_sorted]
        if actual_order == evidence_order:
            add_issue(
                issues,
                "error",
                "MAINLINE_RANKING_USES_LEGACY_EVIDENCE",
                "mainline_ranking",
                "mainline_ranking must not use legacy evidence_score as the default sort key.",
                expected=default_score_field,
                actual="evidence_score order",
            )

    if legacy_ranking:
        add_issue(
            issues,
            "warning",
            "LEGACY_THEME_RANKING_PRESENT",
            "legacy_theme_ranking",
            "legacy_theme_ranking is present for backward-readable market context.",
            expected="legacy only",
            actual="present",
        )
        for index, row in enumerate(legacy_ranking):
            if isinstance(row, dict) and not row.get("legacy_status"):
                add_issue(
                    issues,
                    "error",
                    "LEGACY_ROW_STATUS_MISSING",
                    f"legacy_theme_ranking.{index}.legacy_status",
                    "Legacy ranking rows must be marked as legacy status.",
                    expected="present",
                    actual="missing",
                )

    if theme_ranking:
        if legacy_ranking and _same_legacy_rows(theme_ranking, legacy_ranking):
            add_issue(
                issues,
                "warning",
                "THEME_RANKING_LEGACY_COMPAT",
                "theme_ranking",
                "theme_ranking is retained only as legacy-compatible market context.",
                expected="legacy-compatible",
                actual="present",
            )
        else:
            all_marked = all(isinstance(row, dict) and row.get("legacy_status") for row in theme_ranking)
            if not all_marked:
                add_issue(
                    issues,
                    "error",
                    "THEME_RANKING_UNMARKED_LEGACY",
                    "theme_ranking",
                    "theme_ranking must be equal to legacy_theme_ranking or clearly marked legacy.",
                    expected="legacy-compatible or marked",
                    actual="unmarked",
                )


def validate_data_quality_contract(report: dict[str, Any], rules: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    summary = report.get("data_quality_summary")
    if not isinstance(summary, dict) or not summary:
        add_issue(
            issues,
            "warning",
            "DATA_QUALITY_SUMMARY_MISSING",
            "data_quality_summary",
            "Older report has no data_quality_summary.",
            expected="present for new reports",
            actual="missing",
        )
        return

    expected_version = rules.get("data_quality_scoring_version", "live_report_data_guard_v2")
    if summary.get("scoring_version") != expected_version:
        add_issue(
            issues,
            "error",
            "DATA_QUALITY_VERSION_MISMATCH",
            "data_quality_summary.scoring_version",
            "data_quality_summary scoring version mismatch.",
            expected=expected_version,
            actual=summary.get("scoring_version"),
        )

    allowed_statuses = set(rules.get("data_quality_status_values") or ["pass", "degraded", "fail"])
    if summary.get("status") not in allowed_statuses:
        add_issue(
            issues,
            "error",
            "DATA_QUALITY_STATUS_INVALID",
            "data_quality_summary.status",
            "data_quality_summary status is invalid.",
            expected=sorted(allowed_statuses),
            actual=summary.get("status"),
        )

    required_failure_count = _int(summary.get("required_failure_count"))
    if required_failure_count > 0:
        add_issue(
            issues,
            "error",
            "DATA_QUALITY_REQUIRED_FAILURE",
            "data_quality_summary.required_failure_count",
            "Required data quality stage failed.",
            expected=0,
            actual=required_failure_count,
        )

    optional_failure_count = _int(summary.get("optional_failure_count"))
    if optional_failure_count > 0:
        add_issue(
            issues,
            "warning",
            "DATA_QUALITY_OPTIONAL_DEGRADED",
            "data_quality_summary.optional_failure_count",
            "Optional market-context data stage degraded; canonical mainline score is not adjusted.",
            expected=0,
            actual=optional_failure_count,
        )

    stage_statuses = _as_list(summary.get("stage_statuses"))
    stages_by_name = {str(status.get("stage")): status for status in stage_statuses if isinstance(status, dict)}
    for stage in rules.get("data_quality_required_stages") or []:
        status = stages_by_name.get(str(stage))
        if not status:
            add_issue(
                issues,
                "error",
                "DATA_QUALITY_REQUIRED_STAGE_MISSING",
                f"data_quality_summary.stage_statuses.{stage}",
                "Required data quality stage is missing from stage_statuses.",
                expected={"stage": stage, "required": True},
                actual="missing",
            )
            continue
        if not status.get("required"):
            add_issue(
                issues,
                "error",
                "DATA_QUALITY_REQUIRED_STAGE_NOT_REQUIRED",
                f"data_quality_summary.stage_statuses.{stage}.required",
                "Required data quality stage must be marked required.",
                expected=True,
                actual=status.get("required"),
            )


def _find_policy_id_leaks(value: Any, rejected_ids: set[str], path: str) -> list[dict[str, Any]]:
    leaks: list[dict[str, Any]] = []
    policy_id_fields = {
        "policy_id",
        "primary_policy_id",
        "selected_relevance_policy_id",
        "selected_stance_policy_id",
    }
    policy_id_list_fields = {"member_policy_ids", "included_policy_ids"}
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if key in policy_id_fields:
                item_id = str(item or "")
                if item_id in rejected_ids:
                    leaks.append({"path": child_path, "policy_id": item_id})
            elif key in policy_id_list_fields and isinstance(item, list):
                for index, member_id in enumerate(item):
                    item_id = str(member_id or "")
                    if item_id in rejected_ids:
                        leaks.append({"path": f"{child_path}.{index}", "policy_id": item_id})
            leaks.extend(_find_policy_id_leaks(item, rejected_ids, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            leaks.extend(_find_policy_id_leaks(item, rejected_ids, f"{path}.{index}" if path else str(index)))
    return leaks


def validate_policy_provenance_contract(report: dict[str, Any], rules: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    summary = report.get("policy_provenance_summary")
    if not isinstance(summary, dict) or not summary:
        add_issue(
            issues,
            "error",
            "POLICY_PROVENANCE_SUMMARY_MISSING",
            "policy_provenance_summary",
            "New reports must include policy_provenance_summary.",
            expected="present",
            actual="missing",
        )
        return

    expected_version = "policy_source_provenance_v2"
    if summary.get("scoring_version") != expected_version:
        add_issue(
            issues,
            "error",
            "POLICY_PROVENANCE_VERSION_MISMATCH",
            "policy_provenance_summary.scoring_version",
            "policy_provenance_summary scoring version mismatch.",
            expected=expected_version,
            actual=summary.get("scoring_version"),
        )

    raw_count = _int(summary.get("raw_policy_count"))
    included_count = _int(summary.get("included_policy_count"))
    excluded_count = _int(summary.get("excluded_policy_count"))
    rejected_count = _int(summary.get("rejected_count"))
    degraded_count = _int(summary.get("degraded_count"))
    if raw_count != included_count + excluded_count:
        add_issue(
            issues,
            "error",
            "POLICY_PROVENANCE_COUNT_MISMATCH",
            "policy_provenance_summary.raw_policy_count",
            "raw_policy_count must equal included_policy_count + excluded_policy_count.",
            expected=included_count + excluded_count,
            actual=raw_count,
        )
    if rejected_count != excluded_count:
        add_issue(
            issues,
            "error",
            "POLICY_REJECTED_EXCLUDED_COUNT_MISMATCH",
            "policy_provenance_summary.rejected_count",
            "rejected_count must equal excluded_policy_count.",
            expected=excluded_count,
            actual=rejected_count,
        )

    policy_summary = _as_dict(report.get("policy_summary"))
    if _int(policy_summary.get("signals_count")) != included_count:
        add_issue(
            issues,
            "error",
            "POLICY_SUMMARY_INCLUDED_COUNT_MISMATCH",
            "policy_summary.signals_count",
            "policy_summary.signals_count must equal policy_provenance_summary.included_policy_count.",
            expected=included_count,
            actual=_int(policy_summary.get("signals_count")),
        )

    if degraded_count > 0:
        add_issue(
            issues,
            "warning",
            "POLICY_PROVENANCE_DEGRADED_PRESENT",
            "policy_provenance_summary.degraded_count",
            "Some policies are included with degraded provenance.",
            expected=0,
            actual=degraded_count,
        )
    if rejected_count > 0:
        add_issue(
            issues,
            "warning",
            "POLICY_PROVENANCE_REJECTED_EXCLUDED",
            "policy_provenance_summary.rejected_count",
            "Rejected policies are present in the raw store and must remain excluded from scoring.",
            expected=0,
            actual=rejected_count,
        )

    rejected_ids = {str(item) for item in summary.get("excluded_policy_ids") or [] if str(item)}
    for row in _as_list(summary.get("excluded_policies")):
        if isinstance(row, dict) and row.get("policy_id"):
            rejected_ids.add(str(row.get("policy_id")))
    if not rejected_ids:
        return
    checked_sections = {
        "theme_summary": report.get("theme_summary"),
        "event_cluster_summary": report.get("event_cluster_summary"),
        "policy_stance_summary": report.get("policy_stance_summary"),
        "event_theme_allocation_summary": report.get("event_theme_allocation_summary"),
        "mainline_lifecycle_summary": report.get("mainline_lifecycle_summary"),
        "mainline_ranking": report.get("mainline_ranking"),
        "theme_ranking": report.get("theme_ranking"),
        "legacy_theme_ranking": report.get("legacy_theme_ranking"),
    }
    leaks: list[dict[str, Any]] = []
    for section, value in checked_sections.items():
        leaks.extend(_find_policy_id_leaks(value, rejected_ids, section))
    if leaks:
        add_issue(
            issues,
            "error",
            "REJECTED_POLICY_USED_IN_MAINLINE",
            leaks[0]["path"],
            "Rejected policy appeared in a mainline scoring section.",
            expected="no rejected policy IDs in scoring sections",
            actual=leaks[:10],
        )


def validate_mainline_report_contract(
    report: dict[str, Any],
    rules: dict[str, Any] | None = None,
    *,
    checked_at: str | None = None,
    require_self_section: bool = False,
) -> dict[str, Any]:
    active_rules = rules or load_rules()
    issues: list[dict[str, Any]] = []
    checked_sections = {
        "required_sections": True,
        "version_contract": True,
        "canonical_contract": True,
        "score_monotonicity": True,
        "score_formulas": True,
        "event_allocation_contract": True,
        "lifecycle_contract": True,
        "counts_contract": True,
        "legacy_default_leak": True,
        "data_quality_contract": True,
        "policy_provenance_contract": True,
    }
    validate_required_sections(report, active_rules, issues, require_self_section=require_self_section)
    validate_version_contract(report, active_rules, issues)
    validate_canonical_contract(report, active_rules, issues)
    validate_score_monotonicity(report, active_rules, issues)
    validate_score_formulas(report, active_rules, issues)
    validate_event_allocation_contract(report, active_rules, issues)
    validate_lifecycle_contract(report, active_rules, issues)
    validate_counts_contract(report, active_rules, issues)
    validate_no_legacy_default_leak(report, active_rules, issues)
    validate_data_quality_contract(report, active_rules, issues)
    validate_policy_provenance_contract(report, active_rules, issues)

    error_count, warning_count = _issue_counts(issues)
    return {
        "scoring_version": SCORING_VERSION,
        "status": "fail" if error_count else "pass",
        "error_count": error_count,
        "warning_count": warning_count,
        "checked_at": checked_at or datetime.now(TZ).isoformat(timespec="seconds"),
        "checked_sections": checked_sections,
        "issues": issues,
    }


def assert_mainline_report_contract(
    report: dict[str, Any],
    rules: dict[str, Any] | None = None,
    *,
    checked_at: str | None = None,
    require_self_section: bool = False,
) -> dict[str, Any]:
    summary = validate_mainline_report_contract(
        report,
        rules,
        checked_at=checked_at,
        require_self_section=require_self_section,
    )
    if summary["error_count"]:
        codes = ", ".join(issue["code"] for issue in summary["issues"] if issue["severity"] == "error")
        raise RuntimeError(f"Mainline report contract failed: {codes}")
    return summary


def latest_report_path() -> Path:
    files = sorted(REPORT_DIR.glob("mainline_review_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError("No mainline report JSON files found.")
    return files[0]


def load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _cli_summary(summary: dict[str, Any], path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "scoring_version": summary["scoring_version"],
        "status": summary["status"],
        "error_count": summary["error_count"],
        "warning_count": summary["warning_count"],
        "issues": summary["issues"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate A-share mainline report contract.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--latest", action="store_true", help="Validate the newest research/mainline JSON report.")
    group.add_argument("--path", type=Path, help="Validate a specific report JSON path.")
    parser.add_argument("--require-embedded-summary", action="store_true", help="Require contract_validation_summary in the JSON.")
    args = parser.parse_args(argv)

    path = latest_report_path() if args.latest else args.path
    if path is None:
        parser.error("--path is required unless --latest is set")
    report = load_report(path)
    if not args.require_embedded_summary:
        report = deepcopy(report)
        report.setdefault(SELF_SECTION, {})
    summary = validate_mainline_report_contract(
        report,
        require_self_section=args.require_embedded_summary,
    )
    print(json.dumps(_cli_summary(summary, path), ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
