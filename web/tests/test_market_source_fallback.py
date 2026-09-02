from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from hithink_market_fallback import with_hithink_fallback


def test_primary_source_is_used_without_fallback():
    state = {}
    calls = []
    value = with_hithink_fallback(
        "breadth",
        lambda: calls.append("tushare") or {"rows": 1},
        lambda: calls.append("hithink") or {"rows": 2},
        state,
    )
    assert value == {"rows": 1}
    assert calls == ["tushare"]
    assert state["breadth"]["source"] == "tushare"
    assert state["breadth"]["fallback_used"] is False


def test_hithink_is_used_only_after_primary_failure():
    state = {}
    value = with_hithink_fallback(
        "breadth",
        lambda: (_ for _ in ()).throw(RuntimeError("tushare unavailable")),
        lambda: {"rows": 2},
        state,
    )
    assert value == {"rows": 2}
    assert state["breadth"]["source"] == "hithink"
    assert state["breadth"]["fallback_used"] is True
    assert "tushare unavailable" in state["breadth"]["fallback_reason"]
