# Policy Signals

The daily automation keeps policy research separate from market scoring.

## Workflow

1. Codex reviews official policy sources after market close.
2. Codex updates `data/policy_signals.json` only when there is a relevant new or corrected official signal.
3. `scripts/generate_mainline_report.py` reads the policy store and recalculates `policy_score_v2` deterministically for the report basis date.
4. `scripts/theme_relevance.py` maps policy signals to mainline themes through deterministic `theme_relevance_v2` rules from `config/themes.json`.
5. `scripts/daily_mainline_update.py` commits the policy store together with any new report.

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
theme_score_v2 = sum(contribution)
```

The report writes `theme_summary.scoring_version = theme_relevance_v2` and includes matched evidence for each top policy contributor.
