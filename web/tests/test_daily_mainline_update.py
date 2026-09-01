import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

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
