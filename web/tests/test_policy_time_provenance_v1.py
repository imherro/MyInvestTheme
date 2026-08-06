import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from policy_time_provenance import audit_policy_time, build_policy_time_provenance_summary


def test_document_date_before_public_time_is_audited_but_not_blocked():
    audit = audit_policy_time(
        {
            "id": "p1",
            "document_date": "2026-06-01",
            "official_publish_at": "2026-06-03T09:00:00+08:00",
            "first_seen_at": "2026-06-03T10:00:00+08:00",
            "crawl_at": "2026-06-03T10:05:00+08:00",
        },
        report_basis="2026-06-04",
    )
    assert audit["time_provenance_status"] == "verified"
    assert audit["point_in_time_basis"] == "official_publish_at"
    assert "DOCUMENT_DATE_BEFORE_OFFICIAL_PUBLISH" in {item["code"] for item in audit["issues"]}
    assert audit["write_allowed"] is True


def test_missing_official_publish_uses_first_seen():
    audit = audit_policy_time(
        {"id": "p2", "document_date": "2026-06-01", "first_seen_at": "2026-06-03T10:00:00+08:00", "crawl_at": "2026-06-03T10:05:00+08:00"},
        report_basis="2026-06-04",
    )
    assert audit["time_provenance_status"] == "degraded"
    assert audit["point_in_time_basis"] == "first_seen_at"


def test_all_times_missing_is_legacy_unknown():
    audit = audit_policy_time({"id": "legacy"}, report_basis="2026-06-04")
    assert audit["time_provenance_status"] == "legacy_unknown"
    assert audit["point_in_time_basis"] == "unavailable"
    assert "POINT_IN_TIME_UNAVAILABLE" in {item["code"] for item in audit["issues"]}


def test_future_policy_time_blocks_report_write():
    summary = build_policy_time_provenance_summary(
        [{"id": "future", "official_publish_at": "2026-06-05T09:00:00+08:00", "first_seen_at": "2026-06-05T10:00:00+08:00", "crawl_at": "2026-06-05T10:05:00+08:00"}],
        report_basis="2026-06-04",
    )
    assert summary["status"] == "fail"
    assert summary["future_timestamp_count"] == 1


def test_timezone_normalization_and_determinism():
    policy = {"id": "tz", "official_publish_at": "2026-06-03T01:00:00Z", "first_seen_at": "2026-06-03T10:00:00+08:00", "crawl_at": "2026-06-03T10:05:00+08:00"}
    first = audit_policy_time(policy, report_basis="2026-06-04")
    second = audit_policy_time(policy, report_basis="2026-06-04")
    assert first == second
    assert first["official_publish_at"] == "2026-06-03T09:00:00+08:00"
