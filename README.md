# MyInvestTheme

A-share mainline research workspace and read-only web system.

## Current Web App

The local web app reads generated research files from `research/mainline/` and does not mutate source data or trading state.

Run:

```powershell
python scripts/run_web.py --port 8012
```

Generate latest research:

```powershell
python scripts/generate_mainline_report.py --write
```

Daily after-close update:

```powershell
python scripts/daily_mainline_update.py
```

The daily updater is idempotent: if the latest complete Tushare trading date already has a report, it exits without creating a duplicate. The Codex recurring automation runs this command after market close.

Build derived two-level mainline backfill:

```powershell
python scripts/theme_taxonomy_v2.py --all --write
```

`theme_taxonomy_v2` is a deterministic derived observation layer. It remaps existing reports into finer second-level themes such as `机器人`, `智能汽车/自动驾驶/车路云`, `农业/养殖/猪周期`, `量子科技/量子计算`, and `可控核聚变`. It does not modify old reports and should not be read as the original conclusion at that historical time.

Policy scoring:

- Codex reviews official policy sources and maintains `data/policy_signals.json`.
- Before scoring, `policy_source_provenance_v2` from `config/policy_source_rules.json` validates policy source URL, official domain, source organization/domain match, required fields, publish date parseability, and stable content hash. Rejected policies are excluded before theme scoring.
- `policy_snapshot_integrity_v2` from `config/policy_snapshot_rules.json` compares each policy's `content_hash` with `data/policy_snapshot_registry.json`; an existing `policy_id` whose content changes without a revision note blocks new report writes.
- The report generator calculates `policy_score` from `policy_score_v2`, a deterministic rule score.
- Policy-to-theme mapping uses deterministic `theme_relevance_v2` rules from `config/themes.json`; old manual relevance values do not participate.
- Policy event clustering uses deterministic `policy_event_clustering_v2`; policy direction uses deterministic `policy_theme_stance_v2` from `config/policy_stance_rules.json`.
- Event-theme allocation uses deterministic `event_theme_allocation_v2` from `config/theme_allocation_rules.json` so one policy event has a finite contribution budget across matched themes.
- Mainline lifecycle uses deterministic `mainline_lifecycle_v2` from `config/mainline_lifecycle_rules.json` to classify themes as accelerating, sustained, emerging, single-event emerging, cooling, legacy tail, unknown, or dormant.
- Live report data guard uses deterministic `live_report_data_guard_v2` from `config/data_quality_rules.json` to keep optional market-context stages from crashing report generation when they return empty tables or missing columns.
- Report contract validation uses deterministic `mainline_contract_validator_v2` from `config/mainline_contract_rules.json` to check report sections, version fields, policy provenance, canonical ranking, score formulas, event allocation budgets, lifecycle counts, and legacy default-score leakage before a new report is written.
- Snapshot registry finalization uses deterministic `snapshot_registry_finalization_v2` from `config/snapshot_registry_finalization_rules.json`; written JSON/Markdown reports must carry an `updated` registry receipt rather than a pending registry state.
- Reproducibility manifest uses deterministic `reproducibility_manifest_v2` from `config/reproducibility_manifest_rules.json` to record Git metadata, code/config/input fingerprints, JSON/Markdown artifact hashes, runtime metadata, run arguments, and secret-safety status without reading or writing `.env` values.
- System drift control uses deterministic `system_drift_control_v2` from `config/system_drift_rules.json` and `data/golden_mainline_snapshot.json` to compare the current report with a golden snapshot without changing `mainline_score_v6`.
- Explainability trace uses deterministic `explainability_trace_graph_v2` to expose policy -> event -> theme -> mainline paths, event contribution breakdowns, and formula checks without changing scores, ranking, contract validation, drift, or snapshots.
- Counterfactual simulation uses deterministic `counterfactual_mainline_simulator_v2`, `mainline_sensitivity_engine_v2`, and `core_driver_detector_v2` to simulate removing a policy or event on an in-memory report copy without writing reports or changing real scores, ranking, contract validation, drift, or snapshots.
- System consistency oracle uses deterministic `system_consistency_oracle_v2`, `multi_run_executor_v2`, and `divergence_analyzer_v2` to repeat same-report projections and classify score, ranking, allocation, lifecycle, provenance, snapshot, and explainability divergence without writing reports or changing real outputs.
- Two-level taxonomy backfill uses deterministic `theme_taxonomy_v2_backfill_v1` from `config/theme_taxonomy_v2.json` to split coarse themes and surface independent themes that the original 8-bucket market view could hide. The output is written to `research/mainline_taxonomy_v2/` and is marked as derived/backfilled.
- `theme_score_v2_raw` is the undeduplicated policy-theme comparison score, `theme_score_v3_dedup` is the deduplicated score before direction adjustment, `theme_score_v4_stance_adjusted` is the direction-adjusted score before allocation, `theme_score_v5` is the event-theme allocated score, and `mainline_score_v6` is the default lifecycle-adjusted policy-theme score.
- Default canonical mainline score is `mainline_score_v6`.
- `mainline_score_v6 = theme_score_v5 * lifecycle_quality_multiplier`.
- `legacy_evidence_score` is a market-heat observation comparison field and is not the policy-mainline ranking score.
- See `docs/POLICY_SIGNALS.md` for the extraction schema and scoring rules.

