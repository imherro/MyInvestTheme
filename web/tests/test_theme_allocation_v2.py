import asyncio
import sys
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from theme_allocation import (
    allocate_event_theme_contributions,
    allocate_single_event_theme_contributions,
    build_allocated_theme_summary,
    build_event_theme_claim_rows,
    compute_raw_event_theme_contribution,
    sort_allocated_theme_rows,
)
from web.main import app


def claim(event_id="event-a", theme_id="theme_a", raw=0.6, policy_score=0.8, relevance=0.75, multiplier=1.0):
    return {
        "event_cluster_id": event_id,
        "theme_id": theme_id,
        "theme_name": theme_id,
        "cluster_policy_score_v2": policy_score,
        "cluster_relevance_score_v2": relevance,
        "direction_multiplier": multiplier,
        "raw_stance_adjusted_cluster_contribution": raw,
        "primary_policy_id": f"policy-{event_id}",
    }


def previous_summary(rows):
    return {
        "scoring_version": "theme_score_v4_stance_adjusted",
        "base_relevance_version": "theme_relevance_v2",
        "event_clustering_version": "policy_event_clustering_v2",
        "policy_stance_version": "policy_theme_stance_v2",
        "themes": rows,
    }


def get(path: str) -> httpx.Response:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path)

    return asyncio.run(run())


def test_single_theme_event_is_not_reduced():
    event = allocate_single_event_theme_contributions([claim(raw=0.6)])
    row = event["allocated_themes"][0]

    assert row["allocated_cluster_contribution"] == 0.6
    assert event["allocation_capped"] is False
    assert row["allocation_share"] == 1.0
    assert row["allocation_role"] == "primary"


def test_multi_theme_event_over_budget_is_allocated_proportionally():
    event = allocate_single_event_theme_contributions(
        [
            claim(theme_id="theme_a", raw=0.6, policy_score=0.8),
            claim(theme_id="theme_b", raw=0.4, policy_score=0.8),
        ]
    )
    rows = {row["theme_id"]: row for row in event["allocated_themes"]}

    assert rows["theme_a"]["allocated_cluster_contribution"] == 0.48
    assert rows["theme_b"]["allocated_cluster_contribution"] == 0.32
    assert sum(row["allocated_cluster_contribution"] for row in rows.values()) == 0.8
    assert event["allocation_capped"] is True


def test_multi_theme_event_under_budget_is_not_reduced():
    event = allocate_single_event_theme_contributions(
        [
            claim(theme_id="theme_a", raw=0.3, policy_score=0.8),
            claim(theme_id="theme_b", raw=0.2, policy_score=0.8),
        ]
    )
    rows = {row["theme_id"]: row for row in event["allocated_themes"]}

    assert rows["theme_a"]["allocated_cluster_contribution"] == 0.3
    assert rows["theme_b"]["allocated_cluster_contribution"] == 0.2
    assert event["allocation_capped"] is False
    assert event["allocation_reduction_effect"] == 0.0


def test_allocated_sum_never_exceeds_event_budget():
    event = allocate_single_event_theme_contributions(
        [
            claim(theme_id="theme_a", raw=0.6, policy_score=0.8),
            claim(theme_id="theme_b", raw=0.4, policy_score=0.8),
            claim(theme_id="theme_c", raw=0.2, policy_score=0.8),
        ]
    )

    assert sum(row["allocated_cluster_contribution"] for row in event["allocated_themes"]) <= event["cluster_policy_score_v2"] + 1e-6


def test_theme_score_v5_is_not_higher_than_v4_when_capped():
    summary = allocate_event_theme_contributions(
        [
            claim(event_id="event-a", theme_id="theme_a", raw=0.6, policy_score=0.8),
            claim(event_id="event-a", theme_id="theme_b", raw=0.4, policy_score=0.8),
        ]
    )
    allocated = build_allocated_theme_summary(
        previous_summary(
            [
                {"theme_id": "theme_a", "theme_name": "theme_a", "theme_score_v4": 0.6},
                {"theme_id": "theme_b", "theme_name": "theme_b", "theme_score_v4": 0.4},
            ]
        ),
        summary,
    )

    for row in allocated["themes"]:
        assert row["theme_score_v5"] <= row["theme_score_v4_stance_adjusted"]
    assert any(row["allocation_adjustment_effect"] > 0 for row in allocated["themes"])


