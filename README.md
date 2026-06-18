# MyInvestTheme

A-share mainline research workspace and read-only web system.

## Current Web App

The local web app reads generated research files from `research/mainline/` and does not mutate source data or trading state.

Run:

```powershell
python scripts/run_web.py --port 8012
```

Open:

- Latest research: http://127.0.0.1:8012/
- Historical research: http://127.0.0.1:8012/reports
- Latest shadow-account API: http://127.0.0.1:8012/api/shadow-account/latest

## API Contract

The latest shadow-account endpoint returns:

- `read_only=true`
- `ratio_only=true`
- `contains_trade_orders=false`
- `theme_signals` with theme rank, stage, score, evidence count, top index/ETF proxies, and score-weight ratio
- `latest_result` with the full latest research JSON

## Development Sync Rule

After each completed development task:

1. Run focused validation.
2. Confirm `.env`, `temp/`, logs, caches, and local runtime files are ignored.
3. Commit the completed task.
4. Push `main` to `https://github.com/imherro/MyInvestTheme.git`.
