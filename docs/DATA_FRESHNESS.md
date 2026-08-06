# 数据新鲜度

版本：`data_freshness_guard_v1`

数据新鲜度根据交易日历判断，不按自然日计算。系统分别报告行情基准日延迟和政策抓取延迟：

- `expected_latest_trade_date`
- `actual_basis_date`
- `stale_trading_days`
- `latest_policy_first_seen_at`
- `policy_ingestion_lag_hours`
- `market_data_lag_hours`

状态为 `fresh`、`degraded`、`stale` 或 `unknown`。周末和节假日不会被误算为交易日延迟。

当状态为 `stale` 时，API 仍可读取历史数据，但页面必须显示过期提示，报告文案降级为“最近一次有效数据截至 YYYY-MM-DD。以下结果为历史观察，不代表当前状态。”，不得继续使用“当前主线”或“最新主线”等确定性表述。是否阻断报告写入由 `config/data_freshness_rules.json` 控制。

`policy_theme_conviction_score` 表示政策支持证据的强度、持续性和广度，不表示未来收益率、价格方向、买入信号或仓位建议。
