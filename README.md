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
- `theme_score_v2_raw` is the undeduplicated policy-theme comparison score, `theme_score_v3_dedup` is the deduplicated score before direction adjustment, `theme_score_v4_stance_adjusted` is the direction-adjusted score before allocation, `theme_score_v5` is the event-theme allocated score, and `mainline_score_v6` is the default lifecycle-adjusted policy-theme score.
- Default canonical mainline score is `mainline_score_v6`.
- `mainline_score_v6 = theme_score_v5 * lifecycle_quality_multiplier`.
- `legacy_evidence_score` is a market-context comparison field and is not the canonical mainline ranking score.
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
```

Open:

- Latest research: http://127.0.0.1:8012/
- Historical research: http://127.0.0.1:8012/reports
- Homepage content API: http://127.0.0.1:8012/api/index
- Latest report API: http://127.0.0.1:8012/api/latest
- Drift status API: http://127.0.0.1:8012/api/drift
- Golden snapshot API: http://127.0.0.1:8012/api/golden-snapshot
- Compare report API: http://127.0.0.1:8012/api/compare
- Theme explanation API: http://127.0.0.1:8012/api/explain/theme/ai_compute_communications

## API Contract

The homepage endpoint returns the main content used by `/`:

- `latest_report`
- `canonical_mainline_summary`
- `contract_validation_summary`
- `policy_provenance_summary`
- `policy_snapshot_summary`
- `snapshot_registry_update_summary`
- `reproducibility_manifest`
- `mainline_ranking`
- `theme_ranking`
- `legacy_theme_ranking`
- `market`
- `score_series`
- `reports`
- `markdown`

`mainline_ranking` is the canonical default mainline list. `theme_ranking` and `legacy_theme_ranking` are compatibility market-context lists and are not the default mainline ranking.
In `score_series`, `score` and `default_score` both use `mainline_score_v6`; old market-context values are exposed only as `legacy_*` fields.
`/api/index`, `/api/latest`, and `/api/health` expose the latest `contract_validation_summary` or status fields. Contract errors block new JSON/Markdown writes; warnings are retained for audit.
`data_quality_summary` is also exposed by `/api/latest`, `/api/index`, and `/api/health`. Required data stages block writes if they fail; optional market-context stages can degrade with schema fallback and do not change `mainline_score_v6`.
`policy_provenance_summary` is exposed by `/api/latest` and `/api/index`; `/api/health` exposes the latest provenance status and rejected/degraded counts.
`policy_snapshot_summary` is exposed by `/api/latest` and `/api/index`; `/api/health` exposes the latest snapshot status and silent-change/duplicate-conflict counts.
`snapshot_registry_update_summary` is exposed by `/api/latest` and `/api/index`; `/api/health` exposes the latest registry update status and updated registry hash.
`reproducibility_manifest` is exposed by `/api/latest` and `/api/index`; `/api/health` exposes the latest reproducibility status, Git commit, and JSON artifact hash.
`/api/explain/theme/{theme_id}` exposes the latest theme explanation graph. Pass `?report_id=mainline_review_YYYY-MM-DD_HHMMSS` to inspect a historical report. The response contains `trace_graph`, `top_policy_paths`, `event_breakdowns`, and validation checks for contribution sums and the `mainline_score_v6` formula.

The latest report endpoint returns the newest research report artifact:

- `report_id`
- `result`, containing the full latest research JSON from `research/mainline/`

## Development Sync Rule

After each completed development task:

1. Run focused validation.
2. Confirm `.env`, `temp/`, logs, caches, and local runtime files are ignored.
3. Commit the completed task.
4. Push `main` to `https://github.com/imherro/MyInvestTheme.git`.
