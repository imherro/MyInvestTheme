import sys
from types import SimpleNamespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import daily_mainline_update as daily
from daily_mainline_update import GeneratedArtifactTransaction, SNAPSHOT_REGISTRY_PATH, generated_commit_paths


def test_daily_commit_paths_include_snapshot_registry():
    paths = generated_commit_paths(
        [ROOT / "data" / "policy_signals.json"],
        ROOT / "research" / "mainline" / "report.json",
        ROOT / "research" / "mainline" / "report.md",
        ROOT / "research" / "era_mainline" / "era.json",
        ROOT / "research" / "era_mainline" / "era.md",
    )

    assert SNAPSHOT_REGISTRY_PATH in paths
    assert paths.count(SNAPSHOT_REGISTRY_PATH) == 1


def test_generated_artifact_transaction_restores_new_and_existing_files(tmp_path):
    existing = tmp_path / "existing.json"
    created = tmp_path / "created.json"
    existing.write_text("before", encoding="utf-8")
    transaction = GeneratedArtifactTransaction()
    transaction.track(existing, created)
    existing.write_text("after", encoding="utf-8")
    created.write_text("new", encoding="utf-8")

    transaction.rollback()

    assert existing.read_text(encoding="utf-8") == "before"
    assert not created.exists()


def test_daily_commit_message_uses_mainline_json_not_snapshot_registry(monkeypatch):
    paths = generated_commit_paths(
        [],
        ROOT / "research" / "mainline" / "mainline_review_test.json",
        ROOT / "research" / "mainline" / "mainline_review_test.md",
        ROOT / "research" / "era_mainline" / "era.json",
        ROOT / "research" / "era_mainline" / "era.md",
    )
    commands = []

    def fake_run(args, *, check=True):
        commands.append(args)
        return SimpleNamespace(stdout="generated" if args[:4] == ["git", "diff", "--cached", "--name-only"] else "")

    monkeypatch.setattr(daily, "run_command", fake_run)
    monkeypatch.setattr(daily, "load_json", lambda path: {"basis_date": "2026-08-31", "theme_ranking": [{"theme": "半导体"}]} if path.name.startswith("mainline_review_") else {})

    daily.commit_and_push(paths, no_push=True)

    assert ["git", "commit", "-m", "Daily mainline report 2026-08-31: 半导体"] in commands
