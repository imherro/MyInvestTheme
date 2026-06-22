from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
from datetime import datetime

from policy_provenance import compute_policy_content_hash, normalize_text


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "config" / "policy_snapshot_rules.json"
SCORING_VERSION = "policy_snapshot_integrity_v2"
TZ = ZoneInfo("Asia/Shanghai")


def round4(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return round(number, 4)


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def load_policy_snapshot_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _registry_path(rules: dict[str, Any] | None = None, path: Path | None = None) -> Path:
    if path is not None:
        return path
    active_rules = rules or load_policy_snapshot_rules()
    configured = Path(str(active_rules.get("registry_path") or "data/policy_snapshot_registry.json"))
    return configured if configured.is_absolute() else ROOT / configured


def _initial_registry() -> dict[str, Any]:
    return {
        "version": SCORING_VERSION,
        "updated_at": "",
        "last_report_id": "",
        "policy_snapshots": [],
    }


def load_snapshot_registry(path: Path | None = None) -> dict[str, Any]:
    active_path = _registry_path(path=path)
    if not active_path.exists():
        return _initial_registry()
    payload = json.loads(active_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return _initial_registry()
    payload.setdefault("version", SCORING_VERSION)
    payload.setdefault("updated_at", "")
    payload.setdefault("last_report_id", "")
    payload.setdefault("policy_snapshots", [])
    return payload


def save_snapshot_registry(registry: dict[str, Any], path: Path | None = None) -> None:
    active_path = _registry_path(path=path)
    active_path.parent.mkdir(parents=True, exist_ok=True)
    active_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")


def _first_nonempty(policy: dict[str, Any], fields: list[str] | tuple[str, ...]) -> str:
    for field in fields:
        value = normalize_text(policy.get(field))
        if value:
            return value
    return ""


def _provenance_by_index(provenance_summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = provenance_summary.get("policies") if isinstance(provenance_summary, dict) else []
    return [row for row in rows if isinstance(row, dict)]


def _revision_value(policy: dict[str, Any], fields: list[str]) -> str:
    return _first_nonempty(policy, fields)


def build_policy_snapshot_row(policy: dict[str, Any], provenance_row: dict[str, Any] | None = None) -> dict[str, Any]:
    provenance = provenance_row or {}
    rules = load_policy_snapshot_rules()
    revision_note = _revision_value(policy, [str(field) for field in rules.get("revision_note_fields") or []])
    revision_id = _revision_value(policy, [str(field) for field in rules.get("revision_id_fields") or []])
    policy_id = normalize_text(provenance.get("policy_id") or policy.get("policy_id") or policy.get("id"))
    source_url = normalize_text(provenance.get("source_url") or policy.get("source_url") or policy.get("url") or policy.get("official_url"))
    content_hash = normalize_text(provenance.get("content_hash")) or compute_policy_content_hash(policy)
    return {
        "policy_id": policy_id,
        "title": normalize_text(provenance.get("title") or policy.get("title")),
        "source_org_norm": normalize_text(provenance.get("source_org_norm")),
        "source_url": source_url,
        "source_domain": normalize_text(provenance.get("source_domain")),
        "publish_date": normalize_text(provenance.get("publish_date") or policy.get("publish_date") or policy.get("published_date")),
        "content_hash": content_hash,
        "previous_content_hash": "",
        "snapshot_status": "new",
        "provenance_status": normalize_text(provenance.get("provenance_status")),
        "inclusion_status": normalize_text(provenance.get("inclusion_status")),
        "revision_id": revision_id,
        "revision_note": revision_note,
        "first_seen_report_id": "",
        "last_seen_report_id": "",
        "first_seen_at": "",
        "last_seen_at": "",
        "snapshot_reasons": [],
    }


def index_registry_by_policy_id(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in registry.get("policy_snapshots") or []:
        if isinstance(row, dict) and row.get("policy_id"):
            result[str(row["policy_id"])] = dict(row)
    return result


def detect_duplicate_policy_ids(snapshot_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in snapshot_rows:
        policy_id = str(row.get("policy_id") or "")
        if policy_id:
            groups.setdefault(policy_id, []).append(row)
    conflicts = []
    for policy_id, rows in sorted(groups.items()):
        if len(rows) < 2:
            continue
        source_urls = sorted({str(row.get("source_url") or "") for row in rows})
        content_hashes = sorted({str(row.get("content_hash") or "") for row in rows})
        if len(source_urls) > 1 or len(content_hashes) > 1:
            conflicts.append(
                {
                    "policy_id": policy_id,
                    "row_count": len(rows),
                    "source_urls": source_urls,
                    "content_hashes": content_hashes,
                    "reason": "duplicate_policy_id_conflict",
                }
            )
    return conflicts


def detect_duplicate_source_urls(snapshot_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in snapshot_rows:
        source_url = str(row.get("source_url") or "")
        if source_url:
            groups.setdefault(source_url, []).append(row)
    conflicts = []
    for source_url, rows in sorted(groups.items()):
        policy_ids = sorted({str(row.get("policy_id") or "") for row in rows})
        if len(policy_ids) < 2:
            continue
        content_hashes = sorted({str(row.get("content_hash") or "") for row in rows})
        if len(content_hashes) > 1:
            conflicts.append(
                {
                    "source_url": source_url,
                    "row_count": len(rows),
                    "policy_ids": policy_ids,
                    "content_hashes": content_hashes,
                    "reason": "duplicate_source_url_conflict",
                }
            )
    return conflicts


def classify_policy_snapshot_status(
    snapshot_row: dict[str, Any],
    previous_row: dict[str, Any] | None,
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del rules
    row = dict(snapshot_row)
    previous_hash = normalize_text((previous_row or {}).get("content_hash"))
    current_hash = normalize_text(row.get("content_hash"))
    row["previous_content_hash"] = previous_hash
    reasons: list[str] = []
    if not previous_row:
        status = "new"
        reasons.append("policy_id_not_seen_before")
    elif current_hash == previous_hash:
        status = "unchanged"
        reasons.append("content_hash_unchanged")
    elif normalize_text(row.get("revision_note")):
        status = "changed_with_revision_note"
        reasons.append("content_hash_changed_with_revision_note")
    else:
        status = "changed_without_revision_note"
        reasons.append("content_hash_changed_without_revision_note")
    row["snapshot_status"] = status
    row["snapshot_reasons"] = reasons
    row["first_seen_report_id"] = normalize_text((previous_row or {}).get("first_seen_report_id"))
    row["first_seen_at"] = normalize_text((previous_row or {}).get("first_seen_at"))
    return row


def _mark_duplicate_conflicts(
    rows: list[dict[str, Any]],
    policy_id_conflicts: list[dict[str, Any]],
    source_url_conflicts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    conflict_policy_ids = {conflict["policy_id"] for conflict in policy_id_conflicts}
    conflict_source_urls = {conflict["source_url"] for conflict in source_url_conflicts}
    marked = []
    for row in rows:
        item = dict(row)
        reasons = list(item.get("snapshot_reasons") or [])
        if item.get("policy_id") in conflict_policy_ids:
            item["snapshot_status"] = "duplicate_policy_id_conflict"
            reasons.append("duplicate_policy_id_conflict")
        elif item.get("source_url") in conflict_source_urls:
            item["snapshot_status"] = "duplicate_source_url_conflict"
            reasons.append("duplicate_source_url_conflict")
        item["snapshot_reasons"] = sorted(set(reasons))
        marked.append(item)
    return marked


def build_policy_snapshot_summary(
    raw_policies: list[dict[str, Any]],
    provenance_summary: dict[str, Any],
    previous_registry: dict[str, Any] | None = None,
    report_id: str | None = None,
    generated_at: str | None = None,
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active_rules = rules or load_policy_snapshot_rules()
    registry = previous_registry if previous_registry is not None else load_snapshot_registry()
    previous_by_id = index_registry_by_policy_id(registry)
    provenance_rows = _provenance_by_index(provenance_summary)
    generated = generated_at or now_iso()
    current_rows = []
    for index, policy in enumerate(raw_policies):
        provenance_row = provenance_rows[index] if index < len(provenance_rows) else None
        base_row = build_policy_snapshot_row(policy, provenance_row)
        classified = classify_policy_snapshot_status(base_row, previous_by_id.get(base_row.get("policy_id", "")), active_rules)
        if not classified.get("first_seen_report_id"):
            classified["first_seen_report_id"] = report_id or ""
        if not classified.get("first_seen_at"):
            classified["first_seen_at"] = generated
        classified["last_seen_report_id"] = report_id or ""
        classified["last_seen_at"] = generated
        current_rows.append(classified)

    policy_id_conflicts = detect_duplicate_policy_ids(current_rows)
    source_url_conflicts = detect_duplicate_source_urls(current_rows)
    current_rows = _mark_duplicate_conflicts(current_rows, policy_id_conflicts, source_url_conflicts)
    current_ids = {str(row.get("policy_id") or "") for row in current_rows if row.get("policy_id")}
    removed_rows = []
    for previous_id, previous in sorted(previous_by_id.items()):
        if previous_id in current_ids:
            continue
        removed = dict(previous)
        removed["previous_content_hash"] = normalize_text(previous.get("content_hash"))
        removed["snapshot_status"] = "removed_from_current_store"
        removed["snapshot_reasons"] = ["policy_id_absent_from_current_store"]
        removed_rows.append(removed)

    status_counts = {status: 0 for status in [
        "new",
        "unchanged",
        "changed_with_revision_note",
        "changed_without_revision_note",
        "duplicate_policy_id_conflict",
        "duplicate_source_url_conflict",
    ]}
    for row in current_rows:
        status = str(row.get("snapshot_status") or "")
        if status in status_counts:
            status_counts[status] += 1
    changed_policy_count = status_counts["changed_with_revision_note"] + status_counts["changed_without_revision_note"]
    duplicate_policy_id_conflict_count = len(policy_id_conflicts)
    duplicate_source_url_conflict_count = len(source_url_conflicts)
    fail = (
        status_counts["changed_without_revision_note"] > 0
        or duplicate_policy_id_conflict_count > 0
        or duplicate_source_url_conflict_count > 0
    )
    degraded = status_counts["changed_with_revision_note"] > 0 or len(removed_rows) > 0
    status = "fail" if fail else "degraded" if degraded else "pass"
    registry_path = str(active_rules.get("registry_path") or "data/policy_snapshot_registry.json")
    return {
        "scoring_version": SCORING_VERSION,
        "registry_path": registry_path,
        "registry_loaded": bool(previous_registry is not None or _registry_path(rules=active_rules).exists()),
        "registry_update_status": "pending",
        "raw_policy_count": len(raw_policies),
        "snapshot_policy_count": len(current_rows),
        "new_policy_count": status_counts["new"],
        "unchanged_policy_count": status_counts["unchanged"],
        "changed_policy_count": changed_policy_count,
        "changed_with_revision_note_count": status_counts["changed_with_revision_note"],
        "changed_without_revision_note_count": status_counts["changed_without_revision_note"],
        "duplicate_policy_id_count": sum(1 for conflict in policy_id_conflicts if conflict.get("row_count", 0) > 1),
        "duplicate_source_url_count": sum(1 for conflict in source_url_conflicts if conflict.get("row_count", 0) > 1),
        "duplicate_policy_id_conflict_count": duplicate_policy_id_conflict_count,
        "duplicate_source_url_conflict_count": duplicate_source_url_conflict_count,
        "removed_policy_count": len(removed_rows),
        "status": status,
        "policies": current_rows,
        "removed_policies": removed_rows,
        "duplicate_policy_id_conflicts": policy_id_conflicts,
        "duplicate_source_url_conflicts": source_url_conflicts,
        "provenance_scoring_version": provenance_summary.get("scoring_version", "") if isinstance(provenance_summary, dict) else "",
    }


def assert_policy_snapshot_integrity(summary: dict[str, Any]) -> None:
    blockers = []
    if summary.get("status") == "fail":
        blockers.append("policy_snapshot_status_fail")
    if int(summary.get("changed_without_revision_note_count") or 0) > 0:
        blockers.append("changed_without_revision_note")
    if int(summary.get("duplicate_policy_id_conflict_count") or 0) > 0:
        blockers.append("duplicate_policy_id_conflict")
    if int(summary.get("duplicate_source_url_conflict_count") or 0) > 0:
        blockers.append("duplicate_source_url_conflict")
    if blockers:
        raise RuntimeError("Policy snapshot integrity failed: " + ", ".join(blockers))


def build_updated_snapshot_registry(
    previous_registry: dict[str, Any],
    snapshot_summary: dict[str, Any],
    report_id: str,
    generated_at: str,
) -> dict[str, Any]:
    previous_by_id = index_registry_by_policy_id(previous_registry)
    updated_by_id: dict[str, dict[str, Any]] = {}
    for row in snapshot_summary.get("policies") or []:
        if not isinstance(row, dict) or not row.get("policy_id"):
            continue
        item = dict(row)
        previous = previous_by_id.get(str(item["policy_id"]))
        if previous:
            item["first_seen_report_id"] = previous.get("first_seen_report_id") or item.get("first_seen_report_id", "")
            item["first_seen_at"] = previous.get("first_seen_at") or item.get("first_seen_at", "")
        item["last_seen_report_id"] = report_id
        item["last_seen_at"] = generated_at
        updated_by_id[str(item["policy_id"])] = item
    current_ids = set(updated_by_id)
    for previous_id, previous in previous_by_id.items():
        if previous_id in current_ids:
            continue
        removed = dict(previous)
        removed["snapshot_status"] = "removed_from_current_store"
        removed["snapshot_reasons"] = ["policy_id_absent_from_current_store"]
        updated_by_id[previous_id] = removed
    return {
        "version": SCORING_VERSION,
        "updated_at": generated_at,
        "last_report_id": report_id,
        "policy_snapshots": [updated_by_id[key] for key in sorted(updated_by_id)],
    }
