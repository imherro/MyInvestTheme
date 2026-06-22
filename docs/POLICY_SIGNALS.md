# Policy Signals

The daily automation keeps policy research separate from market scoring.

## Workflow

1. Codex reviews official policy sources after market close.
2. Codex updates `data/policy_signals.json` only when there is a relevant new or corrected official signal.
3. `scripts/generate_mainline_report.py` reads the policy store and calculates `policy_score` deterministically.
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
- `url`
- `specificity`: 0-1
- `implementation_path`: 0-1
- `confidence`: 0-1
- `evidence`: concise extracted reason
- `themes`: list of `{ theme, relevance, beneficiary_chain }`

Theme names must match the mainline themes in `scripts/generate_mainline_report.py`.

## LLM Extraction Prompt

Use this prompt when Codex updates policy signals:

```text
Read only official Chinese policy/regulator sources from the last 90 days.
Extract A-share mainline policy signals into the JSON schema in docs/POLICY_SIGNALS.md.
Do not score by market performance. Only extract policy authority, specificity, implementation path, affected themes, beneficiary chains and evidence.
Do not invent policies. If source text is broad or slogan-like, lower specificity and implementation_path.
If the policy is already present in data/policy_signals.json, update only when the source, relevance, or extracted evidence materially improves.
```

## Scoring

Python scoring is deterministic:

- authority 30%
- freshness 20%
- specificity 20%
- implementation path 20%
- confidence 10%

Theme relevance scales each signal. A theme's `policy_score` is the weighted average of its top three policy signals.
