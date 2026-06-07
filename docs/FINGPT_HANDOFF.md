# FinGPT Handoff

This note defines the first-test handoff from FinPortfolio IR to
`Supportive_project_FinGPT_as_feature_engine`.

## Purpose

FinPortfolio IR remains the retrieval system. The FinGPT Feature Engine consumes
only validated, causally safe retrieved contexts.

The handoff package is a directory with:

- `retrieved_contexts.jsonl`: flat JSONL accepted by the FinGPT loader.
- `evidence_bundles.jsonl`: grouped stock/sector/market evidence bundles.
- `handoff_manifest.json`: schema version, source paths, row counts, status.
- `handoff_validation.json`: machine-readable validation report.
- `handoff_report.html`: browser-readable summary for quick inspection.

## Required Context Fields

The first FinGPT-side loader requires:

- `portfolio_id`
- `decision_time`
- `retrieval_cutoff`
- `doc_id`
- `published_at`
- `available_at`
- `title`
- `body_excerpt`
- `matched_tickers`
- `document_hash`

FinPortfolio IR also includes stronger provenance and ranking fields:

- `query_id`
- `method`
- `rank`
- `source_type`
- `first_seen_at`
- `ingested_at`
- `duplicate_cluster_id`
- `matched_holdings`
- `event_tags`
- `risk_terms`
- `evidence_scope`
- `retrieval_reason_tags`
- component scores and `final_score`

## SEC Dow 30 Medium Handoff

The first serious non-sample handoff is the official SEC-only Dow 30 corpus:

- raw corpus:
  `data/raw_documents/sec_dow30_2010_2023_300.jsonl`
- normalized corpus:
  `data/processed_documents/sec_dow30_2010_2023_300_documents.jsonl`
- retrieved contexts:
  `data/exports/sec_dow30_2010_2023/retrieved_contexts.jsonl`
- manifest:
  `data/exports/sec_dow30_2010_2023/manifest.json`
- validation:
  `data/exports/sec_dow30_2010_2023/handoff_validation.json`
- FinGPT smoke summary:
  `data/exports/sec_dow30_2010_2023/fingpt_smoke/smoke_summary.json`

Protocol:

- 300 SEC filings total;
- 30 Dow 30 tickers x 10 documents;
- train/test periods match the PPO setup:
  - train: `2010-01-01` to before `2021-10-01`;
  - test/OOS: `2021-10-01` to `2023-03-01`;
- split: 240 train contexts, 60 test contexts;
- `available_at <= decision_time` is enforced for every row;
- `split` and `document_split` are present in every context;
- SEC registrant ticker is treated as the authoritative holding label;
- all timestamp fields are exported as second-level UTC ISO strings.

Latest smoke result:

- status: passed;
- FinGPT leakage rows: 0;
- doc extraction coverage: 300/300;
- provenance coverage: 300/300;
- holding coverage rate: 1.0.

## SEC Section/Exhibit-Level Handoff

The stronger SEC handoff parses full primary filing HTML into section-level
retrieval units, fetches attached 8-K exhibits such as `EX-99.1`, and then
selects comparable 300 contexts for FinGPT.

Section corpus artifacts:

- raw section corpus:
  `data/raw_documents/sec_dow30_2010_2023_300_sections.jsonl`
- normalized section corpus:
  `data/processed_documents/sec_dow30_2010_2023_300_sections_documents.jsonl`
- section summary:
  `data/processed_documents/sec_dow30_2010_2023_300_sections_summary.json`
- full HTML cache:
  `data/raw_documents/sec_full_html_cache`

Section handoff artifacts:

- retrieved contexts:
  `data/exports/sec_dow30_2010_2023_sections/retrieved_contexts.jsonl`
- manifest:
  `data/exports/sec_dow30_2010_2023_sections/manifest.json`
- validation:
  `data/exports/sec_dow30_2010_2023_sections/handoff_validation.json`
- FinGPT smoke summary:
  `data/exports/sec_dow30_2010_2023_sections/fingpt_smoke/smoke_summary.json`

Current section corpus:

- 300 parent SEC filings;
- 1032 extracted section/exhibit documents;
- 818 primary filing section documents;
- 214 attached exhibit documents;
- 10-K sections: Business, Risk Factors, MD&A, Market Risk, Financial
  Statements;
- 10-Q sections: Financial Statements, MD&A, Market Risk, Controls, Legal
  Proceedings, Risk Factors;
- 8-K sections: numbered current-report Items such as 2.02, 8.01, 9.01;
- 8-K exhibits: textual attached exhibits such as `EX-99.1`, `EX-99.2`,
  `EX-10.1`;
- fetch errors: 0.

