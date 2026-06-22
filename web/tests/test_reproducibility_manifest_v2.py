import asyncio
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from mainline_contract_validator import latest_report_path, validate_mainline_report_contract
from reproducibility_manifest import (
    build_reproducibility_manifest,
    json_report_self_hash,
    sha256_file,
    stable_json_hash,
)
from web.main import app


def get(path: str) -> httpx.Response:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path)

    return asyncio.run(run())


def latest_payload() -> dict:
    return json.loads(latest_report_path().read_text(encoding="utf-8"))


def golden_payload() -> dict:
    return json.loads((ROOT / "data" / "golden_mainline_snapshot.json").read_text(encoding="utf-8"))


def issue_codes(summary: dict, severity: str = "error") -> set[str]:
    return {issue["code"] for issue in summary["issues"] if issue["severity"] == severity}


def assert_hash(value: str) -> None:
    assert isinstance(value, str)
    assert value.startswith("sha256:")
    assert len(value) == 71


def test_file_hash_is_deterministic(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_text("same content", encoding="utf-8")
    hashes = [sha256_file(path) for _ in range(10)]
    assert all(item == hashes[0] for item in hashes)


def test_stable_json_hash_is_field_order_independent():
    assert stable_json_hash({"a": 1, "b": 2}) == stable_json_hash({"b": 2, "a": 1})


def test_required_fingerprints_exist_in_latest_report():
    manifest = latest_payload()["reproducibility_manifest"]
    assert manifest["input_fingerprints"]["policy_store"]["exists"] is True
    assert manifest["input_fingerprints"]["snapshot_registry"]["exists"] is True
    assert all(row["exists"] for row in manifest["config_fingerprints"])
    assert all(row["exists"] for row in manifest["code_fingerprints"])


def test_written_report_manifest_is_not_pending():
    assert latest_payload()["reproducibility_manifest"]["status"] in {"pass", "warning"}


def test_artifact_hash_format_is_valid():
    artifact = latest_payload()["reproducibility_manifest"]["artifact_fingerprints"]
    assert_hash(artifact["json_report"]["sha256"])
    assert_hash(artifact["markdown_report"]["sha256"])


def test_json_self_hash_is_deterministic():
    report = latest_payload()
    expected = report["reproducibility_manifest"]["artifact_fingerprints"]["json_report"]["sha256"]
    assert json_report_self_hash(report) == expected
    assert all(json_report_self_hash(report) == expected for _ in range(5))


def test_secret_safety_does_not_record_env_values(monkeypatch, tmp_path):
    monkeypatch.setenv("TUSHARE_TOKEN", "super-secret-test-token")
    report = latest_payload()
    manifest = build_reproducibility_manifest(
        report,
        tmp_path / "report.json",
        tmp_path / "report.md",
        run_args={"report_id": "test"},
        root=ROOT,
    )
    serialized = json.dumps(manifest, ensure_ascii=False)
    assert "super-secret-test-token" not in serialized
    assert manifest["secret_safety"]["env_values_included"] is False


def test_contract_validator_rejects_secret_leak():
    report = latest_payload()
    broken = deepcopy(report)
    broken["reproducibility_manifest"]["secret_safety"]["env_values_included"] = True
    summary = validate_mainline_report_contract(broken, checked_at="2026-06-22T18:30:00+08:00")
    assert "REPRODUCIBILITY_SECRET_ENV_VALUES_INCLUDED" in issue_codes(summary)


def test_contract_validator_rejects_missing_required_config():
    report = latest_payload()
    broken = deepcopy(report)
    broken["reproducibility_manifest"]["config_fingerprints"][0]["exists"] = False
    broken["reproducibility_manifest"]["config_fingerprints"][0]["sha256"] = ""
    summary = validate_mainline_report_contract(broken, checked_at="2026-06-22T18:30:00+08:00")
    assert "REPRODUCIBILITY_REQUIRED_FILE_MISSING" in issue_codes(summary)


def test_data_quality_summary_contains_required_reproducibility_stage():
    statuses = latest_payload()["data_quality_summary"]["stage_statuses"]
    stage = next(item for item in statuses if item["stage"] == "reproducibility_manifest")
    assert stage["required"] is True
    assert stage["status"] in {"pass", "degraded"}


def test_api_latest_exposes_reproducibility_manifest():
    body = get("/api/latest").json()
    assert body["result"]["reproducibility_manifest"]["scoring_version"] == "reproducibility_manifest_v2"


def test_api_index_exposes_reproducibility_summary():
    body = get("/api/index").json()
    assert body["reproducibility_manifest"]["status"] in {"pass", "warning"}
    assert body["latest_report"]["reproducibility_status"] in {"pass", "warning"}
    assert body["latest_report"]["reproducibility_git_commit"]


def test_api_health_exposes_reproducibility_status():
    body = get("/api/health").json()
    assert body["latest_reproducibility_status"] in {"pass", "warning"}


def test_cli_latest_succeeds():
    completed = subprocess.run(
        [sys.executable, "scripts/reproducibility_manifest.py", "--latest"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0
    assert "Reproducibility manifest:" in completed.stdout


def test_cli_bad_report_fails(tmp_path):
    report = latest_payload()
    report["reproducibility_manifest"]["status"] = "pending"
    path = tmp_path / "bad_report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "reproducibility_manifest.py"), "--path", str(path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 1
    assert "REPRODUCIBILITY_STATUS_INVALID" in completed.stdout


def test_mainline_top_score_unchanged_from_snapshot_finalization_task():
    current = latest_payload()
    golden = golden_payload()
    assert current["mainline_ranking"][0]["theme_id"] == golden["mainline_ranking"][0]["theme_id"]
    assert current["mainline_ranking"][0]["mainline_score_v6"] == golden["mainline_ranking"][0]["mainline_score_v6"]


def test_manifest_core_is_deterministic_for_same_payload(tmp_path):
    report = latest_payload()
    manifests = [
        build_reproducibility_manifest(
            report,
            tmp_path / "report.json",
            tmp_path / "report.md",
            run_args={"report_id": "deterministic"},
            root=ROOT,
        )
        for _ in range(10)
    ]
    for manifest in manifests:
        manifest.pop("generated_at", None)
    assert all(manifest == manifests[0] for manifest in manifests)
