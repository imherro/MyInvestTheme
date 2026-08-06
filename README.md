# MyInvestTheme

A-share era-mainline and lifecycle research workspace with a read-only web system.

The recommended research layer is `era_mainline_model_v2`. It combines policy persistence, industry validation, market confirmation, and official strategic narrative diffusion, then applies evidence windows and explicit lifecycle gates. It is a research judgment system, not an expected-return model or investment-advice system.

The existing canonical mainline layer remains available as the compatible policy-theme evidence view and as an input to the era-mainline model.

## Current Web App

The local web app reads generated research files from `research/mainline/` and does not mutate source data or trading state.

Run:

```powershell
python scripts/run_web.py --port 8012
```

Generate latest research:

```powershell
python scripts/generate_mainline_report.py --write
python scripts/generate_era_mainline_report.py --write
```

Daily after-close update:

```powershell
python scripts/daily_mainline_update.py
```

The daily updater is idempotent: if the latest complete Tushare trading date already has a report, it exits without creating a duplicate. The Codex recurring automation runs this command after market close.

## Era Mainline Model

- `era_mainline_rules_v2` configures policy 40%, industry 25%, market 25%, and official narrative 10%. Unknown industry observations remain `null`; every report exposes configured and effective weights.
- `policy_event_type_rules_v1` separates national plans, implementation plans, funding, projects, standards, pilots, restrictions, risk control, and exits. Policy conviction combines long-term level, execution, cross-department breadth, reinforcement, novelty, and restriction.
- `industry_indicator_mapping_v1` defines expandable proxy indicators. Missing observations display `产业验证不足` and never become a false zero.
- `market_confirmation_v1` turns existing SW, THS, ETF, limit-up, breadth and flow evidence into sustained market confirmation. It verifies a direction but cannot create an era mainline alone.
- `narrative_momentum_v1` uses official policy events, cross-department breadth and secondary-theme expansion rather than raw news counts.
- `era_lifecycle_rules_v2` derives the 12 stages from condition duration, consecutive observations, dimension evidence, score changes, and ranking stability. Report count no longer advances a theme one stage at a time.
- `era_confidence_rules_v1` separates current-state, lifecycle-stage, and lifecycle-date confidence. Missing industry, degraded point-in-time provenance, short history, and sparse observations impose explicit caps.
- Historical results are retrospective replays under the current model. A left-censored theme has no invented start date; `start_date_status=before_available_history` identifies that boundary.
- `official_narrative_diffusion_v1` measures cross-department strategic wording, terminology, and subtheme coverage. It does not reuse the policy total or represent public-opinion heat.
- Start dates require a forming-stage observation and cannot precede available evidence. Ending requires multiple weak observations; neither a short market correction nor a 90-day policy gap ends a mainline by itself.
- Reports are saved under `research/era_mainline/`. Existing `research/mainline/` reports and APIs remain readable.

Build derived two-level mainline backfill:

```powershell
python scripts/theme_taxonomy_v2.py --all --write
```

`theme_taxonomy_v2` is a deterministic derived observation layer. It remaps existing reports into finer second-level themes such as `机器人`, `智能汽车/自动驾驶/车路云`, `农业/养殖/猪周期`, `量子科技/量子计算`, and `可控核聚变`. It does not modify old reports and should not be read as the original conclusion at that historical time.

Policy scoring:

- Phase-one policy radar trust rules add point-in-time availability, field provenance isolation, complete candidate decisions, explicit score semantics, and trading-calendar freshness checks. These controls reduce identifiable look-ahead and label-loop risks; legacy records with missing historical timestamps remain explicitly degraded rather than backfilled with invented times.
- `policy_time_provenance_v1` from `config/policy_time_provenance_rules.json` distinguishes document date, official publication, first system sighting, crawl, effective and revision times. Verified `official_publish_at` is the preferred point-in-time availability; otherwise the system uses `first_seen_at`.
- `policy_field_provenance_v1` and `theme_relevance_input_v1` isolate official/factual fields from LLM inference. Production mode is `strict_point_in_time`; `beneficiary_chain`, `related_industries`, `research_notes`, and `analyst_tags` cannot enter default theme relevance.
- `policy_candidate_audit_v1` requires every selected policy to have an included candidate record with a matching content hash in `data/policy_candidates.jsonl`.
- `data_freshness_guard_v1` evaluates staleness with the trading calendar and uses `data/policy_scan_status.json:last_scan_completed_at` for policy-scan freshness. A candidate's `first_seen_at` is point-in-time evidence, not a crawler heartbeat.

