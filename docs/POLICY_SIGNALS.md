# Policy Signals

The daily automation keeps policy research separate from market scoring.

## Workflow

1. Codex reviews official policy sources after market close.
2. Codex updates `data/policy_signals.json` only when there is a relevant new or corrected official signal.
3. `scripts/policy_provenance.py` validates source provenance through deterministic `policy_source_provenance_v2`; rejected policies are excluded before scoring.
4. `scripts/policy_snapshot_integrity.py` compares current policy content hashes with `data/policy_snapshot_registry.json`; unexplained historical content changes block report writes.
5. `scripts/generate_mainline_report.py` reads the included policy set and recalculates `policy_score_v2` deterministically for the report basis date.
6. `scripts/theme_relevance.py` maps policy signals to mainline themes through deterministic `theme_relevance_v2` rules from `config/themes.json`.
7. `scripts/policy_event_clustering.py` clusters duplicate policy signals into deterministic policy events.
8. `scripts/policy_stance.py` identifies whether each policy supports, mildly supports, neutrally mixes, mildly restricts, or restricts each matched theme.
9. `scripts/theme_allocation.py` allocates one event's finite contribution budget across matched themes through deterministic `event_theme_allocation_v2`.
10. `scripts/mainline_lifecycle.py` classifies each theme's current lifecycle through deterministic `mainline_lifecycle_v2`.
11. `scripts/canonical_mainline.py` publishes the canonical default output contract through deterministic `canonical_mainline_output_v2`.
12. `scripts/daily_mainline_update.py` commits the policy store together with any new report.

## Official Sources

Prefer official sources only:

- State Council and ministry websites
- CSRC, exchanges, NDRC, MIIT, NEA and other regulators
- Official policy PDFs, press releases, action plans, notices and formal interpretations

Avoid media rewrites, brokerage interpretations and unsourced reposts for scoring.

## Policy Source Provenance V2

`policy_source_provenance_v2` is deterministic. It does not call an LLM, embeddings, external search, or web scraping. It only validates fields already stored in `data/policy_signals.json`.

Configuration lives in `config/policy_source_rules.json`:

- official domain allowlist for State Council, NDRC, MOF, MIIT, CSRC, PBOC, NEA, MOST, MOFCOM and NDA
- official suffix rule `.gov.cn`
- source organization aliases such as `国家发展改革委 -> ndrc` and `中国证监会 -> csrc`
- required inclusion fields: `policy_id`, `title`, `source_org`, `source_url`, `publish_date`, `summary`, `key_points`
- recommended fields: `beneficiary_chain`, `related_industries`
- compatibility aliases for the current store: `id`, `source`, `url`, `published_date`

Status rules:

- `verified`: source URL exists, domain is official, source organization matches its official domain, required fields are complete, and publish date is parseable.
- `degraded`: official URL, required fields, and date pass, but recommended fields are missing or the official source match is weak. The policy remains included.
- `rejected`: missing URL, non-official domain, required field missing, unparseable date, missing source organization, or source organization/domain conflict. The policy is excluded before `policy_score_v2`, `theme_relevance_v2`, event clustering, stance, allocation, lifecycle and canonical mainline output.

Each provenance row records `policy_id`, normalized source organization, normalized URL, source domain, official-domain match, source-organization/domain match, date parseability, missing fields, `sha256` content hash, provenance status, inclusion status, exclusion reason and reasons.

Report fields:

```text
policy_provenance_summary.scoring_version = policy_source_provenance_v2
policy_provenance_summary.raw_policy_count
policy_provenance_summary.included_policy_count
policy_provenance_summary.excluded_policy_count
policy_provenance_summary.verified_count
policy_provenance_summary.degraded_count
policy_provenance_summary.rejected_count
policy_summary.signals_count = policy_provenance_summary.included_policy_count
```

## Policy Snapshot Integrity V2

`policy_snapshot_integrity_v2` is deterministic. It does not call an LLM, embeddings, external search, market data, or web scraping. It only compares the policy store and provenance `content_hash` values against the local snapshot registry.

Configuration lives in `config/policy_snapshot_rules.json`:

