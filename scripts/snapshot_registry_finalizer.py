from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from mainline_contract_validator import validate_mainline_report_contract


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "config" / "snapshot_registry_finalization_rules.json"
SCORING_VERSION = "snapshot_registry_finalization_v2"
TZ = ZoneInfo("Asia/Shanghai")


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def load_snapshot_registry_finalization_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def stable_json_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def load_registry_for_finalization(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "version": "policy_snapshot_integrity_v2",
            "updated_at": "",
            "last_report_id": "",
            "policy_snapshots": [],
        }
    return json.loads(path.read_text(encoding="utf-8"))


def _artifact_label(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def build_registry_update_receipt(
    previous_registry: dict[str, Any],
    updated_registry: dict[str, Any],
    policy_snapshot_summary: dict[str, Any],
    report_id: str,
    generated_at: str,
    json_path: Path,
    markdown_path: Path,
) -> dict[str, Any]:
    return {
        "scoring_version": SCORING_VERSION,
        "status": "updated",
        "registry_path": "data/policy_snapshot_registry.json",
        "report_id": report_id,
        "updated_at": generated_at or now_iso(),
        "previous_registry_hash": stable_json_hash(previous_registry),
        "updated_registry_hash": stable_json_hash(updated_registry),
        "registry_policy_count_before": len(previous_registry.get("policy_snapshots") or []),
        "registry_policy_count_after": len(updated_registry.get("policy_snapshots") or []),
        "new_policy_count": int(policy_snapshot_summary.get("new_policy_count") or 0),
        "unchanged_policy_count": int(policy_snapshot_summary.get("unchanged_policy_count") or 0),
        "changed_with_revision_note_count": int(policy_snapshot_summary.get("changed_with_revision_note_count") or 0),
        "removed_policy_count": int(policy_snapshot_summary.get("removed_policy_count") or 0),
        "json_artifact_path": _artifact_label(json_path),
        "markdown_artifact_path": _artifact_label(markdown_path),
        "write_steps": [
            "build_payload",
            "contract_validation_pass",
            "registry_receipt_prepared",
            "json_tmp_written",
            "markdown_tmp_written",
            "registry_backup_written",
            "registry_updated",
            "json_artifact_finalized",
            "markdown_artifact_finalized",
            "registry_backup_removed",
        ],
        "error": "",
    }


def apply_registry_update_receipt_to_payload(payload: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(payload, ensure_ascii=False))
    receipt_hash = stable_json_hash(receipt)
    result["snapshot_registry_update_summary"] = dict(receipt)
    snapshot = dict(result.get("policy_snapshot_summary") or {})
    snapshot["registry_update_status"] = receipt.get("status", "updated")
    snapshot["registry_update_version"] = SCORING_VERSION
    snapshot["registry_update_receipt_hash"] = receipt_hash
    result["policy_snapshot_summary"] = snapshot
    return result


def _receipt_markdown(receipt: dict[str, Any]) -> str:
    steps = "、".join(receipt.get("write_steps") or [])
    return "\n".join(
        [
            "",
            "## 快照 Registry 写入回执",
            "",
            f"- 状态：{receipt.get('status', '')}",
            f"- report_id：{receipt.get('report_id', '')}",
            f"- registry_path：{receipt.get('registry_path', '')}",
            f"- previous_registry_hash：{receipt.get('previous_registry_hash', '')}",
            f"- updated_registry_hash：{receipt.get('updated_registry_hash', '')}",
            f"- registry_policy_count_before：{receipt.get('registry_policy_count_before', 0)}",
            f"- registry_policy_count_after：{receipt.get('registry_policy_count_after', 0)}",
            f"- 写入步骤：{steps}",
        ]
    )


def render_markdown_with_final_receipt(payload: dict[str, Any], markdown_text: str) -> str:
    receipt = payload.get("snapshot_registry_update_summary") or {}
    snapshot = payload.get("policy_snapshot_summary") or {}
    replacement = "\n".join(
        [
            f"- Registry 更新状态：{snapshot.get('registry_update_status', 'updated')}",
            f"- Registry 更新版本：{snapshot.get('registry_update_version', SCORING_VERSION)}",
            f"- Registry 更新回执：{snapshot.get('registry_update_receipt_hash', '')}",
            f"- Registry 更新前 hash：{receipt.get('previous_registry_hash', '')}",
            f"- Registry 更新后 hash：{receipt.get('updated_registry_hash', '')}",
            f"- Registry 写入政策数：{receipt.get('registry_policy_count_after', 0)}",
        ]
    )
    if "- Registry 更新状态：pending" in markdown_text:
        final_markdown = markdown_text.replace("- Registry 更新状态：pending", replacement, 1)
    else:
        final_markdown = markdown_text
    return final_markdown.rstrip() + "\n" + _receipt_markdown(receipt) + "\n"


def write_text_atomic(path: Path, text: str, temp_suffix: str = ".tmp") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + temp_suffix)
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)


