import asyncio
import sys
from datetime import date
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from mainline_lifecycle import (
    build_lifecycle_adjusted_theme_summary,
    build_mainline_lifecycle_summary,
    compute_theme_lifecycle_v2,
    sort_lifecycle_adjusted_theme_rows,
)
from web.main import app


AS_OF = date(2026, 6, 22)


def event(event_id, published_date, contribution, source="国家发展改革委", role="primary"):
    return {
        "event_cluster_id": event_id,
        "published_date": published_date,
        "source": source,
        "allocation_role": role,
        "allocated_cluster_contribution": contribution,
        "allocation_share": 1.0,
        "cluster_policy_score_v2": 0.8,
        "cluster_relevance_score_v2": 0.7,
        "cluster_stance_label": "supportive",
        "primary_policy_id": f"policy-{event_id}",
        "primary_policy_title": f"title-{event_id}",
    }


def theme(theme_id="theme_a", score=0.0, events=None):
    rows = events or []
    return {
        "theme_id": theme_id,
        "theme_name": theme_id,
        "theme_score_v5": score if score else round(sum(row.get("allocated_cluster_contribution", 0.0) for row in rows), 4),
        "all_event_contributors": rows,
        "top_event_contributors": rows[:3],
        "matched_allocated_event_count": len(rows),
        "primary_event_count": sum(1 for row in rows if row.get("allocation_role") == "primary"),
        "avg_allocation_share": 1.0 if rows else 0.0,
    }


def summary(themes):
    return {
        "scoring_version": "theme_score_v5_allocated",
        "event_theme_allocation_version": "event_theme_allocation_v2",
        "themes": themes,
    }


def get(path: str) -> httpx.Response:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path)

    return asyncio.run(run())


def test_recent_single_event_is_single_event_emerging():
    result = compute_theme_lifecycle_v2(theme(events=[event("event-a", "2026-06-20", 0.2)]), AS_OF)

    assert result["lifecycle_state"] == "single_event_emerging"
    assert result["mainline_score_v6"] <= result["theme_score_v5"]
    assert result["lifecycle_quality_multiplier"] < 1.0


def test_recent_multi_event_acceleration_is_accelerating():
    result = compute_theme_lifecycle_v2(
        theme(
            events=[
                event("event-a", "2026-06-20", 0.4),
                event("event-b", "2026-06-10", 0.4),
                event("event-c", "2026-05-10", 0.3),
            ]
        ),
        AS_OF,
    )

    assert result["score_30d"] == 0.8
    assert result["event_count_30d"] == 2
    assert result["acceleration_ratio_30d"] >= 0.25
    assert result["lifecycle_state"] == "accelerating"
    assert result["lifecycle_state_label"] == "升温加速"


def test_multiple_active_windows_are_sustained():
    result = compute_theme_lifecycle_v2(
        theme(events=[event("event-a", "2026-06-20", 0.3), event("event-b", "2026-05-10", 0.3)]),
        AS_OF,
    )

    assert result["lifecycle_state"] == "sustained"
    assert result["lifecycle_state_label"] == "持续有效"
    assert result["persistence_score"] >= 0.6666


def test_recent_weakness_is_cooling():
    result = compute_theme_lifecycle_v2(
        theme(events=[event("event-a", "2026-06-20", 0.2), event("event-b", "2026-05-10", 0.6)]),
        AS_OF,
    )

    assert result["lifecycle_state"] == "cooling"
    assert result["state_multiplier"] == 0.55


def test_only_old_events_are_legacy_tail():
    result = compute_theme_lifecycle_v2(theme(events=[event("event-old", "2026-02-01", 0.4)]), AS_OF)

    assert result["lifecycle_state"] == "legacy_tail"


def test_undated_events_are_unknown():
    result = compute_theme_lifecycle_v2(theme(events=[event("event-undated", "", 0.4)]), AS_OF)

    assert result["lifecycle_state"] == "undated_unknown"
    assert result["undated_event_count"] > 0
    assert result["undated_score"] > 0


def test_empty_theme_is_dormant():
    result = compute_theme_lifecycle_v2(theme(score=0.0, events=[]), AS_OF)

    assert result["lifecycle_state"] == "dormant"
    assert result["mainline_score_v6"] == 0.0
    assert result["lifecycle_quality_multiplier"] == 0.0


def test_breadth_score_targets():
    full = compute_theme_lifecycle_v2(
        theme(
            events=[
                event("event-a", "2026-06-20", 0.1, source="国家发展改革委"),
                event("event-b", "2026-06-19", 0.1, source="中国证监会"),
                event("event-c", "2026-06-18", 0.1, source="国家能源局"),
            ]
        ),
        AS_OF,
    )
    narrow = compute_theme_lifecycle_v2(theme(events=[event("event-a", "2026-06-20", 0.1)]), AS_OF)

    assert full["breadth_score"] == 1.0
    assert narrow["breadth_score"] == 0.3333


def test_mainline_score_v6_is_not_higher_than_v5():
    lifecycle = build_mainline_lifecycle_summary(
        summary([theme("theme_a", events=[event("event-a", "2026-06-20", 0.4)])]),
        AS_OF,
    )

    assert all(row["mainline_score_v6"] <= row["theme_score_v5"] for row in lifecycle["themes"])


def test_sorting_uses_mainline_score_v6():
    rows = [
        {"theme_id": "theme_a", "mainline_score_v6": 0.8, "theme_score_v5": 2.0, "lifecycle_state": "accelerating"},
        {"theme_id": "theme_b", "mainline_score_v6": 1.0, "theme_score_v5": 1.2, "lifecycle_state": "emerging"},
    ]

    assert sort_lifecycle_adjusted_theme_rows(rows)[0]["theme_id"] == "theme_b"


def test_future_date_clamps_age_to_zero():
    result = compute_theme_lifecycle_v2(theme(events=[event("event-future", "2026-06-30", 0.2)]), AS_OF)
    detail = result["lifecycle_event_details"][0]

    assert detail["age_days"] == 0
    assert detail["date_warning"] == "future_event_date_clamped_to_zero"


def test_lifecycle_uses_only_current_theme_summary_events():
    current = summary([theme("theme_a", events=[event("event-a", "2026-06-20", 0.4)])])
    lifecycle = build_mainline_lifecycle_summary(current, AS_OF)

    assert lifecycle["themes"][0]["event_count_90d"] == 1
    assert lifecycle["themes"][0]["score_90d"] == 0.4


def test_empty_lifecycle_input():
    lifecycle = build_mainline_lifecycle_summary(summary([]), AS_OF)
    adjusted = build_lifecycle_adjusted_theme_summary(summary([]), lifecycle)

    assert lifecycle["theme_count"] == 0
    assert lifecycle["themes"] == []
    assert adjusted["themes"] == []


def test_lifecycle_pipeline_is_deterministic():
    current = summary(
        [
            theme("theme_a", events=[event("event-a", "2026-06-20", 0.4), event("event-b", "2026-05-10", 0.2)]),
            theme("theme_b", events=[event("event-c", "2026-06-18", 0.3)]),
        ]
    )
    outputs = [build_lifecycle_adjusted_theme_summary(current, build_mainline_lifecycle_summary(current, AS_OF)) for _ in range(10)]

    assert all(item == outputs[0] for item in outputs)


def test_api_latest_exposes_lifecycle_versions():
    response = get("/api/latest")
    assert response.status_code == 200
    result = response.json()["result"]

    assert result["mainline_lifecycle_summary"]["scoring_version"] == "mainline_lifecycle_v2"
    assert result["theme_summary"]["scoring_version"] == "mainline_score_v6_lifecycle_adjusted"
