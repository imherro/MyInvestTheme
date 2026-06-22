# Policy Signals

The daily automation keeps policy research separate from market scoring.

## Workflow

1. Codex reviews official policy sources after market close.
2. Codex updates `data/policy_signals.json` only when there is a relevant new or corrected official signal.
3. `scripts/generate_mainline_report.py` reads the policy store and recalculates `policy_score_v2` deterministically for the report basis date.
4. `scripts/daily_mainline_update.py` commits the policy store together with any new report.

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
- `evidence`: concise extracted reason
- `themes`: list of `{ theme, relevance, beneficiary_chain }`

Theme names must match the mainline themes in `scripts/generate_mainline_report.py`.

Deprecated fields are not allowed in the policy store and are ignored by scoring logic if present in older historical artifacts:

- `specificity`
- `implementation_path`
- `confidence`

## LLM Extraction Prompt

Use this prompt when Codex updates policy signals:

```text
Read only official Chinese policy/regulator sources from the last 90 days.
Extract A-share mainline policy signals into the JSON schema in docs/POLICY_SIGNALS.md.
Do not score policy quality with an LLM. Only extract official source facts, authority_level, economic_scope, affected themes, beneficiary chains and evidence.
Do not invent policies. If source text is broad or slogan-like, keep evidence concise and let Python scoring rules handle the score.
If the policy is already present in data/policy_signals.json, update only when the source, relevance, or extracted evidence materially improves.
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

Theme relevance scales each signal. A theme's `policy_score` is the weighted average of its top three policy signals.