Validate report contract:

```powershell
python scripts/mainline_contract_validator.py --latest
python scripts/mainline_contract_validator.py --path research/mainline/mainline_review_2026-06-22_155506.json
python scripts/reproducibility_manifest.py --latest
python scripts/reproducibility_manifest.py --path research/mainline/mainline_review_2026-06-22_180013.json
python scripts/golden_snapshot_builder.py --latest --write
python scripts/system_drift_detector.py --latest
python scripts/explainability_trace.py --latest --theme ai_compute_communications
python scripts/counterfactual_simulator.py --latest --remove-policy ndrc-2026-06-03-intelligent-economy
python scripts/mainline_sensitivity_engine.py --latest --theme ai_compute_communications
python scripts/core_driver_detector.py --latest
python scripts/system_consistency_oracle.py --latest --runs 10
```

Open:

- Latest research: http://127.0.0.1:8012/
- Historical research: http://127.0.0.1:8012/reports
- API directory: http://127.0.0.1:8012/api
- Swagger UI: http://127.0.0.1:8012/docs
- ReDoc: http://127.0.0.1:8012/redoc
- OpenAPI schema: http://127.0.0.1:8012/openapi.json
- Homepage content API: http://127.0.0.1:8012/api/index
- Latest report API: http://127.0.0.1:8012/api/latest
- Taxonomy v2 latest API: http://127.0.0.1:8012/api/taxonomy-v2
- Taxonomy v2 score series API: http://127.0.0.1:8012/api/taxonomy-v2/score-series
- Drift status API: http://127.0.0.1:8012/api/drift
- Golden snapshot API: http://127.0.0.1:8012/api/golden-snapshot
- Compare report API: http://127.0.0.1:8012/api/compare
- Theme explanation API: http://127.0.0.1:8012/api/explain/theme/ai_compute_communications
- Remove-policy simulation API: http://127.0.0.1:8012/api/simulate/remove-policy/ndrc-2026-06-03-intelligent-economy
- Remove-event simulation API: http://127.0.0.1:8012/api/simulate/remove-event/event_20260603_ndrc_ndrc_2026_06_03_intelligent_economy
- Theme sensitivity API: http://127.0.0.1:8012/api/sensitivity/theme/ai_compute_communications
- Core drivers API: http://127.0.0.1:8012/api/core-drivers
- System consistency oracle API: http://127.0.0.1:8012/api/consistency/oracle?runs=10

## API Contract

`GET /api` is the unified read-only API directory. It does not load reports, recalculate research, write files, trade, or synchronize external systems. The response contains:

