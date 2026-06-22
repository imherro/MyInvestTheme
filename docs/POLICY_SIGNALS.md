# Policy Signals

The daily automation keeps policy research separate from market scoring.

## Workflow

1. Codex reviews official policy sources after market close.
2. Codex updates `data/policy_signals.json` only when there is a relevant new or corrected official signal.
3. `scripts/generate_mainline_report.py` reads the policy store and recalculates `policy_score_v2` deterministically for the report basis date.
4. `scripts/theme_relevance.py` maps policy signals to mainline themes through deterministic `theme_relevance_v2` rules from `config/themes.json`.
5. `scripts/policy_event_clustering.py` clusters duplicate policy signals into deterministic policy events.
6. `scripts/policy_stance.py` identifies whether each policy supports, mildly supports, neutrally mixes, mildly restricts, or restricts each matched theme.
7. `scripts/theme_allocation.py` allocates one event's finite contribution budget across matched themes through deterministic `event_theme_allocation_v2`.
8. `scripts/daily_mainline_update.py` commits the policy store together with any new report.

## Official Sources

Prefer official sources only:

- State Council and ministry websites
- CSRC, exchanges, NDRC, MIIT, NEA and other regulators
- Official policy PDFs, press releases, action plans, notices and formal interpretations

Avoid media rewrites, brokerage interpretations and unsourced reposts for scoring.

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
- `theme_summary.scoring_version = theme_score_v5_allocated`
- `theme_summary.base_relevance_version = theme_relevance_v2`
- `theme_summary.event_clustering_version = policy_event_clustering_v2`
- `theme_summary.policy_stance_version = policy_theme_stance_v2`
- `theme_summary.event_theme_allocation_version = event_theme_allocation_v2`

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

`theme_score_v5` is the default policy-theme score used by new reports. `theme_score_v4_stance_adjusted`, `theme_score_v3_dedup`, and `theme_score_v2_raw` remain comparison fields.

The allocation layer is only a mainline research input. It does not generate trading, position, account, order, backtest or execution advice.