def write_json_atomic(path: Path, payload: dict[str, Any], temp_suffix: str = ".tmp") -> None:
    write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2), temp_suffix)


def _write_text_tmp(path: Path, text: str, temp_suffix: str) -> Path:
    tmp_path = path.with_name(path.name + temp_suffix)
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_text(text, encoding="utf-8")
    return tmp_path


def finalize_report_artifacts_with_registry(
    payload: dict[str, Any],
    markdown_text: str,
    json_path: Path,
    markdown_path: Path,
    registry_path: Path,
    updated_registry: dict[str, Any],
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active_rules = rules or load_snapshot_registry_finalization_rules()
    temp_suffix = str(active_rules.get("atomic_write_temp_suffix") or ".tmp")
    backup_suffix = str(active_rules.get("registry_backup_suffix") or ".bak")
    previous_registry = load_registry_for_finalization(registry_path)
    report_id = str(payload.get("report_id") or json_path.stem)
    receipt = build_registry_update_receipt(
        previous_registry,
        updated_registry,
        payload.get("policy_snapshot_summary") or {},
        report_id,
        str(payload.get("generated_at_iso") or payload.get("generated_at") or now_iso()),
        json_path,
        markdown_path,
    )
    final_payload = apply_registry_update_receipt_to_payload(payload, receipt)
    final_validation = validate_mainline_report_contract(final_payload, allow_pending_registry=False)
    if final_validation.get("error_count"):
        codes = ", ".join(issue["code"] for issue in final_validation.get("issues", []) if issue.get("severity") == "error")
        raise RuntimeError(f"Snapshot registry finalization contract failed: {codes}")
    final_payload["contract_validation_summary"] = final_validation
    final_markdown = render_markdown_with_final_receipt(final_payload, markdown_text)

    json_tmp = json_path.with_name(json_path.name + temp_suffix)
    markdown_tmp = markdown_path.with_name(markdown_path.name + temp_suffix)
    backup_path = registry_path.with_name(registry_path.name + backup_suffix)
    registry_replaced = False
    json_finalized = False
    try:
        _write_text_tmp(json_path, json.dumps(final_payload, ensure_ascii=False, indent=2), temp_suffix)
        _write_text_tmp(markdown_path, final_markdown, temp_suffix)
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        if registry_path.exists():
            shutil.copy2(registry_path, backup_path)
        else:
            backup_path.write_text(json.dumps(previous_registry, ensure_ascii=False, indent=2), encoding="utf-8")
        write_json_atomic(registry_path, updated_registry, temp_suffix)
        registry_replaced = True
        json_tmp.replace(json_path)
        json_finalized = True
        markdown_tmp.replace(markdown_path)
        if backup_path.exists():
            backup_path.unlink()
        return final_payload.get("snapshot_registry_update_summary") or receipt
    except Exception as exc:
        for tmp_path in (json_tmp, markdown_tmp):
            if tmp_path.exists():
                tmp_path.unlink()
        if registry_replaced and backup_path.exists():
            backup_path.replace(registry_path)
        if json_finalized and json_path.exists():
            json_path.unlink()
        raise RuntimeError(f"Snapshot registry finalization failed: {exc}") from exc
