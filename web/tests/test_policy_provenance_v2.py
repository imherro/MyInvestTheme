import asyncio
import json
import sys
from copy import deepcopy
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_quality_guard import build_data_quality_summary, build_stage_status
from mainline_contract_validator import latest_report_path, validate_mainline_report_contract
from policy_event_clustering import build_event_cluster_summary, build_policy_event_clusters
from policy_provenance import (
    build_policy_provenance_summary,
    compute_policy_content_hash,
    compute_policy_provenance_v2,
    filter_policies_by_provenance,
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
        "policy_id": "ndrc-test-policy",
        "title": "国家发展改革委支持人工智能和算力基础设施发展",
        "source_org": "国家发展改革委",
        "source_url": "https://www.ndrc.gov.cn/test/policy.html",
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


def issue_codes(summary: dict, severity: str = "error") -> set[str]:
    return {issue["code"] for issue in summary["issues"] if issue["severity"] == severity}


def latest_payload() -> dict:
    return json.loads(latest_report_path().read_text(encoding="utf-8"))


def test_official_ndrc_policy_is_verified():
    provenance = compute_policy_provenance_v2(policy())
    assert provenance["provenance_status"] == "verified"
    assert provenance["inclusion_status"] == "included_in_mainline"
    assert provenance["source_org_norm"] == "ndrc"
    assert provenance["official_domain_match"] is True
    assert provenance["source_org_domain_match"] is True


def test_non_official_domain_is_rejected():
    provenance = compute_policy_provenance_v2(policy(source_url="https://example.com/policy"))
    assert provenance["provenance_status"] == "rejected"
    assert provenance["inclusion_status"] == "excluded_from_mainline"
    assert "non_official_source_domain" in provenance["provenance_reasons"]


def test_source_org_domain_conflict_is_rejected():
    provenance = compute_policy_provenance_v2(policy(source_url="https://www.csrc.gov.cn/csrc/test.html"))
    assert provenance["provenance_status"] == "rejected"
    assert provenance["exclusion_reason"] == "source_org_domain_conflict"


def test_missing_source_url_is_rejected():
    provenance = compute_policy_provenance_v2(policy(source_url=""))
    assert provenance["provenance_status"] == "rejected"
    assert "missing_source_url" in provenance["provenance_reasons"]


def test_missing_required_field_is_rejected():
    provenance = compute_policy_provenance_v2(policy(summary=""))
    assert provenance["provenance_status"] == "rejected"
    assert "summary" in provenance["missing_required_fields"]


def test_missing_recommended_field_is_degraded_but_included():
    provenance = compute_policy_provenance_v2(policy(beneficiary_chain=[]))
    assert provenance["provenance_status"] == "degraded"
    assert provenance["inclusion_status"] == "included_in_mainline"
    assert "beneficiary_chain" in provenance["missing_recommended_fields"]


def test_unparseable_date_is_rejected():
    provenance = compute_policy_provenance_v2(policy(publish_date="not-a-date"))
    assert provenance["provenance_status"] == "rejected"
    assert "unparseable_publish_date" in provenance["provenance_reasons"]


def test_content_hash_is_deterministic_and_whitespace_stable():
    left = policy(summary=" 支持人工智能  和 算力基础设施发展。 ")
    right = {
        "related_industries": ["AI算力", "半导体"],
        "beneficiary_chain": ["人工智能", "算力"],
        "key_points": ["人工智能", "算力基础设施"],
        "summary": "支持人工智能 和 算力基础设施发展。",
        "publish_date": "2026-06-01",
        "source_url": "https://www.ndrc.gov.cn/test/policy.html#fragment",
        "source_org": "国家发展改革委",
        "title": "国家发展改革委支持人工智能和算力基础设施发展",
        "policy_id": "ndrc-test-policy",
    }
    assert compute_policy_content_hash(left) == compute_policy_content_hash(right)


def test_rejected_policy_is_not_used_in_mainline_scoring_input():
    accepted = policy(policy_id="good-policy")
    rejected = policy(policy_id="bad-policy", source_url="https://example.com/policy")
    included, excluded = filter_policies_by_provenance([accepted, rejected])
    clusters = build_policy_event_clusters(included, ["人工智能", "算力"])
    event_summary = build_event_cluster_summary(included, clusters)
    assert [item["policy_id"] for item in excluded] == ["bad-policy"]
    assert event_summary["raw_policy_count"] == 1
    assert "bad-policy" not in json.dumps(event_summary, ensure_ascii=False)


def test_contract_detects_rejected_policy_leakage():
    report = latest_payload()
    broken = deepcopy(report)
    included_count = broken["policy_provenance_summary"]["included_policy_count"]
    broken["policy_provenance_summary"]["raw_policy_count"] = included_count + 1
    broken["policy_provenance_summary"]["excluded_policy_count"] = 1
    broken["policy_provenance_summary"]["rejected_count"] = 1
    broken["policy_provenance_summary"]["excluded_policy_ids"] = ["bad-policy"]
    broken["policy_provenance_summary"]["excluded_policies"] = [
        {"policy_id": "bad-policy", "provenance_status": "rejected", "inclusion_status": "excluded_from_mainline"}
    ]
    broken["mainline_ranking"][0]["top_event_contributors"][0]["member_policy_ids"].append("bad-policy")
    summary = validate_mainline_report_contract(broken, checked_at="2026-06-22T18:00:00+08:00")
    assert "REJECTED_POLICY_USED_IN_MAINLINE" in issue_codes(summary)


def test_data_quality_stage_contains_required_policy_provenance():
    report = latest_payload()
    statuses = {
        status["stage"]: status
        for status in report["data_quality_summary"]["stage_statuses"]
    }
    assert statuses["policy_provenance"]["required"] is True
    assert statuses["policy_provenance"]["status"] == "pass"


def test_api_latest_exposes_policy_provenance_summary():
    body = get("/api/latest").json()
    summary = body["result"]["policy_provenance_summary"]
    assert summary["scoring_version"] == "policy_source_provenance_v2"
    assert summary["raw_policy_count"] == summary["included_policy_count"] + summary["excluded_policy_count"]


def test_api_index_and_health_expose_policy_provenance_status():
    index_body = get("/api/index").json()
    health_body = get("/api/health").json()
    assert index_body["policy_provenance_summary"]["scoring_version"] == "policy_source_provenance_v2"
    assert index_body["latest_report"]["policy_provenance_status"] == index_body["policy_provenance_summary"]["status"]
    assert health_body["latest_policy_provenance_status"] == index_body["policy_provenance_summary"]["status"]


def test_empty_policy_store_summary_is_zeroed():
    summary = build_policy_provenance_summary([])
    assert summary["raw_policy_count"] == 0
    assert summary["included_policy_count"] == 0
    assert summary["excluded_policy_count"] == 0
    assert summary["verified_count"] == 0
    assert summary["rejected_count"] == 0


def test_policy_provenance_summary_is_deterministic():
    policies = [
        policy(policy_id="good-policy"),
        policy(policy_id="degraded-policy", related_industries=[]),
        policy(policy_id="rejected-policy", source_url="https://example.com/policy"),
    ]
    outputs = [build_policy_provenance_summary(policies) for _ in range(10)]
    assert all(item == outputs[0] for item in outputs)


def test_data_quality_summary_can_represent_required_policy_provenance_stage():
    summary = build_data_quality_summary(
        [
            build_stage_status("policy_store", "pass", True, 2),
            build_stage_status("policy_provenance", "pass", True, 1),
        ]
    )
    stage = next(item for item in summary["stage_statuses"] if item["stage"] == "policy_provenance")
    assert stage["required"] is True
    assert summary["required_failure_count"] == 0
