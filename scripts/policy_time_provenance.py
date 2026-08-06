from __future__ import annotations

import json
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "config" / "policy_time_provenance_rules.json"
VERSION = "policy_time_provenance_v1"


def load_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_policy_time(value: Any, timezone: str = "Asia/Shanghai") -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    zone = ZoneInfo(timezone)
    try:
        if len(text) == 10:
            parsed_date = date.fromisoformat(text)
            return datetime.combine(parsed_date, time.min, zone)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=zone)
        return parsed.astimezone(zone)
    except ValueError:
        return None


def normalize_policy_time(value: Any, timezone: str = "Asia/Shanghai") -> str:
    parsed = parse_policy_time(value, timezone)
    return parsed.isoformat(timespec="seconds") if parsed else ""


def _policy_id(policy: dict[str, Any]) -> str:
    return str(policy.get("id") or policy.get("policy_id") or "")


def audit_policy_time(
    policy: dict[str, Any],
    *,
    report_basis: str | date | datetime | None = None,
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active_rules = rules or load_rules()
    timezone = str(active_rules.get("timezone") or "Asia/Shanghai")
    aliases = {
        "document_date": policy.get("document_date") or policy.get("published_date") or policy.get("publish_date"),
        "official_publish_at": policy.get("official_publish_at"),
        "first_seen_at": policy.get("first_seen_at"),
        "crawl_at": policy.get("crawl_at"),
        "effective_at": policy.get("effective_at"),
        "revision_at": policy.get("revision_at"),
    }
    parsed = {field: parse_policy_time(value, timezone) for field, value in aliases.items()}
    issues: list[dict[str, Any]] = []

    def add(code: str, field: str, detail: str) -> None:
        severity = active_rules.get("severity", {}).get(code, "warning")
        issues.append({"code": code, "severity": severity, "field": field, "detail": detail})

    for field, raw in aliases.items():
        if raw not in (None, "") and parsed[field] is None:
            add("POLICY_TIME_CONFLICT", field, f"Unparseable timestamp: {raw}")

    document = parsed["document_date"]
    official = parsed["official_publish_at"]
    first_seen = parsed["first_seen_at"]
    crawl = parsed["crawl_at"]
    if document and official and document < official:
        add("DOCUMENT_DATE_BEFORE_OFFICIAL_PUBLISH", "document_date", "Document date precedes verified public availability.")
    if document and first_seen and document < first_seen:
        add("DOCUMENT_DATE_BEFORE_FIRST_SEEN", "document_date", "Document date precedes system discovery.")
    if official and first_seen and official > first_seen:
        add("OFFICIAL_PUBLISH_AFTER_FIRST_SEEN", "official_publish_at", "Official publish time is after system first seen time.")
    if first_seen and crawl and first_seen > crawl:
        add("POLICY_TIME_CONFLICT", "first_seen_at", "System first seen time is after crawl time.")

    if official and not any(item["code"] == "OFFICIAL_PUBLISH_AFTER_FIRST_SEEN" for item in issues):
        available = official
        basis = "official_publish_at"
    elif first_seen:
        available = first_seen
        basis = "first_seen_at"
    else:
        available = None
        basis = "unavailable"
        add("POINT_IN_TIME_UNAVAILABLE", "point_in_time_available_at", "No verified public or system first-seen time is available.")

    report_at = parse_policy_time(report_basis, timezone)
    if isinstance(report_basis, date) and not isinstance(report_basis, datetime):
        report_at = datetime.combine(report_basis, time.max, ZoneInfo(timezone))
    if isinstance(report_basis, str) and len(report_basis.strip()) == 10 and report_at:
        report_at = datetime.combine(report_at.date(), time.max, ZoneInfo(timezone))
    # Effective, crawl and revision timestamps may legitimately be later than a
    # report basis. Only public/system availability determines look-ahead risk.
    future_fields = [
        field
        for field in ("official_publish_at", "first_seen_at")
        if parsed.get(field) and report_at and parsed[field] > report_at
    ]
    if available and report_at and available > report_at:
        future_fields.append("point_in_time_available_at")
    if future_fields:
        add("FUTURE_POLICY_TIMESTAMP", future_fields[0], "Policy timestamp is later than the report basis time.")
    if parsed.get("effective_at") and report_at and parsed["effective_at"] > report_at:
        add("EFFECTIVE_AFTER_REPORT_BASIS", "effective_at", "Policy effective time is later than the report basis; this is informational only.")

    invalid = any(raw not in (None, "") and parsed[field] is None for field, raw in aliases.items())
    conflict = any(item["code"] in {"OFFICIAL_PUBLISH_AFTER_FIRST_SEEN", "POLICY_TIME_CONFLICT"} for item in issues)
    if invalid:
        status = "invalid"
    elif future_fields:
        status = "future_timestamp"
    elif conflict:
        status = "conflict"
    elif not available:
        status = "legacy_unknown"
    elif not official or not first_seen or not crawl:
        status = "degraded"
    else:
        status = "verified"
    block_statuses = set(active_rules.get("write_policy", {}).get("block_statuses", []))
    return {
        "policy_id": _policy_id(policy),
        "scoring_version": active_rules.get("version", VERSION),
        "document_date": normalize_policy_time(aliases["document_date"], timezone),
        "official_publish_at": normalize_policy_time(aliases["official_publish_at"], timezone),
        "first_seen_at": normalize_policy_time(aliases["first_seen_at"], timezone),
        "crawl_at": normalize_policy_time(aliases["crawl_at"], timezone),
        "effective_at": normalize_policy_time(aliases["effective_at"], timezone),
        "revision_at": normalize_policy_time(aliases["revision_at"], timezone),
        "point_in_time_available_at": available.isoformat(timespec="seconds") if available else "",
        "point_in_time_basis": basis,
        "time_provenance_status": status,
        "time_provenance_notes": [item["detail"] for item in issues],
        "issues": issues,
        "write_allowed": status not in block_statuses,
    }


def build_policy_time_provenance_summary(
    policies: list[dict[str, Any]],
    *,
    report_basis: str | date | datetime | None = None,
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active_rules = rules or load_rules()
    audits = [audit_policy_time(policy, report_basis=report_basis, rules=active_rules) for policy in policies]
    counts = {status: 0 for status in ("verified", "degraded", "legacy_unknown", "conflict", "future_timestamp", "invalid")}
    issue_counts: dict[str, int] = {}
    for audit in audits:
        counts[audit["time_provenance_status"]] += 1
        for issue in audit["issues"]:
            issue_counts[issue["code"]] = issue_counts.get(issue["code"], 0) + 1
    blocking = [audit["policy_id"] for audit in audits if not audit["write_allowed"]]
    return {
        "scoring_version": active_rules.get("version", VERSION),
        "status": "fail" if blocking else ("degraded" if counts["legacy_unknown"] or counts["degraded"] else "pass"),
        "policy_count": len(audits),
        "status_counts": counts,
        "point_in_time_unavailable_count": issue_counts.get("POINT_IN_TIME_UNAVAILABLE", 0),
        "future_timestamp_count": issue_counts.get("FUTURE_POLICY_TIMESTAMP", 0),
        "conflict_count": counts["conflict"],
        "blocking_policy_ids": blocking,
        "issue_counts": issue_counts,
        "policies": audits,
    }


def assert_policy_time_write_allowed(summary: dict[str, Any]) -> None:
    if summary.get("status") == "fail":
        raise RuntimeError("Policy time provenance blocks report write: " + ", ".join(summary.get("blocking_policy_ids") or []))