- registry path: `data/policy_snapshot_registry.json`
- identity fields: `policy_id`, `source_url`, `content_hash`
- revision note fields: `revision_note`, `content_revision_note`, `change_note`
- revision id fields: `revision_id`, `content_revision_id`
- blockers: unexplained content changes, conflicting duplicate `policy_id`, conflicting duplicate `source_url`
- warnings: removed policy and content change with revision note

Snapshot statuses:

- `new`: `policy_id` is not present in the registry.
- `unchanged`: current `content_hash` equals the registry hash.
- `changed_with_revision_note`: current `content_hash` differs and the policy includes a revision note. This is allowed but warned.
- `changed_without_revision_note`: current `content_hash` differs and the policy has no revision note. This blocks new report writes.
- `removed_from_current_store`: a registry policy is absent from the current raw store. This is a warning.
- `duplicate_policy_id_conflict`: the current raw store has the same `policy_id` with conflicting `source_url` or `content_hash`. This blocks writes.
- `duplicate_source_url_conflict`: the current raw store has the same `source_url` across multiple `policy_id` values with conflicting `content_hash`. This blocks writes.

Registry update rule:

```text
Only after JSON write success
and Markdown write success
and contract_validation_summary.status == pass
and policy_snapshot_summary.status in pass/degraded
then update data/policy_snapshot_registry.json
```

The snapshot layer does not change `policy_score_v2`, `theme_score_v5`, `mainline_score_v6`, or `mainline_ranking`. It only protects reproducibility and auditability.

## Signal Schema

Each signal in `data/policy_signals.json` must include:

- `id`: stable lowercase id
- `title`
- `source`
- `published_date`: `YYYY-MM-DD`
- `authority_level`: one of `state_council`, `multi_ministry`, `national_ministry`, `national_regulator`, `exchange`, `provincial`, `industry_association`
- `economic_scope`: one of `national`, `cross_industry`, `industry`, `regional`, `local_pilot`
- `url`
- `authority_score`: 0-1, rule-derived
- `actionability_score`: 0-1, keyword-rule-derived
- `economic_scope_score`: 0-1, scope-rule-derived
- `time_decay_score`: 0-1, `exp(-days / 30)` snapshot at policy store update time
- `policy_score_v2`: 0-1, deterministic score snapshot at policy store update time
- `summary`
- `key_points`
- `beneficiary_chain`
- `related_industries`
- `evidence`: concise extracted reason

Theme names must match the mainline themes in `scripts/generate_mainline_report.py`.

Deprecated fields are not allowed in the policy store and are ignored by scoring logic if present in older historical artifacts:

- `specificity`
- `implementation_path`
- `confidence`
- `themes.relevance`

## LLM Extraction Prompt

Use this prompt when Codex updates policy signals:

```text
Read only official Chinese policy/regulator sources from the last 90 days.
Extract A-share mainline policy signals into the JSON schema in docs/POLICY_SIGNALS.md.
Do not score policy quality or theme relevance with an LLM. Only extract official source facts, authority_level, economic_scope, key_points, beneficiary_chain, related_industries and evidence.
Do not invent policies. If source text is broad or slogan-like, keep evidence concise and let Python scoring rules handle the score.
If the policy is already present in data/policy_signals.json, update only when the source, policy facts, beneficiary chain, related industries, or extracted evidence materially improves.
```

## Scoring

Python scoring is deterministic:

- authority_score 35%
- actionability_score 25%
- economic_scope_score 20%
- time_decay_score 20%

Rules:

- `authority_score`: State Council or central documents 1.0; NDRC, MOF or CSRC 0.85; multi-ministry 0.8; single ministry 0.7; provincial 0.5; municipal 0.3.
- `actionability_score`: adds 0.3 for funds/investment/budget/special funds, 0.3 for projects/engineering/construction/demonstration zones, 0.2 for KPI/assessment/targets, and 0.2 for explicit time nodes or `20XX` deadlines; capped at 1.0.
- `economic_scope_score`: national 1.0; cross-industry 0.8; single industry 0.6; regional 0.4; local pilot 0.3.
- `time_decay_score`: `exp(-days / 30)`, calculated from `published_date` to the report basis date. Missing component inputs fall back to 0.5.

