import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from migrate_policy_candidates_v1 import build_migration, write_candidates
from policy_candidate_audit import audit_policy_candidates
from policy_provenance import compute_policy_content_hash


def policy(policy_id="p1"):
    return {"id": policy_id, "title": "政策", "source": "国务院", "url": "https://www.gov.cn/policy", "published_date": "2026-06-01", "summary": "支持发展"}


def candidate(item, **overrides):
    row = {"candidate_id": "c1", "source_url": item["url"], "source_domain": "www.gov.cn", "source_org": item["source"], "title": item["title"], "discovered_at": "", "crawl_at": "", "content_hash": compute_policy_content_hash(item), "decision": "included", "decision_reasons": [], "policy_id": item["id"], "review_status": "reviewed", "reviewed_at": ""}
    row.update(overrides)
    return row


def test_included_policy_without_candidate_blocks():
    summary = audit_policy_candidates([policy()], [])
    assert summary["status"] == "fail"
    assert summary["issues"][0]["code"] == "INCLUDED_POLICY_MISSING_CANDIDATE_RECORD"


def test_excluded_candidate_is_not_counted_as_included():
    item = policy()
    summary = audit_policy_candidates([], [candidate(item, decision="out_of_scope")])
    assert summary["included_count"] == 0
    assert summary["excluded_count"] == 1


def test_duplicate_url_conflicting_decisions_blocks():
    item = policy()
    rows = [candidate(item), candidate(item, candidate_id="c2", policy_id="", decision="duplicate")]
    summary = audit_policy_candidates([item], rows)
    assert "DUPLICATE_CANDIDATE_CONFLICT" in {issue["code"] for issue in summary["issues"]}


def test_migration_is_idempotent(tmp_path):
    policy_path = tmp_path / "policies.json"
    candidate_path = tmp_path / "candidates.jsonl"
    policy_path.write_text(json.dumps({"signals": [policy()]}, ensure_ascii=False), encoding="utf-8")
    rows, first = build_migration(policy_path, candidate_path)
    write_candidates(rows, candidate_path)
    rows_again, second = build_migration(policy_path, candidate_path)
    assert first["added_count"] == 1
    assert second["added_count"] == 0
    assert rows_again == rows


def test_migration_cli_defaults_to_dry_run():
    completed = subprocess.run([sys.executable, str(SCRIPTS / "migrate_policy_candidates_v1.py")], cwd=ROOT, text=True, capture_output=True, check=True)
    assert json.loads(completed.stdout)["mode"] == "dry_run"
