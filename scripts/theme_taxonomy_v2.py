from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "theme_taxonomy_v2.json"
REPORT_DIR = ROOT / "research" / "mainline"
OUTPUT_DIR = ROOT / "research" / "mainline_taxonomy_v2"
SCORING_VERSION = "theme_taxonomy_v2_backfill_v1"

FIELD_WEIGHTS = {
    "sw": 0.25,
    "ths": 0.30,
    "etf": 0.25,
    "limit": 0.10,
    "flow": 0.10,
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_taxonomy(path: Path = CONFIG_PATH) -> dict[str, Any]:
    return _read_json(path)


def report_json_files(report_dir: Path = REPORT_DIR) -> list[Path]:
    return sorted(report_dir.glob("mainline_review_*.json"))


def latest_report_path(report_dir: Path = REPORT_DIR) -> Path:
    files = report_json_files(report_dir)
    if not files:
        raise FileNotFoundError(f"No mainline report JSON found under {report_dir}")
    return max(files, key=lambda path: path.stat().st_mtime)


def report_id_from_path(path: Path) -> str:
    return path.stem


def backfill_path(report_id: str, output_dir: Path = OUTPUT_DIR) -> Path:
    return output_dir / f"{report_id}.taxonomy_v2.json"


def _keywords(spec: dict[str, Any], field: str) -> list[str]:
    return [str(item) for item in spec.get(field) or [] if str(item)]


def _keyword_hits(text: str, keywords: list[str]) -> list[str]:
    if not text or not keywords:
        return []
    return [keyword for keyword in keywords if keyword and keyword in text]


def _record_text(record: dict[str, Any], fields: list[str]) -> str:
    values = []
    for field in fields:
        value = record.get(field)
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            values.append(json.dumps(value, ensure_ascii=False))
        else:
            values.append(str(value))
    return " ".join(values)


def _theme_name(row: dict[str, Any]) -> str:
    return str(row.get("theme_name") or row.get("theme") or "")


def _theme_id(row: dict[str, Any]) -> str:
    return str(row.get("theme_id") or _theme_name(row))


def _legacy_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("legacy_theme_ranking") or payload.get("theme_ranking") or []
    return [row for row in rows if isinstance(row, dict)]


def _mainline_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("mainline_ranking") or []
    return [row for row in rows if isinstance(row, dict)]


def _by_legacy_key(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        for key in (_theme_id(row), _theme_name(row), str(row.get("theme") or "")):
            if key:
                result[key] = row
    return result


def _max_mainline_score(payload: dict[str, Any]) -> float:
    scores = [_safe_float(row.get("mainline_score_v6")) for row in _mainline_rows(payload)]
    scores.extend(_safe_float(row.get("mainline_score_v6")) for row in _legacy_rows(payload))
    return max([score for score in scores if score > 0] or [1.0])


def _market_records(payload: dict[str, Any], collection: str) -> list[dict[str, Any]]:
    rows = payload.get(collection) or []
    return [row for row in rows if isinstance(row, dict)]


def _match_market_records(
    payload: dict[str, Any],
    collection: str,
    name_fields: list[str],
    keywords: list[str],
    score_field: str = "score",
    limit: int = 3,
) -> tuple[float, list[str], list[dict[str, Any]]]:
    matches: list[dict[str, Any]] = []
    hits: list[str] = []
    for record in _market_records(payload, collection):
        text = _record_text(record, name_fields)
        record_hits = _keyword_hits(text, keywords)
        if not record_hits:
            continue
        hits.extend(record_hits)
        label = str(record.get("name") or record.get("industry") or record.get("ts_code") or "")
        score = _safe_float(record.get(score_field))
        matches.append(
            {
                "source": collection,
                "label": label,
                "score": round(score, 4),
                "matched_keywords": sorted(set(record_hits)),
            }
        )
    matches.sort(key=lambda item: item.get("score") or 0, reverse=True)
    return max([_safe_float(item.get("score")) for item in matches] or [0.0]), sorted(set(hits)), matches[:limit]


def _match_limit_records(payload: dict[str, Any], keywords: list[str]) -> tuple[float, list[str], list[dict[str, Any]]]:
    matches: list[dict[str, Any]] = []
    hits: list[str] = []
    for record in _market_records(payload, "limit_up_top"):
        text = _record_text(record, ["industry"])
        record_hits = _keyword_hits(text, keywords)
        if not record_hits:
            continue
        count = int(_safe_float(record.get("limit_count")))
        score = min(100.0, count * 12.0)
        hits.extend(record_hits)
        matches.append(
            {
                "source": "limit_up_top",
                "label": str(record.get("industry") or ""),
                "score": round(score, 4),
                "matched_keywords": sorted(set(record_hits)),
            }
        )
    matches.sort(key=lambda item: item.get("score") or 0, reverse=True)
    return max([_safe_float(item.get("score")) for item in matches] or [0.0]), sorted(set(hits)), matches[:3]


def _match_flow_records(payload: dict[str, Any], keywords: list[str]) -> tuple[float, list[str], list[dict[str, Any]]]:
    rows = _market_records(payload, "moneyflow_top")
    matches: list[dict[str, Any]] = []
    hits: list[str] = []
    total = max(1, len(rows))
    for index, record in enumerate(rows):
        text = _record_text(record, ["industry"])
        record_hits = _keyword_hits(text, keywords)
        if not record_hits:
            continue
        rank_score = 100.0 * (total - index) / total
        large_net = _safe_float(record.get("large_net"))
        score = rank_score if large_net >= 0 else rank_score * 0.5
        hits.extend(record_hits)
        matches.append(
            {
                "source": "moneyflow_top",
                "label": str(record.get("industry") or ""),
                "score": round(score, 4),
                "matched_keywords": sorted(set(record_hits)),
            }
        )
    matches.sort(key=lambda item: item.get("score") or 0, reverse=True)
    return max([_safe_float(item.get("score")) for item in matches] or [0.0]), sorted(set(hits)), matches[:3]


def _legacy_component_scores(
    source_rows: list[dict[str, Any]],
    field_multipliers: dict[str, float],
    inherited_multiplier: float,
) -> dict[str, float]:
    if not source_rows:
        return {field: 0.0 for field in FIELD_WEIGHTS}
    return {
        "sw": max(_safe_float(row.get("sw_score")) for row in source_rows) * max(field_multipliers.get("sw", 0), inherited_multiplier),
        "ths": max(_safe_float(row.get("ths_score")) for row in source_rows) * max(field_multipliers.get("ths", 0), inherited_multiplier),
        "etf": max(_safe_float(row.get("etf_score")) for row in source_rows) * max(field_multipliers.get("etf", 0), inherited_multiplier),
        "limit": max(_safe_float(row.get("limit_score")) for row in source_rows) * max(field_multipliers.get("limit", 0), inherited_multiplier),
        "flow": max(_safe_float(row.get("flow_rank")) * 100 for row in source_rows) * max(field_multipliers.get("flow", 0), inherited_multiplier),
    }


def _weighted_market_score(component_scores: dict[str, float]) -> float:
    return sum(max(0.0, min(100.0, component_scores.get(field, 0.0))) * weight for field, weight in FIELD_WEIGHTS.items())


def _source_policy_text(source_rows: list[dict[str, Any]], mainline_by_key: dict[str, dict[str, Any]]) -> str:
    parts: list[str] = []
    for row in source_rows:
        parts.append(
            _record_text(
                row,
                [
                    "top_policy",
                    "policy_details",
                    "lifecycle_reasons",
                    "top_sw",
                    "top_ths",
                    "top_etf",
                ],
            )
        )
        mainline = mainline_by_key.get(_theme_id(row)) or mainline_by_key.get(_theme_name(row)) or {}
        parts.append(
            _record_text(
                mainline,
                [
                    "theme_name",
                    "top_event_ids",
                    "top_event_contributors",
                    "cycle_stage_reason",
                    "cycle_stage_reasons",
                    "cycle_event_age_details",
                    "lifecycle_reasons",
                ],
            )
        )
    return " ".join(parts)


def _policy_scores(
    spec: dict[str, Any],
    source_rows: list[dict[str, Any]],
    mainline_by_key: dict[str, dict[str, Any]],
    direct_policy_hits: list[str],
) -> tuple[float, float, float]:
    raw_scores: list[float] = []
    theme_scores: list[float] = []
    policy_scores_legacy: list[float] = []
    for row in source_rows:
        mainline = mainline_by_key.get(_theme_id(row)) or mainline_by_key.get(_theme_name(row)) or {}
        raw_scores.append(max(_safe_float(row.get("mainline_score_v6")), _safe_float(mainline.get("mainline_score_v6"))))
        theme_scores.append(max(_safe_float(row.get("theme_score_v5")), _safe_float(mainline.get("theme_score_v5"))))
        policy_scores_legacy.append(_safe_float(row.get("policy_score")))

    if not raw_scores:
        return 0.0, 0.0, 0.0

    exact_legacy = len(spec.get("legacy_sources") or []) == 1 and spec.get("theme_id") in set(spec.get("legacy_sources") or [])
    if direct_policy_hits:
        multiplier = 1.0
    elif exact_legacy:
        multiplier = 0.92
    else:
        multiplier = 0.38
    return max(raw_scores) * multiplier, max(theme_scores) * multiplier, max(policy_scores_legacy) * multiplier


def _confidence(direct_fields: set[str], inherited_only: bool) -> tuple[str, int]:
    if inherited_only and not direct_fields:
        return "低", 35
    if "policy" in direct_fields and len(direct_fields) >= 3:
        return "高", 90
    if len(direct_fields) >= 4:
        return "高", 85
    if len(direct_fields) >= 2:
        return "中", 65
    if direct_fields:
        return "中低", 50
    return "低", 25


def _source_legacy_rows(spec: dict[str, Any], legacy_by_key: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for key in spec.get("legacy_sources") or []:
        row = legacy_by_key.get(str(key))
        if row is None:
            continue
        marker = id(row)
        if marker in seen:
            continue
        rows.append(row)
        seen.add(marker)
    return rows


def _source_legacy_names(rows: list[dict[str, Any]]) -> list[str]:
    return [_theme_name(row) for row in rows if _theme_name(row)]


def build_taxonomy_v2_backfill(report_id: str, payload: dict[str, Any], taxonomy: dict[str, Any] | None = None) -> dict[str, Any]:
    taxonomy = taxonomy or load_taxonomy()
    legacy_rows = _legacy_rows(payload)
    legacy_by_key = _by_legacy_key(legacy_rows)
    mainline_by_key = _by_legacy_key(_mainline_rows(payload))
    max_policy_score = _max_mainline_score(payload)
    theme_rows: list[dict[str, Any]] = []

    for spec in taxonomy.get("themes") or []:
        source_rows = _source_legacy_rows(spec, legacy_by_key)
        policy_text = _source_policy_text(source_rows, mainline_by_key)
        policy_hits = _keyword_hits(policy_text, _keywords(spec, "policy_keywords"))

        sw_score, sw_hits, sw_sources = _match_market_records(payload, "sw_top", ["name"], _keywords(spec, "sw_keywords"))
        ths_score, ths_hits, ths_sources = _match_market_records(payload, "ths_top", ["name"], _keywords(spec, "ths_keywords"))
        etf_score, etf_hits, etf_sources = _match_market_records(
            payload,
            "etf_top",
            ["name", "ts_code"],
            _keywords(spec, "etf_keywords"),
        )
        limit_score, limit_hits, limit_sources = _match_limit_records(payload, _keywords(spec, "limit_keywords"))
        flow_score, flow_hits, flow_sources = _match_flow_records(payload, _keywords(spec, "flow_keywords"))

        legacy_field_text = " ".join(
            _record_text(row, ["theme", "theme_name", "top_sw", "top_ths", "top_etf", "top_policy"]) for row in source_rows
        )
        legacy_field_hits = {
            "sw": _keyword_hits(" ".join(_record_text(row, ["top_sw"]) for row in source_rows), _keywords(spec, "sw_keywords")),
            "ths": _keyword_hits(" ".join(_record_text(row, ["top_ths"]) for row in source_rows), _keywords(spec, "ths_keywords")),
            "etf": _keyword_hits(" ".join(_record_text(row, ["top_etf"]) for row in source_rows), _keywords(spec, "etf_keywords")),
            "limit": _keyword_hits(
                " ".join(_record_text(row, ["theme", "top_sw", "top_ths"]) for row in source_rows),
                _keywords(spec, "limit_keywords"),
            ),
            "flow": _keyword_hits(
                " ".join(_record_text(row, ["theme", "top_sw", "top_ths"]) for row in source_rows),
                _keywords(spec, "flow_keywords"),
            ),
        }
        direct_legacy_hits = _keyword_hits(
            legacy_field_text,
            _keywords(spec, "sw_keywords")
            + _keywords(spec, "ths_keywords")
            + _keywords(spec, "etf_keywords")
            + _keywords(spec, "policy_keywords"),
        )
        field_multipliers = {field: 1.0 for field, hits in legacy_field_hits.items() if hits}
        inherited_multiplier = 0.28 if source_rows else 0.0
        inherited_scores = _legacy_component_scores(source_rows, field_multipliers, inherited_multiplier)
        component_scores = {
            "sw": max(sw_score, inherited_scores["sw"]),
            "ths": max(ths_score, inherited_scores["ths"]),
            "etf": max(etf_score, inherited_scores["etf"]),
            "limit": max(limit_score, inherited_scores["limit"]),
            "flow": max(flow_score, inherited_scores["flow"]),
        }

        direct_fields = {
            field
            for field, hits in {
                "sw": sw_hits,
                "ths": ths_hits,
                "etf": etf_hits,
                "limit": limit_hits,
                "flow": flow_hits,
                "policy": policy_hits,
            }.items()
            if hits
        }
        inherited_only = bool(source_rows) and not direct_fields and not direct_legacy_hits
        confidence_label, confidence_score = _confidence(direct_fields, inherited_only)
        policy_raw, theme_score_v5, legacy_policy_score = _policy_scores(spec, source_rows, mainline_by_key, policy_hits)
        market_score = _weighted_market_score(component_scores)
        policy_score_100 = 0.0 if policy_raw <= 0 else min(100.0, policy_raw / max_policy_score * 100.0)
        combined_score = 0.42 * policy_score_100 + 0.48 * market_score + 0.10 * confidence_score
        if inherited_only:
            combined_score *= 0.72

        matched_keywords = {
            "sw": sw_hits,
            "ths": ths_hits,
            "etf": etf_hits,
            "limit": limit_hits,
            "flow": flow_hits,
            "policy": policy_hits,
                "legacy": sorted(set(direct_legacy_hits)),
                "legacy_fields": {field: sorted(set(hits)) for field, hits in legacy_field_hits.items() if hits},
        }
        evidence_sources = sw_sources + ths_sources + etf_sources + limit_sources + flow_sources
        if policy_hits:
            evidence_sources.append(
                {
                    "source": "policy_mapping",
                    "label": "旧报告政策证据文本",
                    "score": round(policy_score_100, 4),
                    "matched_keywords": policy_hits[:8],
                }
            )
        for row in source_rows:
            evidence_sources.append(
                {
                    "source": "legacy_theme",
                    "label": _theme_name(row),
                    "score": round(_safe_float(row.get("evidence_score")), 4),
                    "matched_keywords": sorted(set(direct_legacy_hits))[:8],
                }
            )

        theme_rows.append(
            {
                "theme_id": spec.get("theme_id", ""),
                "theme_name": spec.get("theme_name", ""),
                "parent_id": spec.get("parent_id", ""),
                "parent_name": spec.get("parent_name", ""),
                "priority": spec.get("priority", 999),
                "combined_score": round(combined_score, 4),
                "policy_mainline_score": round(policy_raw, 4),
                "policy_score_100": round(policy_score_100, 4),
                "market_heat_score": round(market_score, 4),
                "legacy_policy_score": round(legacy_policy_score, 4),
                "theme_score_v5": round(theme_score_v5, 4),
                "confidence_label": confidence_label,
                "confidence_score": confidence_score,
                "confidence_reason": "直接命中：" + "、".join(sorted(direct_fields)) if direct_fields else "仅从旧粗主题继承或暂无直接证据",
                "is_backfilled": True,
                "source_legacy_theme_ids": list(spec.get("legacy_sources") or []),
                "source_legacy_themes": _source_legacy_names(source_rows),
                "component_scores": {key: round(value, 4) for key, value in component_scores.items()},
                "matched_keywords": matched_keywords,
                "evidence_sources": evidence_sources[:12],
                "default_score_field": "policy_mainline_score",
                "mainline_score_v6": round(policy_raw, 4),
                "legacy_market_score": round(market_score, 4),
                "legacy_evidence_score": round(market_score, 4),
                "policy_score": round(legacy_policy_score, 4),
                "market_score": round(market_score, 4),
                "stage": _stage_label(combined_score, confidence_score),
                "basis_date": payload.get("basis_date", ""),
                "generated_at": payload.get("generated_at", ""),
                "report_id": report_id,
            }
        )

    theme_rows.sort(key=lambda item: (-_safe_float(item.get("combined_score")), int(item.get("priority") or 999)))
    for rank, row in enumerate(theme_rows, start=1):
        row["rank"] = rank

    parent_groups = _parent_groups(theme_rows, taxonomy)
    return {
        "scoring_version": SCORING_VERSION,
        "taxonomy_version": taxonomy.get("taxonomy_version", "theme_taxonomy_v2"),
        "source_report_id": report_id,
        "basis_date": payload.get("basis_date", ""),
        "generated_at": payload.get("generated_at", ""),
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "is_backfilled": True,
        "backfill_notice": "本结果由旧报告确定性重映射生成，不修改旧报告，不等同于当时原始研究结论。",
        "score_method": "combined_score = policy_score_100 42% + market_heat_score 48% + confidence_score 10%; policy_score_100 is report-local normalized from policy_mainline_score.",
        "market_method": "market_heat_score = SW 25% + THS 30% + ETF 25% + limit-up 10% + moneyflow 10%, using direct matches first and conservative legacy inheritance second.",
        "themes": theme_rows,
        "parent_groups": parent_groups,
        "theme_count": len(theme_rows),
    }


def _stage_label(combined_score: float, confidence_score: int) -> str:
    if combined_score >= 70 and confidence_score >= 65:
        return "强观察"
    if combined_score >= 50 and confidence_score >= 50:
        return "可观察"
    if combined_score >= 30:
        return "弱观察"
    return "证据不足"


def _parent_groups(rows: list[dict[str, Any]], taxonomy: dict[str, Any]) -> list[dict[str, Any]]:
    parent_names = {item.get("parent_id"): item.get("parent_name", "") for item in taxonomy.get("parents") or []}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("parent_id") or ""), []).append(row)
    groups = []
    for parent_id, items in grouped.items():
        active = [item for item in items if _safe_float(item.get("combined_score")) > 0]
        top = max(items, key=lambda item: _safe_float(item.get("combined_score")))
        groups.append(
            {
                "parent_id": parent_id,
                "parent_name": parent_names.get(parent_id) or top.get("parent_name", ""),
                "theme_count": len(items),
                "active_theme_count": len(active),
                "top_theme_id": top.get("theme_id", ""),
                "top_theme_name": top.get("theme_name", ""),
                "top_combined_score": top.get("combined_score", 0),
            }
        )
    groups.sort(key=lambda item: _safe_float(item.get("top_combined_score")), reverse=True)
    return groups


def load_or_build_taxonomy_v2(report_id: str, payload: dict[str, Any], output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    path = backfill_path(report_id, output_dir)
    if path.exists():
        return _read_json(path)
    return build_taxonomy_v2_backfill(report_id, payload)


def write_taxonomy_v2_backfill(report_id: str, payload: dict[str, Any], output_dir: Path = OUTPUT_DIR) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    result = build_taxonomy_v2_backfill(report_id, payload)
    path = backfill_path(report_id, output_dir)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def build_score_series(report_dir: Path = REPORT_DIR, output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    points_by_theme: dict[str, list[dict[str, Any]]] = {}
    files = report_json_files(report_dir)
    for path in files:
        report_id = report_id_from_path(path)
        payload = _read_json(path)
        backfill = load_or_build_taxonomy_v2(report_id, payload, output_dir)
        x = _series_x_label(report_id, payload)
        for row in backfill.get("themes") or []:
            theme = str(row.get("theme_name") or "")
            if not theme:
                continue
            points_by_theme.setdefault(theme, []).append(
                {
                    "x": x,
                    "basis_date": payload.get("basis_date", ""),
                    "generated_at": payload.get("generated_at", ""),
                    "score": row.get("policy_mainline_score"),
                    "default_score": row.get("policy_mainline_score"),
                    "default_score_field": "policy_mainline_score",
                    "score_field": "policy_mainline_score",
                    "mainline_score_v6": row.get("policy_mainline_score"),
                    "theme_score_v5": row.get("theme_score_v5"),
                    "policy_score_100": row.get("policy_score_100"),
                    "combined_score": row.get("combined_score"),
                    "legacy_evidence_score": row.get("market_heat_score"),
                    "legacy_market_score": row.get("market_heat_score"),
                    "legacy_policy_score": row.get("legacy_policy_score"),
                    "market_score": row.get("market_heat_score"),
                    "policy_score": row.get("legacy_policy_score"),
                    "confidence_score": row.get("confidence_score"),
                    "confidence_label": row.get("confidence_label"),
                    "stage": row.get("stage"),
                    "rank": row.get("rank"),
                    "report_id": report_id,
                }
            )
    return {
        "scoring_version": SCORING_VERSION,
        "taxonomy_version": "theme_taxonomy_v2",
        "is_backfilled": True,
        "report_count": len(files),
        "themes": [{"theme": theme, "points": points} for theme, points in sorted(points_by_theme.items())],
    }


def _series_x_label(report_id: str, payload: dict[str, Any]) -> str:
    prefix = "mainline_review_"
    if report_id.startswith(prefix):
        stamp = report_id[len(prefix) :]
        if len(stamp) == 17 and "_" in stamp:
            date_part, time_part = stamp.split("_", 1)
            month_day = date_part[5:]
            return f"{month_day} {time_part[:2]}:{time_part[2:4]}:{time_part[4:6]}"
    return str(payload.get("generated_at") or payload.get("basis_date") or report_id)


def _select_report_paths(args: argparse.Namespace) -> list[Path]:
    if args.all:
        return report_json_files(REPORT_DIR)
    if args.report_id:
        path = REPORT_DIR / f"{args.report_id}.json"
        if not path.exists():
            raise FileNotFoundError(path)
        return [path]
    return [latest_report_path(REPORT_DIR)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build taxonomy_v2 derived backfill from existing mainline reports.")
    parser.add_argument("--all", action="store_true", help="Process all historical reports.")
    parser.add_argument("--report-id", default="", help="Process a single report id.")
    parser.add_argument("--write", action="store_true", help="Write derived JSON artifacts under research/mainline_taxonomy_v2.")
    parser.add_argument("--series", action="store_true", help="Print taxonomy_v2 historical score series.")
    args = parser.parse_args()

    if args.series:
        print(json.dumps(build_score_series(), ensure_ascii=False, indent=2))
        return

    paths = _select_report_paths(args)
    written: list[str] = []
    previews: list[dict[str, Any]] = []
    for path in paths:
        report_id = report_id_from_path(path)
        payload = _read_json(path)
        result = build_taxonomy_v2_backfill(report_id, payload)
        if args.write:
            written.append(str(write_taxonomy_v2_backfill(report_id, payload)))
        previews.append(
            {
                "report_id": report_id,
                "basis_date": result.get("basis_date", ""),
                "top_themes": [
                    {
                        "rank": row.get("rank"),
                        "theme_name": row.get("theme_name"),
                        "parent_name": row.get("parent_name"),
                        "combined_score": row.get("combined_score"),
                        "confidence_label": row.get("confidence_label"),
                    }
                    for row in (result.get("themes") or [])[:8]
                ],
            }
        )
    print(json.dumps({"processed": len(paths), "written": written, "previews": previews}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