- `system_name`, `version`, `description`, and `base_url`
- `docs`: `/docs`, `/redoc`, and `/openapi.json`
- `recommended_entrypoints`
- `safety`: read-only boundaries, including no recompute, no writes, no trading, and no sync
- `groups`: endpoint groups for documentation entry points, current data, historical data, analysis results, and system status
- `total_endpoints`

Every listed endpoint includes `method`, `path`, `purpose`, `parameters`, `returns`, and `read_only`.

The homepage endpoint returns the main content used by `/`:

- `latest_report`
- `canonical_mainline_summary`
- `contract_validation_summary`
- `policy_provenance_summary`
- `policy_snapshot_summary`
- `snapshot_registry_update_summary`
- `reproducibility_manifest`
- `mainline_ranking`
- `taxonomy_v2_backfill`
- `taxonomy_v2_ranking`
- `taxonomy_v2_parent_groups`
- `theme_ranking`
- `legacy_theme_ranking`
- `market`
- `score_series`
- `reports`
- `markdown`

`mainline_ranking` is the policy-mainline list. `theme_ranking` and `legacy_theme_ranking` are compatibility market-heat observation lists and are not the policy-mainline ranking.
`taxonomy_v2_ranking` is a derived second-level observation list. Its `combined_score` combines report-local normalized policy score, market heat, and confidence. It is useful for seeing whether a coarse bucket is hiding independent themes, but it is not a replacement for original policy-mainline ranking.
In `score_series`, `score` and `default_score` both use `mainline_score_v6`; market-heat observation values are exposed only as `legacy_*` fields.
`/api/taxonomy-v2/score-series` returns the derived second-level historical series. It is generated from existing reports and backfill files only; it does not trigger market-data collection or report recomputation.
`/api/index`, `/api/latest`, and `/api/health` expose the latest `contract_validation_summary` or status fields. Contract errors block new JSON/Markdown writes; warnings are retained for audit.
`data_quality_summary` is also exposed by `/api/latest`, `/api/index`, and `/api/health`. Required data stages block writes if they fail; optional market-context stages can degrade with schema fallback and do not change `mainline_score_v6`.
`policy_provenance_summary` is exposed by `/api/latest` and `/api/index`; `/api/health` exposes the latest provenance status and rejected/degraded counts.
`policy_snapshot_summary` is exposed by `/api/latest` and `/api/index`; `/api/health` exposes the latest snapshot status and silent-change/duplicate-conflict counts.
`snapshot_registry_update_summary` is exposed by `/api/latest` and `/api/index`; `/api/health` exposes the latest registry update status and updated registry hash.
`reproducibility_manifest` is exposed by `/api/latest` and `/api/index`; `/api/health` exposes the latest reproducibility status, Git commit, and JSON artifact hash.
`/api/explain/theme/{theme_id}` exposes the latest theme explanation graph. Pass `?report_id=mainline_review_YYYY-MM-DD_HHMMSS` to inspect a historical report. The response contains `trace_graph`, `top_policy_paths`, `event_breakdowns`, and validation checks for contribution sums and the `mainline_score_v6` formula.
`/api/simulate/remove-policy/{policy_id}` and `/api/simulate/remove-event/{event_cluster_id}` return counterfactual overlay results with `baseline_ranking`, `counterfactual_ranking`, `theme_impacts`, and impact summary fields. `/api/sensitivity/theme/{theme_id}` ranks a theme's policy and event sensitivity. `/api/core-drivers` ranks policy-level total mainline impact. All simulation endpoints support `?report_id=<report_id>` and are read-only.
`/api/consistency/oracle?runs=10` repeats deterministic same-report projections and returns `consistency_status`, score/allocation variance, ranking changes, divergence list, root cause attribution, and per-run output hashes. It supports `?report_id=<report_id>` and is read-only.

The latest report endpoint returns the newest research report artifact:

- `report_id`
- `result`, containing the full latest research JSON from `research/mainline/`

## Development Sync Rule

After each completed development task:

1. Run focused validation.
2. Confirm `.env`, `temp/`, logs, caches, and local runtime files are ignored.
3. Commit the completed task.
4. Push `main` to `https://github.com/imherro/MyInvestTheme.git`.
