import asyncio
import json
import sys
from copy import deepcopy
from pathlib import Path

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import generate_mainline_report as gen
import snapshot_registry_finalizer as finalizer
from mainline_contract_validator import latest_report_path, validate_mainline_report_contract
from policy_snapshot_integrity import build_updated_snapshot_registry
from snapshot_registry_finalizer import (
    apply_registry_update_receipt_to_payload,
    build_registry_update_receipt,
    finalize_report_artifacts_with_registry,
    stable_json_hash,
)
from web.main import app
from web.tests.test_data_quality_guard_v2 import mock_report_inputs


def get(path: str) -> httpx.Response:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path)

    return asyncio.run(run())


def latest_payload() -> dict:
    return json.loads(latest_report_path().read_text(encoding="utf-8"))


def issue_codes(summary: dict, severity: str = "error") -> set[str]:
    return {issue["code"] for issue in summary["issues"] if issue["severity"] == severity}


def registry(rows=None):
    return {
        "version": "policy_snapshot_integrity_v2",
        "updated_at": "2026-06-22T18:00:00+08:00",
        "last_report_id": "previous_report",
        "policy_snapshots": rows or [],
    }


def receipt(previous=None, updated=None):
    previous_registry = previous or registry([])
    updated_registry = updated or registry([{"policy_id": "policy-a", "content_hash": "sha256:" + "a" * 64}])
    return build_registry_update_receipt(
        previous_registry,
        updated_registry,
        {"new_policy_count": 1, "unchanged_policy_count": 0, "changed_with_revision_note_count": 0, "removed_policy_count": 0},
        "report-a",
        "2026-06-22T18:00:00+08:00",
        ROOT / "research" / "mainline" / "report-a.json",
        ROOT / "research" / "mainline" / "report-a.md",
    )


def test_written_report_no_longer_has_pending_status():
    report = latest_payload()
    assert report["policy_snapshot_summary"]["registry_update_status"] == "updated"
    assert report["snapshot_registry_update_summary"]["status"] == "updated"


def test_build_report_dry_run_payload_can_be_pending(monkeypatch):
    mock_report_inputs(monkeypatch)
    _, payload, _ = gen.build_report("2026-06-22")
    assert payload["policy_snapshot_summary"]["registry_update_status"] == "pending"
    assert payload["snapshot_registry_update_summary"]["status"] == "pending"


def test_registry_receipt_hash_is_deterministic():
    receipts = [receipt() for _ in range(10)]
    hashes = [stable_json_hash(item) for item in receipts]
    assert all(item == receipts[0] for item in receipts)
    assert all(item == hashes[0] for item in hashes)


def test_registry_write_failure_does_not_write_formal_artifacts(monkeypatch, tmp_path):
    report = latest_payload()
    payload = deepcopy(report)
    payload["report_id"] = "mock_report"
    payload["policy_snapshot_summary"]["registry_update_status"] = "pending"
    payload["snapshot_registry_update_summary"]["status"] = "pending"
    registry_path = tmp_path / "registry.json"
    updated_registry = build_updated_snapshot_registry(registry([]), payload["policy_snapshot_summary"], "mock_report", payload["generated_at_iso"])

    def fail_registry_write(path, payload, temp_suffix=".tmp"):
        raise OSError("mock registry write failed")

    monkeypatch.setattr(finalizer, "write_json_atomic", fail_registry_write)
    with pytest.raises(RuntimeError, match="mock registry write failed"):
        finalize_report_artifacts_with_registry(
            payload,
            "Registry 更新状态：pending",
            tmp_path / "mock_report.json",
            tmp_path / "mock_report.md",
            registry_path,
            updated_registry,
        )
    assert not (tmp_path / "mock_report.json").exists()
    assert not (tmp_path / "mock_report.md").exists()


