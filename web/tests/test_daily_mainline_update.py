import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from daily_mainline_update import SNAPSHOT_REGISTRY_PATH, generated_commit_paths


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
