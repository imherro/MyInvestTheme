import math
import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from policy_scoring import compute_policy_score_v2, policy_score_components
from policy_signals import validate_policy_store


def test_policy_score_v2_is_deterministic_and_hand_checkable():
    policy = {
        "source": "国家发展改革委",
        "authority_level": "national_ministry",
        "economic_scope": "national",
        "published_date": "2026-06-01",
        "title": "重大工程建设目标",
        "evidence": "安排项目建设和目标。",
    }
    basis = date(2026, 6, 22)

    first = policy_score_components(policy, basis)
    second = policy_score_components(policy, basis)

    assert first == second
    assert first["authority_score"] == 0.85
    assert first["actionability_score"] == 0.5
    assert first["economic_scope_score"] == 1.0
    assert first["time_decay_score"] == math.exp(-21 / 30)
    expected = 0.35 * 0.85 + 0.25 * 0.5 + 0.20 * 1.0 + 0.20 * math.exp(-21 / 30)
    assert first["policy_score_v2"] == expected


def test_missing_component_inputs_fallback_to_default():
    policy = {}
    basis = date(2026, 6, 22)

    components = policy_score_components(policy, basis)

    assert components == {
        "authority_score": 0.5,
        "actionability_score": 0.5,
        "economic_scope_score": 0.5,
        "time_decay_score": 0.5,
        "policy_score_v2": 0.5,
    }


def test_deprecated_subjective_fields_do_not_affect_score():
    basis = date(2026, 6, 22)
    base_policy = {
        "source": "省级政府",
        "authority_level": "provincial",
        "economic_scope": "regional",
        "published_date": "2026-06-01",
        "title": "区域试点",
        "evidence": "区域试点。",
    }
    subjective_policy = {
        **base_policy,
        "specificity": 1.0,
        "implementation_path": 1.0,
        "confidence": 1.0,
    }

    assert compute_policy_score_v2(base_policy, basis) == compute_policy_score_v2(subjective_policy, basis)


def test_current_policy_store_has_v2_schema():
    assert validate_policy_store() == []