Comparable section handoff:

- 300 representative section contexts;
- 300 unique parent filings;
- 240 train / 60 test;
- 30 Dow 30 tickers x 10 contexts;
- 106 representative contexts come from attached exhibits;
- 101 representative contexts come from `EX-99.*`;
- strict leakage rows: 0;
- FinGPT smoke status: passed;
- doc extraction coverage: 300/300;
- provenance coverage: 300/300;
- holding coverage rate: 1.0.

Known limitation: exhibit labels are currently inferred from filenames. The
next refinement is parsing SEC exhibit descriptions from filing detail pages or
complete-submission headers.

## Daily PPO-Aligned Handoff

The next handoff mode is daily rather than representative. It builds
document-date-ticker contexts for the PPO base panel:

```text
for each decision_date:
    retrieve portfolio-level official macro/market evidence
    retrieve ticker-level company evidence for active holdings
    export causal contexts with provenance, age, decay weights, and intent
```

Builder:

- `features/build_daily_retrieval_contexts.py`

Inputs:

- `--base-panel`: PPO/base macro panel with `date` and `tic` columns.
- `--documents`: comma-separated processed JSONL corpora.
- `--decision-time-policy`: `market_open`, `pre_open`, `market_close`, or an
  explicit local time.
- `--portfolio-top-k`, `--ticker-top-k`, `--lookback-days`,
  `--max-contexts-total`.

Daily fields added for FinGPT/PPO:

- `retrieval_layer`;
- `target_ticker`;
- `tic`;
- `daily_context_id`;
- `age_days`;
- `age_bucket`;
- `decay_weight_7d`;
- `decay_weight_30d`;
- `decay_weight_90d`;
- `query_intent_primary`;
- `component_scores`;
- `bm25_score`;
- `risk_term_score`;
- `macro_regime_relevance_score`;
- `event_severity_score`;
- `freshness_score`.

Current smoke artifacts:

- `data/exports/daily_retrieval_sample/retrieved_contexts.jsonl`
- `data/exports/daily_retrieval_sample/manifest_daily.json`
- `data/exports/daily_retrieval_sample/handoff_validation.json`
- `data/exports/daily_retrieval_sample/fingpt_smoke/smoke_summary.json`

Current smoke result:

- contexts: 13;
- portfolio contexts: 5;
- stock contexts: 8;
- official macro contexts: 5;
- SEC contexts: 8;
- strict leakage rows: 0;
- FinGPT smoke status: passed.

## Full PPO Daily Package

The first full daily package has now been built against the real PPO base panel
`../processed_final_fixed_external_lagclean_full.csv`.

Current package note:

- `docs/CURRENT_ARTIFACTS_AND_EXPERIMENTS.md`

Source package:

- SEC filings:
  `data/raw_documents/sec_dow30_ppo_2010_2023_1800_with_dis_legacy.jsonl`
- SEC section/exhibit documents:
  `data/processed_documents/sec_dow30_ppo_2010_2023_1800_with_dis_legacy_sections_documents.jsonl`
- official macro documents:
  `data/processed_documents/official_macro_2010_2023_documents.jsonl`

Daily retrieval artifacts:

- contexts:
  `data/exports/daily_retrieval_ppo_full_dis_legacy/retrieved_contexts.jsonl`
- manifest:
  `data/exports/daily_retrieval_ppo_full_dis_legacy/manifest_daily.json`
- validation:
  `data/exports/daily_retrieval_ppo_full_dis_legacy/handoff_validation.json`

Current full daily retrieval result:

- contexts: 28,493;
- unique documents: 2,141;
- portfolio contexts: 16,550;
- stock contexts: 11,943;
- official macro contexts: 16,550;
- SEC section contexts: 8,733;
- SEC exhibit contexts: 3,210;
- unique decision dates: 3,310;
- unique stock tickers: 29;
- split: 25,447 train / 3,046 test;
- strict leakage rows: 0;
- validation hard issues: 0.
- DIS stock evidence is fixed by the legacy CIK pass: 413 rows, matching the
  normal 409-413 range for the other tickers.

Codex-rule feature artifacts for immediate `base_macro + text` ablation:

- document features:
  `data/exports/daily_retrieval_ppo_full_dis_legacy/codex_rule_text_features/doc_text_features_codex_rule.csv`
- daily stock features:
  `data/exports/daily_retrieval_ppo_full_dis_legacy/codex_rule_text_features/daily_stock_text_features_codex_rule.csv`
- daily portfolio features:
  `data/exports/daily_retrieval_ppo_full_dis_legacy/codex_rule_text_features/daily_portfolio_text_features_codex_rule.csv`
