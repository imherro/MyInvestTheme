# 政策字段来源

版本：`policy_field_provenance_v1`、`theme_relevance_input_v1`

字段分为三层：官方原文或元数据、事实性抽取、研究推断。`field_provenance` 为每个字段记录 `source_type`、模型、生成时间和证据字段。

生产模式固定为 `strict_point_in_time`。默认主题相关度只允许官方字段、确定性抽取和事实性 LLM 抽取进入评分。以下推断字段默认禁止：

- `beneficiary_chain`
- `related_industries`
- `research_notes`
- `analyst_tags`

系统同时输出：

- `theme_relevance_strict`：生产排序使用，不含推断标签。
- `theme_relevance_with_inference`：仅用于研究对照。
- `inference_lift`：推断字段带来的相关度差值。

当 `inference_lift` 达到配置阈值时输出 `HIGH_INFERENCE_DEPENDENCY`。该告警不会让推断字段进入默认排序。

规则配置：`config/field_provenance_rules.json`、`config/theme_relevance_input_rules.json`。
