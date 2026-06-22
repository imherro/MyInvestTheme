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
from data_quality_guard import build_data_quality_summary, build_stage_status
from mainline_contract_validator import latest_report_path, validate_mainline_report_contract
from policy_provenance import build_policy_provenance_summary
from policy_snapshot_integrity import (
    assert_policy_snapshot_integrity,
    build_policy_snapshot_summary,
    build_updated_snapshot_registry,
)
from web.main import app


def get(path: str) -> httpx.Response:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path)

    return asyncio.run(run())


def policy(**overrides):
    item = {
        "policy_id": "policy-a",
        "title": "国家发展改革委支持人工智能和算力基础设施发展",
        "source_org": "国家发展改革委",
        "source_url": "https://www.ndrc.gov.cn/test/policy-a.html",
        "publish_date": "2026-06-01",
        "authority_level": "national_ministry",
        "economic_scope": "cross_industry",
        "summary": "支持人工智能、数据要素和算力基础设施发展。",
        "key_points": ["人工智能", "算力基础设施"],
        "beneficiary_chain": ["人工智能", "算力"],
        "related_industries": ["AI算力", "半导体"],
    }
    item.update(overrides)
    return item


def provenance(policies):
    return build_policy_provenance_summary(policies)


def registry_from_summary(summary: dict) -> dict:
    return build_updated_snapshot_registry(
        {"version": "policy_snapshot_integrity_v2", "updated_at": "", "last_report_id": "", "policy_snapshots": []},
        summary,
        "previous_report",
        "2026-06-20T18:00:00+08:00",
    )


def latest_payload() -> dict:
    return json.loads(latest_report_path().read_text(encoding="utf-8"))


def issue_codes(summary: dict, severity: str = "error") -> set[str]:
    return {issue["code"] for issue in summary["issues"] if issue["severity"] == severity}


def test_new_policy_is_marked_new():
    policies = [policy()]
    summary = build_policy_snapshot_summary(policies, provenance(policies), previous_registry={"policy_snapshots": []})
    assert summary["policies"][0]["snapshot_status"] == "new"
    assert summary["new_policy_count"] == 1
    assert summary["status"] == "pass"


def test_unchanged_policy_is_marked_unchanged():
    policies = [policy()]
    first = build_policy_snapshot_summary(policies, provenance(policies), previous_registry={"policy_snapshots": []})
    registry = registry_from_summary(first)
    second = build_policy_snapshot_summary(policies, provenance(policies), previous_registry=registry)
    assert second["policies"][0]["snapshot_status"] == "unchanged"
    assert second["unchanged_policy_count"] == 1


def test_changed_without_revision_note_fails():
    policies = [policy()]
    first = build_policy_snapshot_summary(policies, provenance(policies), previous_registry={"policy_snapshots": []})
    registry = registry_from_summary(first)
    changed = [policy(summary="修订后的政策摘要，没有修订说明。")]
    summary = build_policy_snapshot_summary(changed, provenance(changed), previous_registry=registry)
    assert summary["policies"][0]["snapshot_status"] == "changed_without_revision_note"
    assert summary["changed_without_revision_note_count"] == 1
    with pytest.raises(RuntimeError, match="changed_without_revision_note"):
        assert_policy_snapshot_integrity(summary)


def test_changed_with_revision_note_passes_with_warning_status():
    policies = [policy()]
    first = build_policy_snapshot_summary(policies, provenance(policies), previous_registry={"policy_snapshots": []})
    registry = registry_from_summary(first)
    changed = [
        policy(
            summary="修订后的政策摘要，补充实施目标。",
            revision_note="修正摘要中的政策目标表述",
            revision_id="rev-20260622-001",
        )
    ]
    summary = build_policy_snapshot_summary(changed, provenance(changed), previous_registry=registry)
    assert summary["policies"][0]["snapshot_status"] == "changed_with_revision_note"
    assert summary["changed_with_revision_note_count"] == 1
    assert summary["status"] in ["pass", "degraded"]
    assert_policy_snapshot_integrity(summary)


def test_duplicate_policy_id_conflict_fails():
    policies = [
        policy(policy_id="same-id", source_url="https://www.ndrc.gov.cn/test/a.html", summary="A"),
        policy(policy_id="same-id", source_url="https://www.ndrc.gov.cn/test/b.html", summary="B"),
    ]
    summary = build_policy_snapshot_summary(policies, provenance(policies), previous_registry={"policy_snapshots": []})
    assert summary["duplicate_policy_id_conflict_count"] == 1
    with pytest.raises(RuntimeError, match="duplicate_policy_id_conflict"):
        assert_policy_snapshot_integrity(summary)