- Mistral comparison seed:
  `data/exports/daily_retrieval_ppo_full_dis_legacy/codex_rule_text_features/codex_teacher_seed.jsonl`
- merge-ready PPO panel:
  `data/exports/daily_retrieval_ppo_full_dis_legacy/rl_panel_codex_rule_text_features.csv`

The feature baseline is deterministic and local. It is not a replacement for
FinGPT/Mistral extraction; it is the teacher baseline to compare against and
improve the later API/LLM prompt.

## Build Package

```powershell
& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" features\build_fingpt_handoff_package.py `
  --retrieval data\exports\retrieved_docs_sample.jsonl `
  --output-dir data\exports\fingpt_handoff_sample
```

## Validate Package

```powershell
& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" features\validate_fingpt_handoff.py `
  --contexts data\exports\fingpt_handoff_sample\retrieved_contexts.jsonl `
  --bundles data\exports\fingpt_handoff_sample\evidence_bundles.jsonl `
  --output-report data\exports\fingpt_handoff_sample\handoff_validation.json
```

The hard constraints are:

```text
available_at <= decision_time
available_at <= retrieval_cutoff
retrieval_cutoff <= decision_time
available_at >= published_at
```

## FinGPT Smoke Test

The preferred second-step command is the IR-side smoke runner. It calls the
current FinGPT Feature Engine CLI, writes all prompt/feature/provenance outputs,
and builds one JSON/HTML summary.

```powershell
& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" features\run_fingpt_handoff_smoke.py `
  --handoff-dir data\exports\fingpt_handoff_sample `
  --fingpt-project ..\Supportive_project_FinGPT_as_feature_engine
```

This creates:

- `fingpt_smoke/leakage_report.json`
- `fingpt_smoke/doc_prompts.jsonl`
- `fingpt_smoke/stock_prompts.jsonl`
- `fingpt_smoke/portfolio_prompts.jsonl`
- `fingpt_smoke/doc_extractions.csv`
- `fingpt_smoke/daily_stock_text_features.csv`
- `fingpt_smoke/daily_portfolio_text_features.csv`
- `fingpt_smoke/legacy_stock_features.csv`
- `fingpt_smoke/legacy_portfolio_features.csv`
- `fingpt_smoke/feature_provenance.csv`
- `fingpt_smoke/smoke_summary.json`
- `fingpt_smoke/smoke_report.html`

The runner fails if leakage is detected, if doc-level extraction does not cover
all context docs, if provenance misses context docs, or if IR metadata is not
preserved in doc prompts.

The lower-level FinGPT commands are:

```powershell
& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" scripts\validate_leakage.py `
  --retrieved-contexts ..\FinPortfolio_IR\data\exports\fingpt_handoff_sample\retrieved_contexts.jsonl `
  --output-report ..\FinPortfolio_IR\data\exports\fingpt_handoff_sample\fingpt_leakage_report.json

& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" scripts\build_fingpt_inputs.py `
  --retrieved-contexts ..\FinPortfolio_IR\data\exports\fingpt_handoff_sample\retrieved_contexts.jsonl `
  --doc-output ..\FinPortfolio_IR\data\exports\fingpt_handoff_sample\fingpt_smoke\doc_prompts.jsonl `
  --stock-output ..\FinPortfolio_IR\data\exports\fingpt_handoff_sample\fingpt_stock_prompts.jsonl `
  --portfolio-output ..\FinPortfolio_IR\data\exports\fingpt_handoff_sample\fingpt_portfolio_prompts.jsonl

& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" scripts\run_fingpt_feature_extraction.py `
  --retrieved-contexts ..\FinPortfolio_IR\data\exports\fingpt_handoff_sample\retrieved_contexts.jsonl `
  --doc-output ..\FinPortfolio_IR\data\exports\fingpt_handoff_sample\fingpt_smoke\doc_extractions.csv `
  --daily-stock-output ..\FinPortfolio_IR\data\exports\fingpt_handoff_sample\fingpt_smoke\daily_stock_text_features.csv `
  --daily-portfolio-output ..\FinPortfolio_IR\data\exports\fingpt_handoff_sample\fingpt_smoke\daily_portfolio_text_features.csv `
  --stock-output ..\FinPortfolio_IR\data\exports\fingpt_handoff_sample\fingpt_stock_features.csv `
  --portfolio-output ..\FinPortfolio_IR\data\exports\fingpt_handoff_sample\fingpt_portfolio_features.csv `
  --provenance-output ..\FinPortfolio_IR\data\exports\fingpt_handoff_sample\fingpt_feature_provenance.csv
```

The current FinGPT extractor is a deterministic rule-based smoke-test backend.
It verifies table contracts and provenance before any real model integration.
