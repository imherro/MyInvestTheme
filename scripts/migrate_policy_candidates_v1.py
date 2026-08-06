from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from policy_candidate_audit import CANDIDATE_PATH, load_candidates
from policy_provenance import compute_policy_content_hash
from policy_signals import POLICY_PATH, load_policy_store


def _policy_id(policy: dict[str, Any]) -> str:
    return str(policy.get("id") or policy.get("policy_id") or "")


def legacy_candidate(policy: dict[str, Any]) -> dict[str, Any]:
    policy_id = _policy_id(policy)
    source_url = str(policy.get("url") or policy.get("source_url") or policy.get("official_url") or "")
    digest = hashlib.sha256(f"{policy_id}|{source_url}".encode("utf-8")).hexdigest()[:16]
    return {
        "candidate_id": f"legacy-{digest}",
        "source_url": source_url,
        "source_domain": (urlparse(source_url).hostname or "").lower(),
        "source_org": str(policy.get("source_org") or policy.get("source") or ""),
        "title": str(policy.get("title") or ""),
        "discovered_at": "",
        "crawl_at": "",
        "content_hash": compute_policy_content_hash(policy),
        "decision": "included",
        "decision_reasons": ["legacy_policy_store_import"],
        "policy_id": policy_id,
        "review_status": "legacy_imported",
        "reviewed_at": "",
    }


def build_migration(
    policy_path: Path = POLICY_PATH,
    candidate_path: Path = CANDIDATE_PATH,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    policies = load_policy_store(policy_path).get("signals") or []
    existing = load_candidates(candidate_path)
    existing_policy_ids = {str(row.get("policy_id") or "") for row in existing}
    additions = [legacy_candidate(policy) for policy in policies if _policy_id(policy) not in existing_policy_ids]
    rows = existing + additions
    return rows, {
        "scoring_version": "policy_candidate_migration_v1",
        "mode": "dry_run",
        "policy_count": len(policies),
        "existing_candidate_count": len(existing),
        "added_count": len(additions),
        "final_candidate_count": len(rows),
    }


def write_candidates(rows: list[dict[str, Any]], path: Path = CANDIDATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)
    path.write_text(content + ("\n" if content else ""), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build auditable legacy candidate records for existing policies.")
    parser.add_argument("--write", action="store_true", help="Write data/policy_candidates.jsonl. Default is dry-run.")
    args = parser.parse_args(argv)
    rows, summary = build_migration()
    if args.write:
        write_candidates(rows)
        summary["mode"] = "write"
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
