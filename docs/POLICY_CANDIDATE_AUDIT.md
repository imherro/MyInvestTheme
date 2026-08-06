# 候选政策审计

版本：`policy_candidate_audit_v1`

`data/policy_candidates.jsonl` 保存所有候选政策的来源、发现信息、内容哈希和纳入决策。进入 `data/policy_signals.json` 的政策必须存在一条 `decision=included` 且内容哈希一致的候选记录。

候选决策包括纳入、排除、待审、重复、不可访问、来源无效、非政策、超范围和内容不足。排除记录保留在候选日志中，不进入政策信号库。

旧政策迁移：

```powershell
python scripts/migrate_policy_candidates_v1.py
python scripts/migrate_policy_candidates_v1.py --write
```

默认只预览；只有 `--write` 才落盘。迁移幂等，不覆盖已有记录。迁移记录标记为 `review_status=legacy_imported`，`discovered_at` 保持为空，不伪造历史发现时间。

缺候选记录、决策缺失、哈希不一致或重复候选决策冲突会阻断新报告写入。
