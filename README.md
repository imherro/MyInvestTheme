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
