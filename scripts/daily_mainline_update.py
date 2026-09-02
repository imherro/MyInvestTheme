from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from generate_mainline_report import (
    REPORT_DIR,
    ROOT,
    TZ,
    build_report,
    choose_basis_date,
    get_trade_dates,
    make_client,
    write_report_artifacts,
)
from policy_signals import POLICY_PATH, validate_policy_store
from generate_era_mainline_report import generate as generate_era_report


POLICY_DIRTY_PREFIXES = {
    "data/policy_signals.json",
    "data/policy_candidates.jsonl",
    "data/policy_scan_status.json",
}
SNAPSHOT_REGISTRY_PATH = ROOT / "data" / "policy_snapshot_registry.json"


@dataclass
class GeneratedArtifactTransaction:
    """Restores generated artifacts when pre-commit report generation fails."""

    originals: dict[Path, bytes | None] = field(default_factory=dict)

    def track(self, *paths: Path) -> None:
        for path in paths:
            if path not in self.originals:
                self.originals[path] = path.read_bytes() if path.exists() else None

    def rollback(self) -> None:
        for path, original in self.originals.items():
            if original is None:
                if path.exists():
                    path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(original)


def run_command(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    print(f"$ {' '.join(args)}", flush=True)
    result = subprocess.run(args, cwd=ROOT, text=True, encoding="utf-8", errors="replace", capture_output=True)
    if result.stdout:
        sys.stdout.buffer.write((result.stdout.rstrip() + "\n").encode(sys.stdout.encoding or "utf-8", errors="replace"))
        sys.stdout.flush()
    if result.stderr:
        sys.stderr.buffer.write((result.stderr.rstrip() + "\n").encode(sys.stderr.encoding or "utf-8", errors="replace"))
        sys.stderr.flush()
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {' '.join(args)}")
    return result


def git_status_porcelain() -> str:
    return run_command(["git", "status", "--porcelain"], check=True).stdout.strip()


def dirty_paths() -> list[str]:
    status = git_status_porcelain()
    paths = []
    for line in status.splitlines():
        if not line:
            continue
        # Porcelain output can appear as "M path", " M path", or "?? path".
        path = line[3:] if len(line) > 2 and line[2] == " " else line[2:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path.replace("\\", "/"))
    return paths


def is_allowed_policy_dirty(path: str) -> bool:
    return path in POLICY_DIRTY_PREFIXES or path.startswith("research/policy/")


def policy_dirty_paths() -> list[Path]:
    return [ROOT / path for path in dirty_paths() if is_allowed_policy_dirty(path)]


def generated_commit_paths(
    policy_paths: list[Path],
    json_path: Path,
    md_path: Path,
    era_json_path: Path,
    era_md_path: Path,
) -> list[Path]:
    return [*policy_paths, SNAPSHOT_REGISTRY_PATH, json_path, md_path, era_json_path, era_md_path]


def ensure_clean_worktree(*, allow_dirty: bool) -> None:
    paths = dirty_paths()
    if paths and not allow_dirty:
        unexpected = [path for path in paths if not is_allowed_policy_dirty(path)]
        if not unexpected:
            print(f"Policy-only worktree changes detected and will be included: {', '.join(paths)}", flush=True)
            return
        raise RuntimeError(
            "工作区不是干净状态，自动日更已停止，避免把人工改动混进自动提交。"
            f"非政策文件改动: {', '.join(unexpected)}。先提交/清理当前改动，或显式使用 --allow-dirty。"
        )


def report_files() -> list[Path]:
    if not REPORT_DIR.exists():
        return []
    return sorted(REPORT_DIR.glob("mainline_review_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def existing_report_for_basis(basis_date: str) -> Path | None:
    for path in report_files():
        payload = load_json(path)
        if payload.get("basis_date") == basis_date:
            return path
    return None


def latest_complete_basis(today: str) -> tuple[str, dict[str, Any]]:
    pro = make_client()
    open_days = get_trade_dates(pro, today)
    basis_raw, completeness = choose_basis_date(pro, open_days)
    return f"{basis_raw[:4]}-{basis_raw[4:6]}-{basis_raw[6:]}", completeness


def commit_and_push(paths: list[Path], *, no_push: bool) -> None:
    unique_paths = []
    seen = set()
    for path in paths:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique_paths.append(path)
    relative_paths = [str(path.relative_to(ROOT)) for path in unique_paths]
    run_command(["git", "add", *relative_paths])
    staged = run_command(["git", "diff", "--cached", "--name-only"]).stdout.strip()
    if not staged:
        print("No staged changes after report generation; nothing to commit.", flush=True)
        return

    mainline_json = next(
        (path for path in unique_paths if path.parent == REPORT_DIR and path.suffix == ".json" and path.name.startswith("mainline_review_")),
        None,
    )
    if mainline_json is None:
        raise RuntimeError("Generated mainline JSON is required to build the daily commit message.")
    payload = load_json(mainline_json)
    basis_date = payload.get("basis_date", "unknown-date")
    top = (payload.get("theme_ranking") or [{}])[0]
    top_theme = top.get("theme", "unknown-theme")
    message = f"Daily mainline report {basis_date}: {top_theme}"
    run_command(["git", "commit", "-m", message])
    if no_push:
        print("Skip git push because --no-push was set.", flush=True)
    else:
        run_command(["git", "push", "origin", "main"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily after-close A-share mainline update.")
    parser.add_argument("--today", default=datetime.now(TZ).strftime("%Y-%m-%d"), help="Nominal today in YYYY-MM-DD.")
    parser.add_argument("--force", action="store_true", help="Generate a new report even if the latest complete basis date already exists.")
    parser.add_argument("--dry-run", action="store_true", help="Check latest complete basis date but do not write, test, commit, or push.")
    parser.add_argument("--skip-tests", action="store_true", help="Skip pytest validation.")
    parser.add_argument("--no-git", action="store_true", help="Write the report but do not commit or push.")
    parser.add_argument("--no-push", action="store_true", help="Commit locally but do not push.")
    parser.add_argument("--allow-dirty", action="store_true", help="Allow running when the worktree already has changes.")
    args = parser.parse_args()

    print(f"Daily mainline update started at {datetime.now(TZ).isoformat(timespec='seconds')}", flush=True)
    print(f"Nominal today: {args.today}", flush=True)

    if not args.no_git:
        ensure_clean_worktree(allow_dirty=args.allow_dirty)

    policy_errors = validate_policy_store(POLICY_PATH)
    if policy_errors:
        raise RuntimeError("Policy signal validation failed: " + "; ".join(policy_errors))
    current_policy_dirty = policy_dirty_paths()
    if current_policy_dirty:
        print("Policy changes pending: " + ", ".join(str(path.relative_to(ROOT)) for path in current_policy_dirty), flush=True)

    basis_date, completeness = latest_complete_basis(args.today)
    print(f"Latest complete basis date: {basis_date}", flush=True)
    print(f"Completeness: daily={completeness.get('daily_rows')} daily_basic={completeness.get('daily_basic_rows')}", flush=True)

    existing = existing_report_for_basis(basis_date)
    if existing and not args.force and not current_policy_dirty:
        print(f"Skip: report for basis date {basis_date} already exists: {existing.name}", flush=True)
        return 0

    if args.dry_run:
        print("Dry run: a new report would be generated, but no files were written.", flush=True)
        return 0

    transaction = GeneratedArtifactTransaction()
    transaction.track(SNAPSHOT_REGISTRY_PATH)
    try:
        report_id, payload, markdown = build_report(args.today)
        expected_json = REPORT_DIR / f"{report_id}.json"
        expected_md = REPORT_DIR / f"{report_id}.md"
        transaction.track(expected_json, expected_md)
        json_path, md_path = write_report_artifacts(report_id, payload, markdown)
        top = payload["theme_ranking"][0]
        print(f"Generated: {json_path}", flush=True)
        print(f"Generated: {md_path}", flush=True)
        print(f"Top theme: {top['theme']} {top['stage']} {top['evidence_score']:.2f}", flush=True)
        era_payload, era_json_path, era_md_path = generate_era_report(write=True, before_write=transaction.track)
        print(f"Generated: {era_json_path}", flush=True)
        print(f"Generated: {era_md_path}", flush=True)
        print(f"Era regime: {era_payload['mainline_regime']}", flush=True)

        if not args.skip_tests:
            run_command([sys.executable, "-m", "pytest", "web/tests", "-q"])
    except Exception:
        transaction.rollback()
        raise

    if args.no_git:
        print("Skip git commit/push because --no-git was set.", flush=True)
    else:
        commit_and_push(
            generated_commit_paths(current_policy_dirty, json_path, md_path, era_json_path, era_md_path),
            no_push=args.no_push,
        )

    print(f"Daily mainline update finished at {datetime.now(TZ).isoformat(timespec='seconds')}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