## Theme Relevance V2

Theme relevance is deterministic and does not use old manual `relevance` values, embeddings, or LLM scoring.

Configuration lives in `config/themes.json`. Each theme defines:

- `theme_id`
- `theme_name`
- `core_keywords`
- `industry_keywords`
- `beneficiary_keywords`
- `policy_objectives`
- `negative_keywords`

`scripts/theme_relevance.py` calculates:

- `keyword_score`: core keyword hit +0.25, industry keyword hit +0.15, beneficiary keyword hit +0.20, capped at 1.0.
- `beneficiary_score`: beneficiary field hit +0.30, industry field hit +0.20, core field hit +0.15, capped at 1.0.
- `policy_objective_score`: objective hit +0.25, capped at 1.0.
- `negative_filter_score`: no hit 1.0, one hit 0.7, two hits 0.4, three or more hits 0.2.

Formula:

```text
base_relevance =
  0.45 * keyword_score +
  0.35 * beneficiary_score +
  0.20 * policy_objective_score

relevance_score_v2 = base_relevance * negative_filter_score
```

Only `relevance_score_v2 >= 0.25` enters theme aggregation.

Theme contribution:

```text
contribution = policy_score_v2 * relevance_score_v2
theme_score_v2_raw = sum(contribution)
```

`theme_score_v2_raw` is retained only as an undeduplicated comparison field. It is not the default mainline policy ranking score.

## Policy Event Clustering V2

Policy event clustering is deterministic and does not delete raw policy signals. It only prevents repeated policy signals from being counted multiple times in theme contribution.

`scripts/policy_event_clustering.py` clusters policies through:

- direct matches: same `policy_id`, `source_url`/`url`, or `official_url`
- standard match: same normalized official source, publish dates within 7 days, and title similarity >= 0.65 or keyword overlap >= 0.45
- weak-source match: dates within 7 days, title similarity >= 0.75, and keyword overlap >= 0.55
- missing-date match: same normalized source, title similarity >= 0.80, and keyword overlap >= 0.60

The cluster policy strength is:

```text
cluster_policy_score_v2 = max(member.policy_score_v2)
```

For each theme:

```text
cluster_relevance_score_v2 =
  max(relevance_score_v2 of member policies for this theme)

pre_stance_cluster_contribution =
  cluster_policy_score_v2 * cluster_relevance_score_v2

theme_score_v3_dedup =
  sum(pre_stance_cluster_contribution for matched event clusters)

deduplication_effect =
  max(theme_score_v2_raw - theme_score_v3_dedup, 0.0)
```

`theme_score_v3_dedup` is the deduplicated policy-theme comparison score before direction adjustment. It is not the default score for new policy-theme ranking after `policy_theme_stance_v2`.

The report writes:

- `event_cluster_summary.scoring_version = policy_event_clustering_v2`
- `theme_summary.scoring_version = mainline_score_v6_lifecycle_adjusted`
- `theme_summary.base_relevance_version = theme_relevance_v2`
- `theme_summary.event_clustering_version = policy_event_clustering_v2`
- `theme_summary.policy_stance_version = policy_theme_stance_v2`
- `theme_summary.event_theme_allocation_version = event_theme_allocation_v2`
- `theme_summary.mainline_lifecycle_version = mainline_lifecycle_v2`
- `canonical_mainline_summary.scoring_version = canonical_mainline_output_v2`
- `canonical_mainline_summary.default_score_field = mainline_score_v6`
- `data_quality_summary.scoring_version = live_report_data_guard_v2`
- `policy_provenance_summary.scoring_version = policy_source_provenance_v2`
- `policy_snapshot_summary.scoring_version = policy_snapshot_integrity_v2`
- `contract_validation_summary.scoring_version = mainline_contract_validator_v2`

`mainline_contract_validator_v2` is deterministic. It reads `config/mainline_contract_rules.json` and validates required sections, version fields, policy provenance counts, rejected-policy leakage, policy snapshot integrity, canonical ordering, score monotonicity, score formulas, event allocation budgets, lifecycle state counts, summary counts, and legacy default-score leakage. New report generation attaches `contract_validation_summary`; any error blocks JSON and Markdown writes, while warnings are preserved in the report and Markdown.

