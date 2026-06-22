from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from .theme_explanation_engine import (
        ThemeExplanationNotFound,
        build_theme_explanation,
        latest_report_path,
        load_report,
    )
except ImportError:
    try:
        from theme_explanation_engine import (
            ThemeExplanationNotFound,
            build_theme_explanation,
            latest_report_path,
            load_report,
        )
    except ModuleNotFoundError:
        from scripts.theme_explanation_engine import (
            ThemeExplanationNotFound,
            build_theme_explanation,
            latest_report_path,
            load_report,
        )


def _line(text: str = "") -> None:
    print(text)


def print_text_summary(explanation: dict[str, Any]) -> None:
    _line(f"THEME: {explanation.get('theme_name')} ({explanation.get('theme_id')})")
    _line(f"REPORT: {explanation.get('report_id')}")
    _line(f"SCORE: mainline_score_v6={explanation.get('mainline_score_v6')} theme_score_v5={explanation.get('theme_score_v5')}")
    _line()
    _line("TOP DRIVERS:")
    paths = explanation.get("top_policy_paths") or []
    if not paths:
        _line("- no policy path")
    for path in paths[:5]:
        _line(
            "- policy "
            f"{path.get('policy_id')} -> event {path.get('event_cluster_id')} "
            f"-> +{path.get('path_contribution')}"
        )
    _line()
    _line("TOP REASONS:")
    for reason in (explanation.get("explanation_root") or {}).get("top_reasons") or []:
        _line(f"- {reason}")
    _line()
    contribution = (explanation.get("validation") or {}).get("contribution") or {}
    formula = (explanation.get("validation") or {}).get("mainline_formula") or {}
    _line(
        "CHECKS: "
        f"contribution={contribution.get('status')} "
        f"formula={formula.get('status')} "
        f"graph={(explanation.get('validation') or {}).get('graph', {}).get('status')}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build explainability trace graph for a mainline theme.")
    parser.add_argument("--theme", required=True, help="Theme id or theme name.")
    parser.add_argument("--latest", action="store_true", help="Use latest report.")
    parser.add_argument("--path", type=Path, help="Report JSON path.")
    parser.add_argument("--json", action="store_true", help="Print full JSON explanation.")
    args = parser.parse_args(argv)

    path = latest_report_path() if args.latest or not args.path else args.path
    report = load_report(path)
    try:
        explanation = build_theme_explanation(report, args.theme)
    except ThemeExplanationNotFound:
        print(f"Theme not found: {args.theme}")
        return 2
    if args.json:
        print(json.dumps(explanation, ensure_ascii=False, indent=2))
    else:
        print_text_summary(explanation)
    return 0 if explanation.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
