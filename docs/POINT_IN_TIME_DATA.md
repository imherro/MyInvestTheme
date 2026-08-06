# 点时政策数据

版本：`policy_time_provenance_v1`

政策文件的落款日只表示文件形成或签署日期，不证明市场当时已经能看到官方网页。因此，`document_date` 不能直接等同于历史信号可用时间。

每条新政策应记录：

- `document_date`：文件落款日期。
- `official_publish_at`：官方页面能够证明的首次公开时间。
- `first_seen_at`：本系统第一次发现并记录的时间。
- `crawl_at`：本次抓取或录入时间。
- `effective_at`：明确的生效时间，没有则为空。
- `revision_at`：页面或记录修订时间，没有则为空。

`point_in_time_available_at` 优先使用可信的 `official_publish_at`；无法证明官方发布时间时使用 `first_seen_at`。两者都缺失时，`point_in_time_basis=unavailable`、`time_provenance_status=legacy_unknown`，不得用于未来点时回测。

旧政策不会根据文件落款日倒推出首次发现时间，也不会把后来补录的字段伪装成历史时点已知信息。严重时间冲突、未来时间或不可解析时间会阻断新报告写入；落款日早于公开日只产生可审计警告。

规则配置：`config/policy_time_provenance_rules.json`。