`live_report_data_guard_v2` is deterministic. It reads `config/data_quality_rules.json` and only guards the live report generation pipeline against empty optional tables, missing columns, optional-source exceptions, or required-stage failures. It does not add scoring factors, change `mainline_score_v6`, change `mainline_ranking`, or use market data availability as a theme score input. Required stages such as policy store, policy provenance, policy snapshot integrity, policy theme summary, canonical mainline, and contract validation still block writes if they fail.

## Policy Theme Stance V2

Policy direction is deterministic and does not use LLM scoring, embeddings, market prices, funds flow or external sentiment libraries. It only reads the policy text fields already stored for research:

- `title`
- `summary`
- `policy_text`
- `key_points`
- `beneficiary_chain`
- `related_industries`

Configuration lives in `config/policy_stance_rules.json`. Theme-specific optional fields live in `config/themes.json`:

- `stance_profile`, default `growth_support`
- `theme_specific_supportive_keywords`, default `[]`
- `theme_specific_restrictive_keywords`, default `[]`

Stance is calculated only inside sentences that contain the theme context. Theme context keywords come from:

- `core_keywords`
- `industry_keywords`
- `beneficiary_keywords`
- `policy_objectives`
- `theme_specific_supportive_keywords`
- `theme_specific_restrictive_keywords`

Scoring:

```text
support_score =
  supportive_action_keywords * 0.20 +
  implementation_support_keywords * 0.15 +
  positive_phrase_overrides * 0.25 +
  theme_specific_supportive_keywords * 0.20

constraint_score =
  restrictive_action_keywords * 0.25 +
  risk_constraint_keywords * 0.15 +
  negative_phrase_overrides * 0.30 +
  theme_specific_restrictive_keywords * 0.25

stance_score_v2 = support_score - constraint_score
```

Each unique keyword is counted at most once per policy-theme pair. Scores are capped at 1.0 and rounded to four decimals.

Direction mapping:

```text
stance_score_v2 >=  0.45 -> supportive          -> direction_multiplier 1.00
stance_score_v2 >=  0.15 -> mildly_supportive   -> direction_multiplier 0.75
stance_score_v2 >  -0.15 -> neutral_or_mixed     -> direction_multiplier 0.50
stance_score_v2 >  -0.45 -> mildly_restrictive  -> direction_multiplier 0.25
otherwise                -> restrictive         -> direction_multiplier 0.00
```

Cluster stance aggregation preserves restrictive information inside duplicate-policy clusters:

```text
cluster_support_score = max(member.support_score)
cluster_constraint_score = max(member.constraint_score)
cluster_stance_score_v2 = cluster_support_score - cluster_constraint_score
```

Policy-theme contribution is now:

```text
pre_stance_cluster_contribution =
  cluster_policy_score_v2 * cluster_relevance_score_v2

stance_adjusted_cluster_contribution =
  pre_stance_cluster_contribution * direction_multiplier

theme_score_v3_dedup =
  sum(pre_stance_cluster_contribution for matched event clusters)

theme_score_v4_stance_adjusted =
  sum(stance_adjusted_cluster_contribution for matched event clusters)

stance_adjustment_effect =
  max(theme_score_v3_dedup - theme_score_v4_stance_adjusted, 0.0)
```

`theme_score_v4_stance_adjusted` is the direction-adjusted comparison score before event-theme allocation. `theme_score_v3_dedup` remains the deduplicated before-stance comparison field. `theme_score_v2_raw` remains the undeduplicated comparison field.

Policy direction is only a mainline research input. It does not generate trading, position, account, order, backtest or execution advice.

## Event Theme Allocation V2

Event-theme allocation is deterministic and does not use LLM scoring, embeddings, manual allocation scores, market prices, funds flow, trading data or external sentiment libraries. It only consumes the event contributors already produced by `theme_score_v4_stance_adjusted`.

Configuration lives in `config/theme_allocation_rules.json`:

- `version = event_theme_allocation_v2`
- `allocation_method = proportional_budget_cap`
- `event_budget_cap_ratio = 1.0`
- `min_allocated_contribution_threshold = 0.0001`
- `allocation_role_thresholds.co_primary_min_share = 0.30`
- `allocation_role_thresholds.secondary_min_share = 0.15`