def test_duplicate_source_url_conflict_fails():
    policies = [
        policy(policy_id="policy-a", source_url="https://www.ndrc.gov.cn/test/shared.html", summary="A"),
        policy(policy_id="policy-b", source_url="https://www.ndrc.gov.cn/test/shared.html", summary="B"),
    ]
    summary = build_policy_snapshot_summary(policies, provenance(policies), previous_registry={"policy_snapshots": []})
    assert summary["duplicate_source_url_conflict_count"] == 1
    with pytest.raises(RuntimeError, match="duplicate_source_url_conflict"):
        assert_policy_snapshot_integrity(summary)


def test_removed_policy_is_warning_only():
    policies = [policy()]
    first = build_policy_snapshot_summary(policies, provenance(policies), previous_registry={"policy_snapshots": []})
    registry = registry_from_summary(first)
    summary = build_policy_snapshot_summary([], provenance([]), previous_registry=registry)
    assert summary["removed_policy_count"] == 1
    assert summary["status"] in ["pass", "degraded"]
    assert_policy_snapshot_integrity(summary)


def test_registry_is_not_updated_when_json_write_fails(monkeypatch, tmp_path):
    payload = {
        "generated_at": "2026-06-22 18:00:00 CST",
        "generated_at_iso": "2026-06-22T18:00:00+08:00",
        "contract_validation_summary": {"status": "pass"},
        "policy_snapshot_summary": {"status": "pass", "policies": []},
    }
    called = {"updated": False}
    monkeypatch.setattr(gen, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(gen, "update_snapshot_registry_after_success", lambda report_id, payload: called.__setitem__("updated", True))
    original_write_text = Path.write_text

    def fail_json(path, *args, **kwargs):
        if path.suffix == ".json":
            raise OSError("mock json write failed")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_json)
    with pytest.raises(OSError, match="mock json write failed"):
        gen.write_report_artifacts("mock_report", payload, "markdown")
    assert called["updated"] is False


def test_contract_validator_detects_changed_without_revision_note():
    report = latest_payload()
    broken = deepcopy(report)
    broken["policy_snapshot_summary"]["status"] = "fail"
    broken["policy_snapshot_summary"]["changed_without_revision_note_count"] = 1
    summary = validate_mainline_report_contract(broken, checked_at="2026-06-22T18:30:00+08:00")
    assert "POLICY_SNAPSHOT_CHANGED_WITHOUT_REVISION_NOTE" in issue_codes(summary)


def test_contract_validator_detects_content_hash_mismatch():
    report = latest_payload()
    broken = deepcopy(report)
    broken["policy_snapshot_summary"]["policies"][0]["content_hash"] = "sha256:bad"
    summary = validate_mainline_report_contract(broken, checked_at="2026-06-22T18:30:00+08:00")
    assert "POLICY_SNAPSHOT_CONTENT_HASH_MISMATCH" in issue_codes(summary)


def test_data_quality_stage_contains_required_policy_snapshot_integrity():
    report = latest_payload()
    statuses = {
        status["stage"]: status
        for status in report["data_quality_summary"]["stage_statuses"]
    }
    assert statuses["policy_snapshot_integrity"]["required"] is True
    assert statuses["policy_snapshot_integrity"]["status"] == "pass"


def test_api_latest_exposes_policy_snapshot_summary():
    body = get("/api/latest").json()
    summary = body["result"]["policy_snapshot_summary"]
    assert summary["scoring_version"] == "policy_snapshot_integrity_v2"


def test_api_index_exposes_policy_snapshot_summary():
    body = get("/api/index").json()
    assert body["policy_snapshot_summary"]["status"] in ["pass", "degraded"]
    assert body["latest_report"]["policy_snapshot_status"] == body["policy_snapshot_summary"]["status"]


def test_api_health_exposes_policy_snapshot_status():
    body = get("/api/health").json()
    assert body["latest_policy_snapshot_status"] in ["pass", "degraded"]


def test_policy_snapshot_summary_is_deterministic():
    policies = [policy()]
    previous = {"version": "policy_snapshot_integrity_v2", "policy_snapshots": []}
    outputs = [
        build_policy_snapshot_summary(
            policies,
            provenance(policies),
            previous_registry=previous,
            report_id="report-a",
            generated_at="2026-06-22T18:00:00+08:00",
        )
        for _ in range(10)
    ]
    assert all(item == outputs[0] for item in outputs)


def test_data_quality_summary_can_represent_required_policy_snapshot_stage():
    summary = build_data_quality_summary(
        [
            build_stage_status("policy_store", "pass", True, 2),
            build_stage_status("policy_snapshot_integrity", "pass", True, 2),
        ]
    )
    stage = next(item for item in summary["stage_statuses"] if item["stage"] == "policy_snapshot_integrity")
    assert stage["required"] is True
    assert summary["required_failure_count"] == 0