def test_theme_sorting_uses_v5_not_v4():
    rows = [
        {
            "theme_id": "theme_a",
            "theme_score_v4_stance_adjusted": 2.0,
            "theme_score_v5": 0.8,
            "primary_event_count": 1,
            "matched_allocated_event_count": 1,
            "avg_allocation_share": 0.5,
            "avg_cluster_relevance_score_v2": 0.8,
            "avg_cluster_policy_score_v2": 0.8,
        },
        {
            "theme_id": "theme_b",
            "theme_score_v4_stance_adjusted": 1.2,
            "theme_score_v5": 1.0,
            "primary_event_count": 1,
            "matched_allocated_event_count": 1,
            "avg_allocation_share": 0.4,
            "avg_cluster_relevance_score_v2": 0.7,
            "avg_cluster_policy_score_v2": 0.7,
        },
    ]

    assert sort_allocated_theme_rows(rows)[0]["theme_id"] == "theme_b"


def test_allocation_roles_are_assigned_by_share():
    event = allocate_single_event_theme_contributions(
        [
            claim(theme_id="theme_a", raw=0.5, policy_score=2.0),
            claim(theme_id="theme_b", raw=0.3, policy_score=2.0),
            claim(theme_id="theme_c", raw=0.15, policy_score=2.0),
            claim(theme_id="theme_d", raw=0.05, policy_score=2.0),
        ]
    )
    roles = {row["theme_id"]: row["allocation_role"] for row in event["allocated_themes"]}

    assert roles["theme_a"] == "primary"
    assert roles["theme_b"] == "co_primary"
    assert roles["theme_c"] == "secondary"
    assert roles["theme_d"] == "peripheral"


def test_zero_raw_contribution_does_not_divide_by_zero():
    event = allocate_single_event_theme_contributions([claim(raw=0.0, relevance=0.0, multiplier=0.0)])
    row = event["allocated_themes"][0]

    assert event["allocation_budget_used"] == 0.0
    assert row["allocated_cluster_contribution"] == 0.0
    assert row["allocation_share"] == 0.0
    assert event["allocation_capped"] is False


def test_restrictive_zero_multiplier_has_zero_contribution():
    row = claim(raw=None, relevance=0.8, multiplier=0.0)
    row.pop("raw_stance_adjusted_cluster_contribution")

    assert compute_raw_event_theme_contribution(row) == 0.0
    event = allocate_single_event_theme_contributions([{**row, "raw_stance_adjusted_cluster_contribution": 0.0}])
    assert event["allocated_themes"][0]["allocated_cluster_contribution"] == 0.0


def test_allocation_summary_counts_are_correct():
    rows = [
        claim(event_id="event-a", theme_id="theme_a", raw=0.6, policy_score=0.8),
        claim(event_id="event-a", theme_id="theme_b", raw=0.4, policy_score=0.8),
        claim(event_id="event-b", theme_id="theme_a", raw=0.3, policy_score=0.8),
        claim(event_id="event-b", theme_id="theme_c", raw=0.2, policy_score=0.8),
        claim(event_id="event-c", theme_id="theme_a", raw=0.1, policy_score=0.8),
        claim(event_id="event-c", theme_id="theme_b", raw=0.1, policy_score=0.8),
    ]
    summary = allocate_event_theme_contributions(rows)

    assert summary["event_cluster_count"] == 3
    assert summary["event_theme_claim_count"] == 6
    assert summary["multi_theme_event_count"] == 3
    assert summary["capped_event_count"] == 1


def test_empty_allocation_input():
    summary = allocate_event_theme_contributions([])
    allocated = build_allocated_theme_summary(previous_summary([]), summary)

    assert summary["event_cluster_count"] == 0
    assert summary["event_theme_claim_count"] == 0
    assert summary["events"] == []
    assert allocated["themes"] == []


def test_allocation_pipeline_is_deterministic():
    rows = [
        claim(event_id="event-a", theme_id="theme_a", raw=0.6, policy_score=0.8),
        claim(event_id="event-a", theme_id="theme_b", raw=0.4, policy_score=0.8),
        claim(event_id="event-b", theme_id="theme_a", raw=0.2, policy_score=0.8),
    ]
    outputs = [allocate_event_theme_contributions(rows) for _ in range(10)]

    assert all(item == outputs[0] for item in outputs)


def test_build_claim_rows_reads_theme_summary_contributors():
    summary = previous_summary(
        [
            {
                "theme_id": "theme_a",
                "theme_name": "theme_a",
                "top_event_contributors": [claim(event_id="event-a", theme_id="theme_a", raw=0.6)],
            }
        ]
    )

    rows = build_event_theme_claim_rows(summary)

    assert rows[0]["raw_stance_adjusted_cluster_contribution"] == 0.6


def test_api_latest_exposes_allocation_versions():
    response = get("/api/latest")
    assert response.status_code == 200
    result = response.json()["result"]

    assert result["event_theme_allocation_summary"]["scoring_version"] == "event_theme_allocation_v2"
    assert result["theme_summary"]["event_theme_allocation_version"] == "event_theme_allocation_v2"
