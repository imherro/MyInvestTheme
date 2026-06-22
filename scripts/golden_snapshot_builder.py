from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    from reproducibility_manifest import stable_json_hash
except ModuleNotFoundError:
    from scripts.reproducibility_manifest import stable_json_hash


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "research" / "mainline"
RULES_PATH = ROOT / "config" / "system_drift_rules.json"
GOLDEN_PATH = ROOT / "data" / "golden_mainline_snapshot.json"
SCORING_VERSION = "system_drift_control_v2"
TZ = ZoneInfo("Asia/Shanghai")


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def load_drift_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def latest_report_path() -> Path:
    files = sorted(REPORT_DIR.glob("mainline_review_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError("No mainline report JSON files found.")
    return files[0]


def load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _round(value: Any) -> float:
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return 0.0


def _theme_id(row: dict[str, Any]) -> str:
    return str(row.get("theme_id") or row.get("theme_name") or row.get("theme") or "")


def build_theme_scores(report: dict[str, Any]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for row in report.get("mainline_ranking") or []:
        if not isinstance(row, dict):
            continue
        theme_id = _theme_id(row)
        result[theme_id] = {
            "rank": int(row.get("rank") or len(result) + 1),
            "mainline_score_v6": _round(row.get("mainline_score_v6")),
            "theme_score_v5": _round(row.get("theme_score_v5")),
            "theme_score_v4_stance_adjusted": _round(row.get("theme_score_v4_stance_adjusted", row.get("theme_score_v4"))),
            "theme_score_v3_dedup": _round(row.get("theme_score_v3_dedup", row.get("theme_score_v3"))),
            "theme_score_v2_raw": _round(row.get("theme_score_v2_raw")),
        }
    return result


def build_lifecycle_states(report: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in report.get("mainline_ranking") or []:
        if isinstance(row, dict):
            result[_theme_id(row)] = str(row.get("lifecycle_state") or "")
    return result


def build_allocation_matrix(report: dict[str, Any]) -> dict[str, dict[str, float]]:
    matrix: dict[str, dict[str, float]] = {}
    events = ((report.get("event_theme_allocation_summary") or {}).get("events") or [])
    for event in events:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("event_cluster_id") or "")
        if not event_id:
            continue
        matrix[event_id] = {}
        for theme in event.get("allocated_themes") or []:
            if not isinstance(theme, dict):
                continue
            theme_id = str(theme.get("theme_id") or "")
            if theme_id:
                matrix[event_id][theme_id] = _round(theme.get("allocated_cluster_contribution"))
    return {event_id: dict(sorted(themes.items())) for event_id, themes in sorted(matrix.items())}


def build_stance_matrix(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for theme in (report.get("theme_summary") or {}).get("themes") or []:
        if not isinstance(theme, dict):
            continue
        theme_id = _theme_id(theme)
        result[theme_id] = {}
        for contributor in theme.get("all_event_contributors") or theme.get("top_event_contributors") or []:
            if not isinstance(contributor, dict):
                continue
            event_id = str(contributor.get("event_cluster_id") or "")
            if event_id:
                result[theme_id][event_id] = {
                    "cluster_stance_label": contributor.get("cluster_stance_label", ""),
                    "cluster_stance_score_v2": _round(contributor.get("cluster_stance_score_v2")),
                    "direction_multiplier": _round(contributor.get("direction_multiplier")),
                }
    return {theme_id: dict(sorted(events.items())) for theme_id, events in sorted(result.items())}


def build_golden_snapshot(report: dict[str, Any], snapshot_id: str = "mainline_gold_v1") -> dict[str, Any]:
    mainline_ranking = [
        {
            "rank": int(row.get("rank") or index + 1),
            "theme_id": _theme_id(row),
            "theme_name": row.get("theme_name", ""),
            "mainline_score_v6": _round(row.get("mainline_score_v6")),
            "lifecycle_state": row.get("lifecycle_state", ""),
        }
        for index, row in enumerate(report.get("mainline_ranking") or [])
        if isinstance(row, dict)
    ]
    snapshot = {
        "scoring_version": SCORING_VERSION,
        "snapshot_id": snapshot_id,
        "created_at": report.get("generated_at_iso") or report.get("generated_at") or now_iso(),
        "source_report_id": report.get("report_id", ""),
        "basis_date": report.get("basis_date", ""),
        "source_reproducibility_json_hash": ((report.get("reproducibility_manifest") or {}).get("artifact_fingerprints") or {}).get(
            "json_report",
            {},
        ).get("sha256", ""),
        "source_git_commit": ((report.get("reproducibility_manifest") or {}).get("git") or {}).get("commit", ""),
        "mainline_ranking": mainline_ranking,
        "theme_scores": build_theme_scores(report),
        "lifecycle_states": build_lifecycle_states(report),
        "allocation_matrix": build_allocation_matrix(report),
        "stance_matrix": build_stance_matrix(report),
        "report_sections": sorted(report.keys()),
    }
    snapshot["snapshot_hash"] = stable_json_hash({**snapshot, "snapshot_hash": "sha256:SELF"})
    return snapshot


def write_golden_snapshot(snapshot: dict[str, Any], path: Path = GOLDEN_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build golden mainline drift snapshot.")
    parser.add_argument("--latest", action="store_true", help="Use latest report.")
    parser.add_argument("--path", type=Path, help="Report JSON path.")
    parser.add_argument("--snapshot-id", default="mainline_gold_v1")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    path = latest_report_path() if args.latest or not args.path else args.path
    report = load_report(path)
    snapshot = build_golden_snapshot(report, args.snapshot_id)
    if args.write:
        output = write_golden_snapshot(snapshot)
        print(output)
    else:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