For each event-theme pair:

```text
raw_stance_adjusted_cluster_contribution =
  cluster_policy_score_v2 *
  cluster_relevance_score_v2 *
  direction_multiplier
```

For each event cluster:

```text
event_contribution_budget =
  cluster_policy_score_v2 * event_budget_cap_ratio

raw_contribution_sum_v4 =
  sum(raw_stance_adjusted_cluster_contribution for same event)
```

If `raw_contribution_sum_v4 <= event_contribution_budget`, allocation does not reduce the event. If `raw_contribution_sum_v4 > event_contribution_budget`, the event is capped and each theme receives a proportional share:

```text
allocation_share =
  raw_stance_adjusted_cluster_contribution / raw_contribution_sum_v4

allocated_cluster_contribution =
  event_contribution_budget * allocation_share
```

Zero raw contribution is handled without division by zero:

```text
allocation_share = 0.0
allocated_cluster_contribution = 0.0
allocation_capped = False
```

Allocation roles are assigned within each event after sorting by allocated contribution, allocation share, relevance and theme id:

```text
rank 1                    -> primary
allocation_share >= 0.30  -> co_primary
allocation_share >= 0.15  -> secondary
otherwise                 -> peripheral
```

Theme score after allocation:

```text
theme_score_v5 =
  sum(allocated_cluster_contribution)

theme_score_v4_stance_adjusted =
  sum(raw_stance_adjusted_cluster_contribution)

allocation_adjustment_effect =
  max(theme_score_v4_stance_adjusted - theme_score_v5, 0.0)
```

`theme_score_v5` is the event-theme allocated comparison score before lifecycle adjustment. `theme_score_v4_stance_adjusted`, `theme_score_v3_dedup`, and `theme_score_v2_raw` remain comparison fields.

The allocation layer is only a mainline research input. It does not generate trading, position, account, order, backtest or execution advice.

## Mainline Lifecycle V2

Mainline lifecycle scoring is deterministic and does not use LLM scoring, embeddings, market prices, funds flow, trading data, returns, historical report files or manual lifecycle scores. It only consumes the current report's allocated event-theme contributors and their policy dates.

Configuration lives in `config/mainline_lifecycle_rules.json`. The default state multipliers are:

- `accelerating = 1.00`
- `sustained = 0.95`
- `emerging = 0.85`
- `single_event_emerging = 0.70`
- `cooling = 0.55`
- `legacy_tail = 0.35`
- `undated_unknown = 0.40`
- `dormant = 0.00`

Event activity date is selected from the first available field:

```text
publish_date_max
event_publish_date_max
publish_date
published_date
primary_policy_publish_date
publish_date_min
event_publish_date_min
```

Future dates are clamped to `age_days = 0`; missing dates are counted as `undated`.

Age buckets:

```text
age_days <= 7   -> recent_7d
age_days <= 30  -> recent_30d
age_days <= 60  -> prior_31_60d
age_days <= 90  -> prior_61_90d
otherwise       -> older
missing date    -> undated
```

Lifecycle metrics:

```text
score_30d = score_7d + recent_30d score
score_90d = score_30d + score_31_60d + score_61_90d
active_window_count =
  count(score_30d >= threshold) +
  count(score_31_60d >= threshold) +
  count(score_61_90d >= threshold)
persistence_score = active_window_count / 3
acceleration_delta_30d = score_30d - score_31_60d
acceleration_ratio_30d =
  clamp(acceleration_delta_30d / max(score_31_60d, threshold), -1.0, 5.0)
```

Lifecycle state is classified in this order:

```text
dormant
undated_unknown
legacy_tail
cooling
accelerating
sustained
single_event_emerging
emerging
legacy_tail fallback
```

Breadth score:

```text
event_breadth_score =
  min(event_count_90d / event_count_90d_target, 1.0)
source_breadth_score =
  min(source_org_count_90d / source_org_count_90d_target, 1.0)
breadth_score =
  0.6 * event_breadth_score + 0.4 * source_breadth_score
```

Lifecycle multiplier and final score:

