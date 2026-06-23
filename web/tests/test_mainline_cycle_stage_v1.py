from scripts.mainline_cycle_stage import (
    build_cycle_stage_summary,
    classify_mainline_cycle_stage,
    enrich_mainline_rows_with_cycle_stage,
)


def row(state="accelerating", score_30d=0.3, score_31_60d=0.1, score_90d=0.5):
    return {
        "theme_id": "ai",
        "theme_name": "AI算力/通信",
        "mainline_score_v6": 0.52,
        "lifecycle_state": state,
        "score_30d": score_30d,
        "score_31_60d": score_31_60d,
        "score_90d": score_90d,
        "acceleration_delta_30d": score_30d - score_31_60d,
        "event_count_30d": 2,
        "event_count_90d": 3,
        "active_window_count": 2,
        "source_org_count_90d": 2,
    }


def test_cycle_stage_main_rise_requires_policy_and_market_confirmation():
    result = classify_mainline_cycle_stage(row(), {"theme": "AI算力/通信", "market_score": 80, "evidence_score": 82})
    assert result["cycle_stage"] == "main_rise_diffusion"
    assert result["cycle_stage_label"] == "主升扩散期"
    assert result["cycle_market_score"] == 80


def test_cycle_stage_policy_incubation_when_market_not_confirmed():
    result = classify_mainline_cycle_stage(row("emerging"), {"theme": "AI算力/通信", "market_score": 42})
    assert result["cycle_stage"] == "policy_incubation"
    assert result["cycle_stage_label"] == "政策孕育期"


def test_cycle_stage_crowded_late_when_market_hot_but_policy_not_accelerating():
    result = classify_mainline_cycle_stage(
        row("sustained", score_30d=0.08, score_31_60d=0.16, score_90d=0.34),
        {"theme": "AI算力/通信", "market_score": 88, "evidence_score": 90},
    )
    assert result["cycle_stage"] == "crowded_late"
    assert "市场热度" in result["cycle_stage_reason"]


def test_cycle_stage_enrichment_matches_market_rows_by_theme_name():
    rows = [row()]
    enriched = enrich_mainline_rows_with_cycle_stage(rows, [{"theme": "AI算力/通信", "market_score": 80}])
    assert enriched[0]["cycle_stage"] == "main_rise_diffusion"
    summary = build_cycle_stage_summary(enriched)
    assert summary["scoring_version"] == "mainline_cycle_stage_v2"
    assert summary["stage_counts"]["main_rise_diffusion"] == 1


def test_cycle_stage_timing_shows_review_remaining_days():
    item = row()
    item["_cycle_event_contributors"] = [
        {"event_cluster_id": "policy-44d", "age_days": 44, "allocated_cluster_contribution": 0.4},
        {"event_cluster_id": "policy-5d", "age_days": 5, "allocated_cluster_contribution": 0.2},
    ]
    result = classify_mainline_cycle_stage(item, {"theme": "AI算力/通信", "market_score": 80})
    assert result["cycle_elapsed_days"] == 44
    assert result["cycle_review_window_days"] == 90
    assert result["cycle_review_remaining_days"] == 46
    assert result["cycle_recent_reinforcement_days"] == 5
    assert "距90天复核约46天" in result["cycle_time_window"]