- Codex reviews official policy sources and maintains `data/policy_signals.json`.
- Before scoring, `policy_source_provenance_v2` from `config/policy_source_rules.json` validates policy source URL, official domain, source organization/domain match, required fields, publish date parseability, and stable content hash. Rejected policies are excluded before theme scoring.
- `policy_snapshot_integrity_v2` from `config/policy_snapshot_rules.json` compares each policy's `content_hash` with `data/policy_snapshot_registry.json`; an existing `policy_id` whose content changes without a revision note blocks new report writes.
- The report generator calculates `policy_score` from `policy_score_v2`, a deterministic rule score.
- Policy-to-theme mapping uses deterministic `theme_relevance_strict_v1` rules from `config/themes.json` and `config/theme_relevance_input_rules.json`; inference labels are comparison-only.
- Policy event clustering uses deterministic `policy_event_clustering_v2`; policy direction uses deterministic `policy_theme_stance_v2` from `config/policy_stance_rules.json`.
- Event-theme allocation uses deterministic `event_theme_allocation_v2` from `config/theme_allocation_rules.json` so one policy event has a finite contribution budget across matched themes.
- Mainline lifecycle uses deterministic `mainline_lifecycle_v2` from `config/mainline_lifecycle_rules.json` to classify themes as accelerating, sustained, emerging, single-event emerging, cooling, legacy tail, unknown, or dormant.
- Live report data guard uses deterministic `live_report_data_guard_v2` from `config/data_quality_rules.json` to keep optional market-context stages from crashing report generation when they return empty tables or missing columns.
- Report contract validation uses deterministic `mainline_contract_validator_v3` from `config/mainline_contract_rules.json` to check report sections, point-in-time policy use, field provenance, candidate coverage, strict relevance, freshness, score semantics, canonical ranking and score formulas before a new report is written.
- Snapshot registry finalization uses deterministic `snapshot_registry_finalization_v2` from `config/snapshot_registry_finalization_rules.json`; written JSON/Markdown reports must carry an `updated` registry receipt rather than a pending registry state.
- Reproducibility manifest uses deterministic `reproducibility_manifest_v2` from `config/reproducibility_manifest_rules.json` to record Git metadata, code/config/input fingerprints, JSON/Markdown artifact hashes, runtime metadata, run arguments, and secret-safety status without reading or writing `.env` values.
- System drift control uses deterministic `system_drift_control_v2` from `config/system_drift_rules.json` and `data/golden_mainline_snapshot.json` to compare the current report with a golden snapshot without changing `mainline_score_v6`.
- Explainability trace uses deterministic `explainability_trace_graph_v2` to expose policy -> event -> theme -> mainline paths, event contribution breakdowns, and formula checks without changing scores, ranking, contract validation, drift, or snapshots.
- Counterfactual simulation uses deterministic `counterfactual_mainline_simulator_v2`, `mainline_sensitivity_engine_v2`, and `core_driver_detector_v2` to simulate removing a policy or event on an in-memory report copy without writing reports or changing real scores, ranking, contract validation, drift, or snapshots.
- System consistency oracle uses deterministic `system_consistency_oracle_v2`, `multi_run_executor_v2`, and `divergence_analyzer_v2` to repeat same-report projections and classify score, ranking, allocation, lifecycle, provenance, snapshot, and explainability divergence without writing reports or changing real outputs.
- Two-level taxonomy backfill uses deterministic `theme_taxonomy_v2_backfill_v1` from `config/theme_taxonomy_v2.json` to split coarse themes and surface independent themes that the original 8-bucket market view could hide. The output is written to `research/mainline_taxonomy_v2/` and is marked as derived/backfilled.
- `theme_score_v2_raw` is the undeduplicated policy-theme comparison score, `theme_score_v3_dedup` is the deduplicated score before direction adjustment, `theme_score_v4_stance_adjusted` is the direction-adjusted score before allocation, `theme_score_v5` is the event-theme allocated score, and `mainline_score_v6` is the default lifecycle-adjusted policy-theme score.
- Default canonical field is `policy_theme_conviction_score`; `mainline_score_v6` remains an equal-value compatibility field.
- `mainline_score_v6 = theme_score_v5 * lifecycle_quality_multiplier`.
- `policy_theme_conviction_score` means policy evidence strength, persistence and breadth. It is not expected return, price direction, a buy signal or position-sizing guidance.
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
python scripts/migrate_policy_candidates_v1.py
```

The candidate migration defaults to dry-run. Use `--write` only when intentionally creating missing `legacy_imported` records for old selected policies; it never fabricates `discovered_at`.

Open:

- Latest research: http://127.0.0.1:8012/
- Era mainline: http://127.0.0.1:8012/era-mainline
- Era timeline: http://127.0.0.1:8012/era-timeline
- Era transitions: http://127.0.0.1:8012/era-transitions
- Historical research: http://127.0.0.1:8012/reports
- API directory: http://127.0.0.1:8012/api
- Swagger UI: http://127.0.0.1:8012/docs
- ReDoc: http://127.0.0.1:8012/redoc
- OpenAPI schema: http://127.0.0.1:8012/openapi.json
- Homepage content API: http://127.0.0.1:8012/api/index
- Latest report API: http://127.0.0.1:8012/api/latest
- Era latest API: http://127.0.0.1:8012/api/era-mainline/latest
- Era history API: http://127.0.0.1:8012/api/era-mainline/history
- Era transitions API: http://127.0.0.1:8012/api/era-mainline/transitions
- Taxonomy v2 latest API: http://127.0.0.1:8012/api/taxonomy-v2
- Taxonomy v2 score series API: http://127.0.0.1:8012/api/taxonomy-v2/score-series
- Drift status API: http://127.0.0.1:8012/api/drift
- Golden snapshot API: http://127.0.0.1:8012/api/golden-snapshot
- Compare report API: http://127.0.0.1:8012/api/compare
- Theme explanation API: http://127.0.0.1:8012/api/explain/theme/ai_compute_communications
- Policy audit API: http://127.0.0.1:8012/api/policies/{policy_id}/audit
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
- `policy_time_provenance_summary`
- `policy_candidate_summary`
- `field_provenance_summary`
- `theme_relevance_input_summary`
- `data_freshness_summary`
- `score_semantics`

`mainline_ranking` is the policy-mainline list. `theme_ranking` and `legacy_theme_ranking` are compatibility market-heat observation lists and are not the policy-mainline ranking.
`taxonomy_v2_ranking` is a derived second-level observation list. Its `combined_score` combines report-local normalized policy score, market heat, and confidence. It is useful for seeing whether a coarse bucket is hiding independent themes, but it is not a replacement for original policy-mainline ranking.
In `score_series`, `score` and `default_score` both use `mainline_score_v6`; market-heat observation values are exposed only as `legacy_*` fields.
`/api/taxonomy-v2/score-series` returns the derived second-level historical series. It is generated from existing reports and backfill files only; it does not trigger market-data collection or report recomputation.
`/api/index`, `/api/latest`, and `/api/health` expose point-in-time, candidate audit, field provenance, strict relevance, freshness and contract status. Contract errors block new JSON/Markdown writes; warnings are retained for audit.
`/api/policies/{policy_id}/audit` returns one policy's time basis, field provenance, candidate decision, content hash, strict/inference comparison, report inclusion and point-in-time basis. It is read-only.
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
