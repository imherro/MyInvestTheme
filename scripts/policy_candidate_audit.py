from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from policy_provenance import compute_policy_content_hash
except ModuleNotFoundError:
    from scripts.policy_provenance import compute_policy_content_hash


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "config" / "policy_candidate_audit_rules.json"
CANDIDATE_PATH = ROOT / "data" / "policy_candidates.jsonl"
VERSION = "policy_candidate_audit_v1"


def load_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_candidates(path: Path = CANDIDATE_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"Candidate line {line_number} must be an object")
        rows.append(row)
    return rows


def _policy_id(policy: dict[str, Any]) -> str:
    return str(policy.get("id") or policy.get("policy_id") or "")


def audit_policy_candidates(
    policies: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active = rules or load_rules()
    allowed = set(active.get("allowed_decisions") or [])
    issues: list[dict[str, Any]] = []

    def add(code: str, path: str, actual: Any) -> None:
        issues.append({"code": code, "severity": "error", "path": path, "actual": actual})

    by_policy: dict[str, list[dict[str, Any]]] = {}
    url_decisions: dict[str, set[str]] = {}
    hash_decisions: dict[str, set[str]] = {}
    for index, candidate in enumerate(candidates):
        decision = str(candidate.get("decision") or "")
        if not decision or decision not in allowed:
            add("CANDIDATE_DECISION_MISSING", f"candidates[{index}].decision", decision)
        policy_id = str(candidate.get("policy_id") or "")
        if policy_id:
            by_policy.setdefault(policy_id, []).append(candidate)
        source_url = str(candidate.get("source_url") or "")
        content_hash = str(candidate.get("content_hash") or "")
        if source_url:
            url_decisions.setdefault(source_url, set()).add(decision)
        if content_hash:
            hash_decisions.setdefault(content_hash, set()).add(decision)
    for key, decisions in list(url_decisions.items()) + list(hash_decisions.items()):
        meaningful = {item for item in decisions if item}
        if "included" in meaningful and len(meaningful) > 1:
            add("DUPLICATE_CANDIDATE_CONFLICT", "candidate_duplicates", {"key": key, "decisions": sorted(meaningful)})

    included_policy_ids: list[str] = []
    for policy in policies:
        policy_id = _policy_id(policy)
        records = [row for row in by_policy.get(policy_id, []) if row.get("decision") == "included"]
        if not records:
            add("INCLUDED_POLICY_MISSING_CANDIDATE_RECORD", f"policy.{policy_id}", policy_id)
            continue
        included_policy_ids.append(policy_id)
        expected_hash = compute_policy_content_hash(policy)
        if not any(str(row.get("content_hash") or "") == expected_hash for row in records):
            add("CANDIDATE_HASH_MISMATCH", f"policy.{policy_id}.content_hash", expected_hash)

    counts: dict[str, int] = {decision: 0 for decision in allowed}
    exclusion_reason_counts: dict[str, int] = {}
    for candidate in candidates:
        decision = str(candidate.get("decision") or "")
        if decision in counts:
            counts[decision] += 1
        if decision not in {"included", "pending", "duplicate"}:
            for reason in candidate.get("decision_reasons") or [decision]:
                key = str(reason)
                exclusion_reason_counts[key] = exclusion_reason_counts.get(key, 0) + 1
    decided = len(candidates) - counts.get("pending", 0)
    legacy_count = sum(1 for row in candidates if row.get("review_status") == active.get("legacy_review_status", "legacy_imported"))
    return {
        "scoring_version": active.get("version", VERSION),
        "status": "fail" if issues else ("degraded" if legacy_count else "pass"),
        "candidate_count": len(candidates),
        "included_count": counts.get("included", 0),
        "excluded_count": sum(counts.get(item, 0) for item in ("excluded", "unreachable", "invalid_source", "not_policy", "out_of_scope", "insufficient_content")),
        "pending_count": counts.get("pending", 0),
        "duplicate_count": counts.get("duplicate", 0),
        "decision_rate": round(decided / len(candidates), 4) if candidates else 0.0,
        "legacy_imported_count": legacy_count,
        "exclusion_reason_counts": dict(sorted(exclusion_reason_counts.items())),
        "included_policy_ids": sorted(included_policy_ids),
        "issues": issues,
    }


def assert_candidate_audit(summary: dict[str, Any]) -> None:
    if summary.get("status") == "fail":
        codes = ", ".join(sorted({item.get("code", "") for item in summary.get("issues") or []}))
        raise RuntimeError(f"Policy candidate audit blocks report write: {codes}")