def test_json_finalize_failure_restores_registry_backup(monkeypatch, tmp_path):
    report = latest_payload()
    payload = deepcopy(report)
    payload["report_id"] = "mock_report"
    payload["policy_snapshot_summary"]["registry_update_status"] = "pending"
    payload["snapshot_registry_update_summary"]["status"] = "pending"
    registry_path = tmp_path / "registry.json"
    previous_registry = registry([])
    registry_path.write_text(json.dumps(previous_registry, ensure_ascii=False, indent=2), encoding="utf-8")
    updated_registry = build_updated_snapshot_registry(previous_registry, payload["policy_snapshot_summary"], "mock_report", payload["generated_at_iso"])
    original_replace = Path.replace

    def fail_json_replace(self, target):
        if self.name == "mock_report.json.tmp":
            raise OSError("mock json finalize failed")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_json_replace)
    with pytest.raises(RuntimeError, match="mock json finalize failed"):
        finalize_report_artifacts_with_registry(
            payload,
            "Registry 更新状态：pending",
            tmp_path / "mock_report.json",
            tmp_path / "mock_report.md",
            registry_path,
            updated_registry,
        )
    assert json.loads(registry_path.read_text(encoding="utf-8")) == previous_registry


def test_contract_validator_rejects_pending_written_report():
    report = latest_payload()
    broken = deepcopy(report)
    broken["policy_snapshot_summary"]["registry_update_status"] = "pending"
    broken["snapshot_registry_update_summary"]["status"] = "pending"
    summary = validate_mainline_report_contract(broken, checked_at="2026-06-22T19:00:00+08:00")
    assert "SNAPSHOT_REGISTRY_UPDATE_PENDING_IN_WRITTEN_REPORT" in issue_codes(summary)


def test_contract_validator_rejects_bad_registry_hash():
    report = latest_payload()
    broken = deepcopy(report)
    broken["snapshot_registry_update_summary"]["updated_registry_hash"] = "bad"
    summary = validate_mainline_report_contract(broken, checked_at="2026-06-22T19:00:00+08:00")
    assert "SNAPSHOT_REGISTRY_HASH_INVALID" in issue_codes(summary)


def test_markdown_displays_updated_not_pending():
    md_path = latest_report_path().with_suffix(".md")
    markdown = md_path.read_text(encoding="utf-8")
    assert "Registry 更新状态：updated" in markdown
    assert "Registry 更新状态：pending" not in markdown


def test_api_latest_exposes_registry_update_summary():
    body = get("/api/latest").json()
    assert body["result"]["snapshot_registry_update_summary"]["status"] == "updated"
    assert body["result"]["policy_snapshot_summary"]["registry_update_status"] == "updated"


def test_api_index_exposes_registry_update_summary():
    body = get("/api/index").json()
    assert body["snapshot_registry_update_summary"]["status"] == "updated"
    assert body["latest_report"]["snapshot_registry_update_status"] == "updated"


def test_api_health_exposes_registry_update_status():
    body = get("/api/health").json()
    assert body["latest_snapshot_registry_update_status"] == "updated"


def test_mainline_top_score_unchanged_from_previous_snapshot_task():
    current = latest_payload()
    previous_path = ROOT / "research" / "mainline" / "mainline_review_2026-06-22_172047.json"
    previous = json.loads(previous_path.read_text(encoding="utf-8"))
    assert current["mainline_ranking"][0]["theme_id"] == previous["mainline_ranking"][0]["theme_id"]
    assert current["mainline_ranking"][0]["mainline_score_v6"] == previous["mainline_ranking"][0]["mainline_score_v6"]


def test_receipt_application_is_deterministic():
    report = latest_payload()
    pending = deepcopy(report)
    pending["policy_snapshot_summary"]["registry_update_status"] = "pending"
    pending["snapshot_registry_update_summary"]["status"] = "pending"
    item = receipt()
    outputs = [apply_registry_update_receipt_to_payload(pending, item) for _ in range(10)]
    assert all(output == outputs[0] for output in outputs)
