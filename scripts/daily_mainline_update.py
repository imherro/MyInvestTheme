from __future__ import annotations

import argparse
import json
import subprocess
import sys
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
)


def run_command(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    print(f"$ {' '.join(args)}", flush=True)
    result = subprocess.run(args, cwd=ROOT, text=True, encoding="utf-8", errors="replace", capture_output=True)
    if result.stdout:
        print(result.stdout.rstrip(), flush=True)
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr, flush=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {' '.join(args)}")
    return result


def git_status_porcelain() -> str:
    return run_command(["git", "status", "--porcelain"], check=True).stdout.strip()


def ensure_clean_worktree(*, allow_dirty: bool) -> None:
    status = git_status_porcelain()
    if status and not allow_dirty:
        raise RuntimeError(
            "工作区不是干净状态，自动日更已停止，避免把人工改动混进自动提交。"
            "先提交/清理当前改动，或显式使用 --allow-dirty。"
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


def write_report(report_id: str, payload: dict[str, Any], markdown: str) -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / f"{report_id}.json"
    md_path = REPORT_DIR / f"{report_id}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")
    return json_path, md_path


def latest_complete_basis(today: str) -> tuple[str, dict[str, Any]]:
    pro = make_client()
    open_days = get_trade_dates(pro, today)
    basis_raw, completeness = choose_basis_date(pro, open_days)
    return f"{basis_raw[:4]}-{basis_raw[4:6]}-{basis_raw[6:]}", completeness


def commit_and_push(paths: list[Path], *, no_push: bool) -> None:
    relative_paths = [str(path.relative_to(ROOT)) for path in paths]
    run_command(["git", "add", *relative_paths])
    staged = run_command(["git", "diff", "--cached", "--name-only"]).stdout.strip()
    if not staged:
        print("No staged changes after report generation; nothing to commit.", flush=True)
        return

    payload = load_json(paths[0]) if paths[0].suffix == ".json" else load_json(paths[1])
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

    basis_date, completeness = latest_complete_basis(args.today)
    print(f"Latest complete basis date: {basis_date}", flush=True)
    print(f"Completeness: daily={completeness.get('daily_rows')} daily_basic={completeness.get('daily_basic_rows')}", flush=True)

    existing = existing_report_for_basis(basis_date)
    if existing and not args.force:
        print(f"Skip: report for basis date {basis_date} already exists: {existing.name}", flush=True)
        return 0

    if args.dry_run:
        print("Dry run: a new report would be generated, but no files were written.", flush=True)
        return 0

    report_id, payload, markdown = build_report(args.today)
    json_path, md_path = write_report(report_id, payload, markdown)
    top = payload["theme_ranking"][0]
    print(f"Generated: {json_path}", flush=True)
    print(f"Generated: {md_path}", flush=True)
    print(f"Top theme: {top['theme']} {top['stage']} {top['evidence_score']:.2f}", flush=True)

    if not args.skip_tests:
        run_command([sys.executable, "-m", "pytest", "web/tests", "-q"])

    if args.no_git:
        print("Skip git commit/push because --no-git was set.", flush=True)
    else:
        commit_and_push([json_path, md_path], no_push=args.no_push)

    print(f"Daily mainline update finished at {datetime.now(TZ).isoformat(timespec='seconds')}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