```text
lifecycle_quality_multiplier =
  0.75 * state_multiplier +
  0.25 * breadth_score

mainline_score_v6 =
  theme_score_v5 * lifecycle_quality_multiplier
```

`mainline_score_v6` is the default policy-theme score used by new reports. It is capped so it cannot exceed `theme_score_v5`. `theme_score_v5`, `theme_score_v4_stance_adjusted`, `theme_score_v3_dedup`, and `theme_score_v2_raw` remain comparison fields.

The lifecycle layer is only a mainline research input. It does not generate trading, position, account, order, backtest or execution advice.

## Canonical Mainline Output V2

The default report, API and homepage ranking is `mainline_ranking`, produced by deterministic `canonical_mainline_output_v2`.

Final canonical chain:

```text
policy_source_provenance_v2
-> policy_snapshot_integrity_v2
-> policy_score_v2
-> relevance_score_v2
-> policy_event_clustering_v2
-> policy_theme_stance_v2
-> event_theme_allocation_v2
-> mainline_lifecycle_v2
-> mainline_score_v6
-> mainline_ranking
-> canonical_mainline_summary
```

Default canonical mainline score:

```text
mainline_score_v6 = theme_score_v5 * lifecycle_quality_multiplier
```

Report-level fields:

```text
canonical_mainline_summary.scoring_version = canonical_mainline_output_v2
canonical_mainline_summary.default_score_field = mainline_score_v6
mainline_ranking = canonical default ranking by mainline_score_v6
legacy_theme_ranking = market-context comparison ranking
policy_provenance_summary.scoring_version = policy_source_provenance_v2
policy_snapshot_summary.scoring_version = policy_snapshot_integrity_v2
data_quality_summary.scoring_version = live_report_data_guard_v2
data_quality_summary.status = pass | degraded | fail
contract_validation_summary.scoring_version = mainline_contract_validator_v2
contract_validation_summary.status = pass | fail
```

`legacy_evidence_score`, `evidence_score`, and `market_score` are market-context comparison fields only. They are not the canonical mainline ranking score and must not be used for the default one-line conclusion, homepage top mainline, or API default score.

Report contract validator V2:

```text
required_sections:
  policy_summary
  policy_provenance_summary
  policy_snapshot_summary
  event_cluster_summary
  policy_stance_summary
  event_theme_allocation_summary
  mainline_lifecycle_summary
  theme_summary
  canonical_mainline_summary
  mainline_ranking
  legacy_theme_ranking
  contract_validation_summary

error examples:
  missing required section
  version mismatch
  canonical top mismatch
  mainline_ranking not sorted by mainline_score_v6
  mainline_score_v6 > theme_score_v5
  score formula mismatch
  rejected policy used in mainline scoring
  unexplained policy content change
  duplicate policy_id/source_url conflict
  policy snapshot content_hash mismatch
  event allocation over budget
  lifecycle state count mismatch
  legacy evidence used as default score

warning examples:
  legacy_theme_ranking present
  theme_ranking retained for legacy compatibility
  zero-score inactive theme without top event contributors
  lifecycle state present with empty lifecycle_reasons
```

Live report data guard V2:

```text
required stages:
  policy_store
  policy_provenance
  policy_snapshot_integrity
  policy_theme_summary
  canonical_mainline
  contract_validation

optional stages:
  breadth
  broad_indexes
  sw_score
  ths_score
  etf_score
  limit_up
  moneyflow
  baostock_check

optional failure behavior:
  empty table -> empty schema fallback
  missing column -> default column fill or empty schema fallback
  exception -> fallback value and degraded stage status

required failure behavior:
  raise RuntimeError
  do not write JSON
  do not write Markdown
```

`data_quality_summary` is written into JSON, Markdown, `/api/latest`, `/api/index`, and `/api/health`. A degraded optional market-context stage affects legacy market-context display only; canonical `mainline_score_v6` remains produced from the policy-theme scoring chain.

API score-series contract:

```text
point.score = point.mainline_score_v6
point.default_score = point.mainline_score_v6
point.default_score_field = mainline_score_v6
legacy_evidence_score / legacy_market_score / legacy_policy_score = comparison fields only
```
