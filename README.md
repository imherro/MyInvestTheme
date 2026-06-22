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
- The report generator calculates `policy_score` from `policy_score_v2`, a deterministic rule score.
- Policy-to-theme mapping uses deterministic `theme_relevance_v2` rules from `config/themes.json`; old manual relevance values do not participate.
- Policy event clustering uses deterministic `policy_event_clustering_v2`; policy direction uses deterministic `policy_theme_stance_v2` from `config/policy_stance_rules.json`.
- Event-theme allocation uses deterministic `event_theme_allocation_v2` from `config/theme_allocation_rules.json` so one policy event has a finite contribution budget across matched themes.
- `theme_score_v2_raw` is the undeduplicated policy-theme comparison score, `theme_score_v3_dedup` is the deduplicated score before direction adjustment, `theme_score_v4_stance_adjusted` is the direction-adjusted score before allocation, and `theme_score_v5` is the default policy-theme score after event-theme allocation.
- Mainline score is `market_score * 85% + policy_score * 15%`.
- See `docs/POLICY_SIGNALS.md` for the extraction schema and scoring rules.

Open:

- Latest research: http://127.0.0.1:8012/
- Historical research: http://127.0.0.1:8012/reports
- Homepage content API: http://127.0.0.1:8012/api/index
- Latest report API: http://127.0.0.1:8012/api/latest

## API Contract

The homepage endpoint returns the main content used by `/`:

- `latest_report`
- `theme_ranking`
- `market`
- `score_series`
- `reports`
- `markdown`

The latest report endpoint returns the newest research report artifact:

- `report_id`
- `result`, containing the full latest research JSON from `research/mainline/`

## Development Sync Rule

After each completed development task:

1. Run focused validation.
2. Confirm `.env`, `temp/`, logs, caches, and local runtime files are ignored.
3. Commit the completed task.
4. Push `main` to `https://github.com/imherro/MyInvestTheme.git`.
