from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.metadata
import json
import platform
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "research" / "mainline"
RULES_PATH = ROOT / "config" / "reproducibility_manifest_rules.json"
SCORING_VERSION = "reproducibility_manifest_v2"
TZ = ZoneInfo("Asia/Shanghai")
SELF_HASH = "sha256:SELF"
ZERO_HASH = "sha256:" + "0" * 64


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def load_reproducibility_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def sha256_text(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def stable_json_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _path_label(path: Path, root: Path = ROOT) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def file_metadata(path: Path) -> dict[str, Any]:
    label = _path_label(path)
    if not path.exists() or not path.is_file():
        return {
            "path": label,
            "exists": False,
            "sha256": "",
            "size_bytes": 0,
            "mtime_iso": "",
        }
    stat = path.stat()
    return {
        "path": label,
        "exists": True,
        "sha256": sha256_file(path),
        "size_bytes": int(stat.st_size),
        "mtime_iso": datetime.fromtimestamp(stat.st_mtime, TZ).isoformat(timespec="seconds"),
    }


def _git_output(root: Path, args: list[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


def collect_git_metadata(root: Path) -> dict[str, Any]:
    try:
        commit = _git_output(root, ["rev-parse", "--short", "HEAD"])
        branch = _git_output(root, ["rev-parse", "--abbrev-ref", "HEAD"])
        status_text = _git_output(root, ["status", "--porcelain"])
    except Exception as exc:
        return {
            "commit": "",
            "branch": "",
            "dirty": None,
            "tracked_change_count": 0,
            "untracked_change_count": 0,
            "status_error": str(exc),
        }
    lines = [line for line in status_text.splitlines() if line.strip()]
    tracked = [line for line in lines if not line.startswith("??")]
    untracked = [line for line in lines if line.startswith("??")]
    return {
        "commit": commit,
        "branch": branch,
        "dirty": bool(lines),
        "tracked_change_count": len(tracked),
        "untracked_change_count": len(untracked),
        "status_error": "",
    }


def collect_python_runtime_metadata() -> dict[str, Any]:
    return {
        "python_version": platform.python_version(),
        "python_executable": Path(sys.executable).name,
        "platform": platform.platform(),
    }


def collect_dependency_versions(packages: list[str]) -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in packages:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = ""
    return versions


def build_file_fingerprint_section(paths: list[str], root: Path) -> list[dict[str, Any]]:
    return [file_metadata(root / path) for path in paths]


def build_input_fingerprint_section(root: Path, rules: dict[str, Any] | None = None) -> dict[str, Any]:
    active_rules = rules or load_reproducibility_rules()
    rows = build_file_fingerprint_section(list(active_rules.get("required_input_files") or []), root)
    by_path = {row["path"]: row for row in rows}
    return {
        "policy_store": by_path.get("data/policy_signals.json", file_metadata(root / "data" / "policy_signals.json")),
        "snapshot_registry": by_path.get(
            "data/policy_snapshot_registry.json",
            file_metadata(root / "data" / "policy_snapshot_registry.json"),
        ),
    }


def build_config_fingerprint_section(root: Path, rules: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    active_rules = rules or load_reproducibility_rules()
    return build_file_fingerprint_section(list(active_rules.get("required_config_files") or []), root)


def build_code_fingerprint_section(root: Path, rules: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    active_rules = rules or load_reproducibility_rules()
    return build_file_fingerprint_section(list(active_rules.get("required_code_files") or []), root)


def _missing_rows(rows: list[dict[str, Any]]) -> list[str]:
    return [str(row.get("path")) for row in rows if not row.get("exists")]


def _manifest_status(
    git: dict[str, Any],
    input_fingerprints: dict[str, Any],
    config_fingerprints: list[dict[str, Any]],
    code_fingerprints: list[dict[str, Any]],
    rules: dict[str, Any],
) -> tuple[str, list[str]]:
    reasons = [
        "git_metadata_collected" if not git.get("status_error") else "git_metadata_warning",
        "input_hashes_collected",
        "config_hashes_collected",
        "code_hashes_collected",
        "artifact_hashes_prepared",
        "no_env_values_included",
    ]
    input_missing = [key for key, row in input_fingerprints.items() if not row.get("exists")]
    config_missing = _missing_rows(config_fingerprints)
    code_missing = _missing_rows(code_fingerprints)
    if input_missing or config_missing or code_missing:
        reasons.append("required_fingerprint_missing")
        return "fail", reasons
    if git.get("dirty") and rules.get("fail_on_dirty_git"):
        reasons.append("git_dirty_fail")
        return "fail", reasons
    if git.get("dirty") and rules.get("warn_on_dirty_git", True):
        reasons.append("git_dirty_warning")
        return "warning", reasons
    if git.get("status_error"):
        return "warning", reasons
    return "pass", reasons


def _artifact_fingerprint(path: Path) -> dict[str, Any]:
    return {
        "path": _path_label(path),
        "exists": path.exists(),
        "sha256": ZERO_HASH,
        "size_bytes": int(path.stat().st_size) if path.exists() else 0,
    }


def build_reproducibility_manifest(
    payload: dict[str, Any],
    json_path: Path,
    markdown_path: Path,
    run_args: dict[str, Any] | None = None,
    root: Path | None = None,
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active_root = root or ROOT
    active_rules = rules or load_reproducibility_rules()
    git = collect_git_metadata(active_root)
    runtime = collect_python_runtime_metadata()
    runtime["dependency_versions"] = collect_dependency_versions(list(active_rules.get("tracked_dependency_packages") or []))
    input_fingerprints = build_input_fingerprint_section(active_root, active_rules)
    config_fingerprints = build_config_fingerprint_section(active_root, active_rules)
    code_fingerprints = build_code_fingerprint_section(active_root, active_rules)
    status, reasons = _manifest_status(git, input_fingerprints, config_fingerprints, code_fingerprints, active_rules)
    report_id = str(payload.get("report_id") or json_path.stem)
    return {
        "scoring_version": SCORING_VERSION,
        "status": status,
        "generated_at": now_iso(),
        "timezone": str(active_rules.get("timezone") or "Asia/Shanghai"),
        "hash_algorithm": "sha256",
        "git": git,
        "runtime": runtime,
        "run_args": {
            "basis_date": payload.get("basis_date", ""),
            "write": True,
            "report_id": report_id,
            **(run_args or {}),
        },
        "input_fingerprints": input_fingerprints,
        "config_fingerprints": config_fingerprints,
        "code_fingerprints": code_fingerprints,
        "artifact_fingerprints": {
            "json_report": _artifact_fingerprint(json_path),
            "markdown_report": _artifact_fingerprint(markdown_path),
        },
        "secret_safety": {
            "env_values_included": False,
            "forbidden_key_matches": [],
            "status": "pass",
        },
        "manifest_reasons": reasons,
        "manifest_hash": ZERO_HASH,
    }


def build_pending_reproducibility_manifest(payload: dict[str, Any], report_id: str) -> dict[str, Any]:
    return {
        "scoring_version": SCORING_VERSION,
        "status": "pending",
        "generated_at": "",
        "timezone": "Asia/Shanghai",
        "hash_algorithm": "sha256",
        "git": {},
        "runtime": {},
        "run_args": {
            "basis_date": payload.get("basis_date", ""),
            "write": False,
            "report_id": report_id,
        },
        "input_fingerprints": {},
        "config_fingerprints": [],
        "code_fingerprints": [],
        "artifact_fingerprints": {},
        "secret_safety": {
            "env_values_included": False,
            "forbidden_key_matches": [],
            "status": "pending",
        },
        "manifest_reasons": ["build_report_pending"],
        "manifest_hash": "",
    }


def apply_reproducibility_manifest_to_payload(payload: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(payload)
    result["reproducibility_manifest"] = copy.deepcopy(manifest)
    return result


def _json_hash_payload(report: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(report)
    manifest = result.setdefault("reproducibility_manifest", {})
    artifact = manifest.setdefault("artifact_fingerprints", {})
    json_report = artifact.setdefault("json_report", {})
    json_report["sha256"] = SELF_HASH
    manifest["manifest_hash"] = SELF_HASH
    return result


def json_report_self_hash(report: dict[str, Any]) -> str:
    return stable_json_hash(_json_hash_payload(report))


_HASH_RE = r"sha256:(?:[0-9a-f]{64}|SELF)"


def normalize_markdown_for_artifact_hash(markdown_text: str) -> str:
    result = re.sub(rf"(JSON artifact hash：){_HASH_RE}", r"\1sha256:SELF", markdown_text)
    result = re.sub(rf"(Markdown artifact hash：){_HASH_RE}", r"\1sha256:SELF", result)
    return result


def markdown_artifact_hash(markdown_text: str) -> str:
    return sha256_text(normalize_markdown_for_artifact_hash(markdown_text))


def _manifest_hash_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(manifest)
    result["manifest_hash"] = SELF_HASH
    return result


def finalize_artifact_hashes_in_manifest(
    manifest: dict[str, Any],
    json_text: str,
    markdown_text: str,
) -> dict[str, Any]:
    result = copy.deepcopy(manifest)
    artifact = result.setdefault("artifact_fingerprints", {})
    markdown_report = artifact.setdefault("markdown_report", {})
    markdown_report["sha256"] = markdown_artifact_hash(markdown_text)
    markdown_report["size_bytes"] = len(markdown_text.encode("utf-8"))
    json_report = artifact.setdefault("json_report", {})
    report = json.loads(json_text)
    report["reproducibility_manifest"] = result
    json_report["size_bytes"] = len(json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
    json_report["sha256"] = json_report_self_hash(report)
    report["reproducibility_manifest"] = result
    result["manifest_hash"] = stable_json_hash(_manifest_hash_payload(result))
    return result


def render_reproducibility_markdown(manifest: dict[str, Any]) -> str:
    inputs = manifest.get("input_fingerprints") or {}
    policy_store = inputs.get("policy_store") or {}
    snapshot_registry = inputs.get("snapshot_registry") or {}
    artifact = manifest.get("artifact_fingerprints") or {}
    json_report = artifact.get("json_report") or {}
    markdown_report = artifact.get("markdown_report") or {}
    git = manifest.get("git") or {}
    runtime = manifest.get("runtime") or {}
    secret = manifest.get("secret_safety") or {}
    lines = [
        "",
        "## 可复现性清单",
        "",
        f"- 可复现性版本：{manifest.get('scoring_version', '')}",
        f"- 状态：{manifest.get('status', '')}",
        f"- Git commit：{git.get('commit', '')}",
        f"- Git branch：{git.get('branch', '')}",
        f"- Git dirty：{str(git.get('dirty')).lower()}",
        f"- Python version：{runtime.get('python_version', '')}",
        f"- Policy store hash：{policy_store.get('sha256', '')}",
        f"- Snapshot registry hash：{snapshot_registry.get('sha256', '')}",
        f"- JSON artifact hash：{json_report.get('sha256', '')}",
        f"- Markdown artifact hash：{markdown_report.get('sha256', '')}",
        f"- Secret safety：{secret.get('status', '')}，未写入 env values",
    ]
    if git.get("dirty"):
        lines.append("- Git dirty：true（warning）")
    return "\n".join(lines) + "\n"


def append_reproducibility_markdown(markdown_text: str, manifest: dict[str, Any]) -> str:
    return markdown_text.rstrip() + "\n" + render_reproducibility_markdown(manifest)


def _issue(severity: str, code: str, path: str, message: str, expected: Any = None, actual: Any = None) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "path": path,
        "message": message,
        "expected": expected,
        "actual": actual,
    }


def _sha256_hash_like(value: Any) -> bool:
    return bool(re.fullmatch(r"sha256:[0-9a-f]{64}", str(value or "")))


def _validate_fingerprint_rows(rows: list[dict[str, Any]], base_path: str, issues: list[dict[str, Any]]) -> None:
    for index, row in enumerate(rows):
        path = f"{base_path}.{index}"
        if not row.get("exists"):
            issues.append(_issue("error", "REPRODUCIBILITY_REQUIRED_FILE_MISSING", path, "Required fingerprint file is missing.", True, row))
            continue
        if not _sha256_hash_like(row.get("sha256")):
            issues.append(
                _issue("error", "REPRODUCIBILITY_FINGERPRINT_HASH_INVALID", f"{path}.sha256", "Fingerprint sha256 is invalid.", "sha256:<64 hex>", row.get("sha256"))
            )


def validate_reproducibility_manifest(report: dict[str, Any], rules: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    del rules
    issues: list[dict[str, Any]] = []
    manifest = report.get("reproducibility_manifest")
    if not isinstance(manifest, dict) or not manifest:
        return [
            _issue(
                "error",
                "REPRODUCIBILITY_MANIFEST_MISSING",
                "reproducibility_manifest",
                "Report must include reproducibility_manifest.",
                "present",
                "missing",
            )
        ]
    if manifest.get("scoring_version") != SCORING_VERSION:
        issues.append(
            _issue(
                "error",
                "REPRODUCIBILITY_VERSION_MISMATCH",
                "reproducibility_manifest.scoring_version",
                "reproducibility manifest version mismatch.",
                SCORING_VERSION,
                manifest.get("scoring_version"),
            )
        )
    status = str(manifest.get("status") or "")
    if status not in {"pass", "warning"}:
        issues.append(
            _issue(
                "error",
                "REPRODUCIBILITY_STATUS_INVALID",
                "reproducibility_manifest.status",
                "Written report reproducibility status must be pass or warning.",
                ["pass", "warning"],
                status,
            )
        )
    if manifest.get("hash_algorithm") != "sha256":
        issues.append(
            _issue("error", "REPRODUCIBILITY_HASH_ALGORITHM_MISMATCH", "reproducibility_manifest.hash_algorithm", "Hash algorithm must be sha256.", "sha256", manifest.get("hash_algorithm"))
        )
    inputs = manifest.get("input_fingerprints") or {}
    for key in ("policy_store", "snapshot_registry"):
        row = inputs.get(key) or {}
        if not row.get("exists"):
            issues.append(_issue("error", "REPRODUCIBILITY_REQUIRED_INPUT_MISSING", f"reproducibility_manifest.input_fingerprints.{key}", "Required input fingerprint is missing.", True, row))
        elif not _sha256_hash_like(row.get("sha256")):
            issues.append(_issue("error", "REPRODUCIBILITY_INPUT_HASH_INVALID", f"reproducibility_manifest.input_fingerprints.{key}.sha256", "Input sha256 is invalid.", "sha256:<64 hex>", row.get("sha256")))
    _validate_fingerprint_rows(list(manifest.get("config_fingerprints") or []), "reproducibility_manifest.config_fingerprints", issues)
    _validate_fingerprint_rows(list(manifest.get("code_fingerprints") or []), "reproducibility_manifest.code_fingerprints", issues)
    artifact = manifest.get("artifact_fingerprints") or {}
    for key in ("json_report", "markdown_report"):
        row = artifact.get(key) or {}
        if not _sha256_hash_like(row.get("sha256")):
            issues.append(_issue("error", "REPRODUCIBILITY_ARTIFACT_HASH_INVALID", f"reproducibility_manifest.artifact_fingerprints.{key}.sha256", "Artifact sha256 is invalid.", "sha256:<64 hex>", row.get("sha256")))
    secret = manifest.get("secret_safety") or {}
    if secret.get("env_values_included") is not False:
        issues.append(_issue("error", "REPRODUCIBILITY_SECRET_ENV_VALUES_INCLUDED", "reproducibility_manifest.secret_safety.env_values_included", "Manifest must not include environment values.", False, secret.get("env_values_included")))
    if secret.get("forbidden_key_matches"):
        issues.append(_issue("error", "REPRODUCIBILITY_FORBIDDEN_SECRET_MATCH", "reproducibility_manifest.secret_safety.forbidden_key_matches", "Manifest must not include forbidden secret matches.", [], secret.get("forbidden_key_matches")))
    if secret.get("status") not in {"pass", "warning"}:
        issues.append(_issue("error", "REPRODUCIBILITY_SECRET_SAFETY_STATUS_INVALID", "reproducibility_manifest.secret_safety.status", "Secret safety status is invalid.", ["pass", "warning"], secret.get("status")))
    manifest_hash = manifest.get("manifest_hash")
    if manifest_hash and not _sha256_hash_like(manifest_hash):
        issues.append(_issue("error", "REPRODUCIBILITY_MANIFEST_HASH_INVALID", "reproducibility_manifest.manifest_hash", "Manifest hash is invalid.", "sha256:<64 hex>", manifest_hash))
    return issues


def assert_reproducibility_manifest(report: dict[str, Any]) -> None:
    issues = validate_reproducibility_manifest(report)
    errors = [issue for issue in issues if issue.get("severity") == "error"]
    if errors:
        codes = ", ".join(issue["code"] for issue in errors)
        raise RuntimeError(f"Reproducibility manifest failed: {codes}")


def latest_report_path() -> Path:
    files = sorted(REPORT_DIR.glob("mainline_review_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError("No mainline report JSON files found.")
    return files[0]


def validate_report_artifact_hashes(report: dict[str, Any], path: Path) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    manifest = report.get("reproducibility_manifest") or {}
    artifact = manifest.get("artifact_fingerprints") or {}
    expected_json_hash = (artifact.get("json_report") or {}).get("sha256")
    actual_json_hash = json_report_self_hash(report)
    if expected_json_hash != actual_json_hash:
        issues.append(
            _issue(
                "error",
                "REPRODUCIBILITY_JSON_SELF_HASH_MISMATCH",
                "reproducibility_manifest.artifact_fingerprints.json_report.sha256",
                "JSON artifact self-hash does not match sha256:SELF rule.",
                expected_json_hash,
                actual_json_hash,
            )
        )
    md_path = path.with_suffix(".md")
    expected_markdown_hash = (artifact.get("markdown_report") or {}).get("sha256")
    if not md_path.exists():
        issues.append(_issue("error", "REPRODUCIBILITY_MARKDOWN_ARTIFACT_MISSING", str(md_path), "Markdown artifact is missing.", "present", "missing"))
    else:
        actual_markdown_hash = markdown_artifact_hash(md_path.read_text(encoding="utf-8"))
        if expected_markdown_hash != actual_markdown_hash:
            issues.append(
                _issue(
                    "error",
                    "REPRODUCIBILITY_MARKDOWN_HASH_MISMATCH",
                    "reproducibility_manifest.artifact_fingerprints.markdown_report.sha256",
                    "Markdown artifact hash does not match normalized Markdown text.",
                    expected_markdown_hash,
                    actual_markdown_hash,
                )
            )
    return issues


def _cli(path: Path) -> int:
    report = json.loads(path.read_text(encoding="utf-8"))
    issues = validate_reproducibility_manifest(report)
    if not any(issue.get("code") == "REPRODUCIBILITY_STATUS_INVALID" for issue in issues):
        issues.extend(validate_report_artifact_hashes(report, path))
    errors = [issue for issue in issues if issue.get("severity") == "error"]
    manifest = report.get("reproducibility_manifest") or {}
    artifact = manifest.get("artifact_fingerprints") or {}
    json_report = artifact.get("json_report") or {}
    markdown_report = artifact.get("markdown_report") or {}
    status = "FAIL" if errors else str(manifest.get("status") or "pass").upper()
    print(f"Reproducibility manifest: {status}")
    print(f"git_commit: {(manifest.get('git') or {}).get('commit', '')}")
    print(f"json_hash: {json_report.get('sha256', '')}")
    print(f"markdown_hash: {markdown_report.get('sha256', '')}")
    print(f"secret_safety: {(manifest.get('secret_safety') or {}).get('status', '')}")
    for issue in errors:
        print(f"[{issue['code']}] {issue['message']}")
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate report reproducibility manifest.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--latest", action="store_true", help="Validate newest research/mainline report.")
    group.add_argument("--path", type=Path, help="Validate a specific report JSON path.")
    args = parser.parse_args(argv)
    path = latest_report_path() if args.latest else args.path
    if path is None:
        parser.error("--path is required unless --latest is set")
    return _cli(path)


if __name__ == "__main__":
    raise SystemExit(main())
