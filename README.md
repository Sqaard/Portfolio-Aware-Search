# FinPortfolio IR

Portfolio-driven financial news retrieval for the downstream FinGPT Feature
Engine and later FinRL/PPO feature ablations.

This project is the upstream retrieval layer. It does not trade, does not run
PPO, and does not call FinGPT. Its job is to retrieve a small, causally valid,
portfolio-relevant evidence set. The revised product direction is a
truth-seeking Financial IR engine for US equities: sources must be auditable,
timestamps must be point-in-time safe, and the downstream FinGPT handoff must
preserve provenance instead of only passing text snippets.

The main project methodology is maintained in
[docs/MAIN_METHODOLOGY.md](docs/MAIN_METHODOLOGY.md). Treat that file as the
source of truth for project direction, invariants, evaluation logic, and the
current end-to-end roadmap.

```text
portfolio holdings + decision time
        -> portfolio-aware causal retrieval
        -> top-k evidence JSONL
        -> FinGPT Feature Engine
        -> future structured text features for RL ablation
```

## Current Scope

Implemented v1 scaffold:

- local JSONL document normalization;
- source registry metadata for official, company, macro, and user-favorite
  sources;
- rule-based ticker, alias, company, sector, and macro entity linking;
- timezone-aware causal filtering with `available_at <= decision_time`;
- pure-Python BM25 sparse retrieval;
- portfolio-aware hybrid scoring with component scores;
- duplicate-aware diversified top-k for the `full_hybrid_diversified` method;
- FinGPT-ready context export;
- grouped evidence-bundle export for FinGPT handoff;
- validated FinGPT handoff package with manifest and browser-readable report;
- official SEC-only Dow 30 medium corpus for PPO-aligned train/test handoff:
  300 filings, 30 tickers x 10 documents, 240 train / 60 test;
- full SEC filing section/exhibit parser and section-level FinGPT handoff:
  1032 extracted section/exhibit records, including 214 attached exhibits, and
  300 representative section/exhibit contexts;
- scaled PPO SEC backbone:
  1,740 filings for the 29-ticker PPO universe and 6,824 SEC section/exhibit
  retrieval units, including 665 attached exhibits;
- official US macro release corpus:
  18,240 documents across rates, credit, volatility, energy, labor, inflation,
  growth, and housing;
- PPO-aligned daily retrieval builder for portfolio-level macro evidence and
  stock-level company evidence;
- full PPO daily retrieval package:
  28,493 causal contexts, 2,141 unique documents, 29 stock tickers, train/test
  coverage, strict leakage 0, and DIS legacy SEC coverage fixed;
- deterministic Codex-rule text feature baseline:
  document features, daily stock/portfolio features, 300-row teacher seed for
  Mistral comparison, and a merge-ready PPO panel;
- diagnostic retrieval feature aggregation;
- Precision@K, NDCG@K, MAP, and MRR evaluation;
- configured ranking ablations: `bm25_only`, `bm25_entity`,
  `bm25_entity_portfolio`, `full_hybrid`, `full_hybrid_diversified`;
- sample corpus, sample portfolios, batch query set, sample qrels, and tests.
- deterministic US macro dashboard, portfolio summary, favorite ranking, and
  My Vibe backend primitives for the future English UI.
- ConFIRM-inspired deterministic query-intent routing for SEC/company IR/macro,
  structured facts, news/sentiment, favorite websites, external sources, and
  portfolio-impact queries.

Dense retrieval and FinGPT inference are intentionally deferred until judged
qrels and extraction-quality checks justify them.

## Big Data Infrastructure (course project)

An additive Big Data layer lives under [`bigdata/`](bigdata) and re-expresses the
heavy, corpus-wide processing (tokenization, inverted-index / BM25 statistics,
corpus analytics) as **MapReduce** on **Apache Spark**, with a portable
pure-Python multiprocessing engine behind the same interface for portability and
verification. No existing code was modified.

```powershell
# optional: install Spark (else the pure-Python local engine runs)
& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" -m pip install -r requirements-bigdata.txt

# full pipeline over a corpus -> analytics.json, bm25_stats.json, REPORT.md
& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" -m bigdata.run_all --corpus macro --query "inflation interest rates"

# choose the engine explicitly (default auto -> Spark if installed, else local)
& "...python.exe" -m bigdata.run_inverted_index  --corpus macro --engine spark --master "local[*]"
& "...python.exe" -m bigdata.run_corpus_analytics --corpus sec300 --engine local
```

The distributed output is verified **byte-identical** to the single-machine
`indexing/build_sparse_index.py` (`BM25Index`) on the shared corpus, plus a
Structured-Streaming / incremental auto-updater and a Docker Spark cluster. See
[docs/BIG_DATA_INFRASTRUCTURE.md](docs/BIG_DATA_INFRASTRUCTURE.md) and
[bigdata/README.md](bigdata/README.md).

## Methodology And Docs

The docs folder is deliberately compact:

- [docs/MAIN_METHODOLOGY.md](docs/MAIN_METHODOLOGY.md): project doctrine,
  invariants, evaluation hierarchy, and end-to-end roadmap.
- [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md): active sprint
  tasks and acceptance criteria.
- [docs/CURRENT_ARTIFACTS_AND_EXPERIMENTS.md](docs/CURRENT_ARTIFACTS_AND_EXPERIMENTS.md):
  current retrieval packages, trusted data, Mistral/Codex results, and
  event-study findings.
- [docs/THEORY_AND_BENCHMARKS.md](docs/THEORY_AND_BENCHMARKS.md): theoretical
  foundation, benchmark positioning, and adopted research ideas.
- [docs/DATA_SCHEMA.md](docs/DATA_SCHEMA.md),
  [docs/FINGPT_HANDOFF.md](docs/FINGPT_HANDOFF.md), and
  [docs/ANNOTATION_GUIDE.md](docs/ANNOTATION_GUIDE.md): contracts for data,
  downstream handoff, and human labels.

The default causal protocol is documented in
[configs/decision_protocol.yaml](configs/decision_protocol.yaml).

## Setup

Use CPython 3.9+ and install the small dependency set:

```powershell
cd "C:\Users\ivanp\OneDrive\Рабочий стол\доки+черчи\ITMO\2_sem\FinRL_Tsinghua\FinPortfolio_IR"
& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" -m pip install -r requirements.txt
```

In this workspace, the bare `python` command may point to PyPy. Prefer the
CPython executable above for reproducible runs.

## Data Format

Normalized documents are stored as JSONL records with:

- `doc_id`
- `title`
- `body`
- `source`
- `source_type`
- `url`
- `source_registry_id`
- `canonical_url`
- `source_reliability_tier`
- `robots_policy`
- `last_url_check_at`
- `fetch_status`
- `content_license_note`
- `published_at`
- `first_seen_at`
- `available_at`
- `ingested_at`
- `version_id`
- `duplicate_cluster_id`
- `tickers_detected`
- `matched_tickers`
- `matched_holdings`
- `company_names_detected`
- `sectors_detected`
- `event_tags`
- `risk_terms`
- `source_credibility`
- `event_type`
- `language`
- `document_hash`

`available_at` is the timestamp used for leakage checks. If a document lacks a
safe timestamp, it is excluded by normalization/loading.

## End-To-End Sample

Normalize local raw documents:

```powershell
& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" crawler\normalize_documents.py `
  --input data\raw_documents\sample_documents.jsonl `
  --metadata data\processed_documents\ticker_metadata.csv `
  --output data\processed_documents\documents.jsonl
```

Retrieve top-k causal documents for the sample portfolio:

```powershell
& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" retrieval\retrieve_for_portfolio.py `
  --documents data\processed_documents\documents.jsonl `
  --portfolio configs\sample_portfolio.yaml `
  --metadata data\processed_documents\ticker_metadata.csv `
  --decision-datetime 2022-03-15T09:30:00-05:00 `
  --top-k 10 `
  --output data\exports\retrieved_docs_sample.jsonl `
  --run-csv data\exports\sample_run.csv
```

Export FinGPT-ready contexts:

```powershell
& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" features\export_fingpt_contexts.py `
  --input data\exports\retrieved_docs_sample.jsonl `
  --output data\exports\fingpt_contexts_sample.jsonl
```

Export grouped evidence bundles:

```powershell
& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" features\export_evidence_bundles.py `
  --input data\exports\retrieved_docs_sample.jsonl `
  --output data\exports\evidence_bundles_sample.jsonl
```

Build the first-test FinGPT handoff package:

```powershell
& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" features\build_fingpt_handoff_package.py `
  --retrieval data\exports\retrieved_docs_sample.jsonl `
  --output-dir data\exports\fingpt_handoff_sample
```

Run the cross-project FinGPT smoke test:

```powershell
& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" features\run_fingpt_handoff_smoke.py `
  --handoff-dir data\exports\fingpt_handoff_sample `
  --fingpt-project ..\Supportive_project_FinGPT_as_feature_engine
```

Build the SEC Dow 30 300-document PPO-aligned handoff:

```powershell
& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" features\build_sec_300_corpus.py `
  --inputs data\raw_documents\sec_dow30_filings_2010_2023.jsonl,data\raw_documents\sec_dow30_missing7_2010_2023.jsonl `
  --output-raw data\raw_documents\sec_dow30_2010_2023_300.jsonl `
  --output-processed data\processed_documents\sec_dow30_2010_2023_300_documents.jsonl `
  --metadata data\processed_documents\dow30_ticker_metadata.csv `
  --source-registry data\source_registry\source_registry.csv `
  --summary-output data\processed_documents\sec_dow30_2010_2023_300_summary.json `
  --target-docs 300

& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" features\build_sec_dow30_300_contexts.py `
  --documents data\processed_documents\sec_dow30_2010_2023_300_documents.jsonl `
  --metadata data\processed_documents\dow30_ticker_metadata.csv `
  --config configs\default.yaml `
  --portfolios-dir data\portfolios\sec_dow30_single `
  --output data\exports\sec_dow30_2010_2023\retrieved_contexts.jsonl `
  --manifest-output data\exports\sec_dow30_2010_2023\manifest.json `
  --output-count 300 `
  --rank-search-k 300

& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" features\validate_fingpt_handoff.py `
  --contexts data\exports\sec_dow30_2010_2023\retrieved_contexts.jsonl `
  --output data\exports\sec_dow30_2010_2023\handoff_validation.json

& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" features\run_fingpt_handoff_smoke.py `
  --handoff-dir data\exports\sec_dow30_2010_2023 `
  --fingpt-project ..\Supportive_project_FinGPT_as_feature_engine
```

Build the full SEC section/exhibit-level handoff:

```powershell
& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" features\build_sec_full_section_corpus.py `
  --input-raw data\raw_documents\sec_dow30_2010_2023_300.jsonl `
  --output-raw data\raw_documents\sec_dow30_2010_2023_300_sections.jsonl `
  --output-processed data\processed_documents\sec_dow30_2010_2023_300_sections_documents.jsonl `
  --summary-output data\processed_documents\sec_dow30_2010_2023_300_sections_summary.json `
  --metadata data\processed_documents\dow30_ticker_metadata.csv `
  --source-registry data\source_registry\source_registry.csv `
  --cache-dir data\raw_documents\sec_full_html_cache `
  --max-download-bytes 50000000 `
  --max-section-chars 250000 `
  --max-exhibits-per-filing 6 `
  --exhibit-forms 8-K

& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" features\build_sec_section_contexts.py `
  --documents data\processed_documents\sec_dow30_2010_2023_300_sections_documents.jsonl `
  --metadata data\processed_documents\dow30_ticker_metadata.csv `
  --config configs\default.yaml `
  --output data\exports\sec_dow30_2010_2023_sections\retrieved_contexts.jsonl `
  --manifest-output data\exports\sec_dow30_2010_2023_sections\manifest.json `
  --output-count 300 `
  --rank-search-k 1000

& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" features\validate_fingpt_handoff.py `
  --contexts data\exports\sec_dow30_2010_2023_sections\retrieved_contexts.jsonl `
  --output data\exports\sec_dow30_2010_2023_sections\handoff_validation.json

& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" features\run_fingpt_handoff_smoke.py `
  --handoff-dir data\exports\sec_dow30_2010_2023_sections `
  --fingpt-project ..\Supportive_project_FinGPT_as_feature_engine
```

Build official macro release documents:

```powershell
& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" features\build_official_macro_documents.py `
  --output-raw data\raw_documents\official_macro_2010_2023.jsonl `
  --output-processed data\processed_documents\official_macro_2010_2023_documents.jsonl `
  --summary-output data\processed_documents\official_macro_2010_2023_summary.json `
  --metadata data\processed_documents\dow30_ticker_metadata.csv `
  --source-registry data\source_registry\source_registry.csv `
  --start-date 2010-01-01 `
  --end-date 2023-03-01
```

Build PPO-aligned daily retrieval contexts:

```powershell
& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" features\build_daily_retrieval_contexts.py `
  --base-panel ..\processed_final_fixed_external_lagclean_full.csv `
  --documents data\processed_documents\sec_dow30_ppo_2010_2023_1800_with_dis_legacy_sections_documents.jsonl,data\processed_documents\official_macro_2010_2023_documents.jsonl `
  --metadata data\processed_documents\dow30_ticker_metadata.csv `
  --config configs\default.yaml `
  --decision-time-policy market_open `
  --portfolio-top-k 5 `
  --ticker-top-k 1 `
  --ticker-date-stride 8 `
  --lookback-days 365 `
  --max-contexts-total 30000 `
  --output data\exports\daily_retrieval_ppo_full_dis_legacy\retrieved_contexts.jsonl `
  --manifest-output data\exports\daily_retrieval_ppo_full_dis_legacy\manifest_daily.json
```

Build the deterministic Codex-rule text features and merge-ready PPO panel:

```powershell
& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" features\build_text_feature_baseline.py `
  --contexts data\exports\daily_retrieval_ppo_full_dis_legacy\retrieved_contexts.jsonl `
  --output-dir data\exports\daily_retrieval_ppo_full_dis_legacy\codex_rule_text_features `
  --teacher-size 300

& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" features\merge_text_features_with_base_panel.py `
  --base-panel ..\processed_final_fixed_external_lagclean_full.csv `
  --stock-features data\exports\daily_retrieval_ppo_full_dis_legacy\codex_rule_text_features\daily_stock_text_features_codex_rule.csv `
  --portfolio-features data\exports\daily_retrieval_ppo_full_dis_legacy\codex_rule_text_features\daily_portfolio_text_features_codex_rule.csv `
  --output data\exports\daily_retrieval_ppo_full_dis_legacy\rl_panel_codex_rule_text_features.csv `
  --manifest-output data\exports\daily_retrieval_ppo_full_dis_legacy\rl_panel_codex_rule_text_features_manifest.json
```

The smaller smoke still uses the FinGPT sample panel and writes:

- `data/exports/daily_retrieval_sample/retrieved_contexts.jsonl`
- `data/exports/daily_retrieval_sample/manifest_daily.json`
- `data/exports/daily_retrieval_sample/fingpt_smoke/smoke_summary.json`

Run the local browser-testable dashboard:

```powershell
& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" web_app.py --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765`. The UI is English-only, uses the local sample
corpus, and stores portfolio/favorite settings in
`data/user_settings/settings.json`. LLM API keys entered in the UI are not
persisted. My Vibe analysis uses a real remote LLM call when a session key or
server-side key is available; otherwise it falls back to deterministic local
analysis. The default endpoint is DeepSeek Chat Completions
`https://api.deepseek.com/chat/completions`, while Mistral and OpenAI
Responses-compatible endpoints are also supported through the API endpoint field.

Optional server-side LLM configuration can be kept in `.env`:

```text
LLM_API_KEY=your_key
LLM_BASE_URL=https://api.deepseek.com/chat/completions
LLM_MODEL=deepseek-chat
```

Full post text is never rendered in the browser. It is sent to an LLM provider
only after the user explicitly clicks a post for analysis.

Evaluate the sample ranking:

```powershell
& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" evaluation\evaluate_ir_metrics.py `
  --qrels data\annotations\sample_qrels.csv `
  --run data\exports\sample_run.csv `
  --output data\exports\sample_metrics.csv
```

Run all configured ranking ablations:

```powershell
& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" evaluation\run_ablation_suite.py `
  --documents data\processed_documents\documents.jsonl `
  --portfolio configs\sample_portfolio.yaml `
  --metadata data\processed_documents\ticker_metadata.csv `
  --decision-datetime 2022-03-15T09:30:00-05:00 `
  --top-k 10 `
  --qrels data\annotations\sample_qrels.csv `
  --output-dir data\exports\ablation_sample
```

Run the multi-query sample evaluation set:

```powershell
& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" evaluation\run_ablation_suite.py `
  --documents data\processed_documents\documents.jsonl `
  --queries data\portfolios\sample_query_set.csv `
  --metadata data\processed_documents\ticker_metadata.csv `
  --top-k 10 `
  --qrels data\annotations\sample_qrels.csv `
  --output-dir data\exports\ablation_batch_sample
```

This writes per-query metrics to `ablation_metrics.csv` and method averages to
`ablation_metrics_by_method.csv`.

Build the local HTML report:

```powershell
& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" evaluation\build_html_report.py `
  --metrics data\exports\ablation_batch_sample\ablation_metrics_by_method.csv `
  --diagnostics data\exports\ablation_batch_sample\ablation_diagnostics_by_method.csv `
  --output data\exports\ablation_batch_sample\retrieval_report.html `
  --title "FinPortfolio IR Batch Retrieval Report"
```

Build an annotation pool for later human review:

```powershell
& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" evaluation\build_annotation_pool.py `
  --input data\exports\ablation_batch_sample\ablation_retrieved_all.jsonl `
  --qrels data\annotations\bootstrap_sample_qrels.csv `
  --output data\annotations\annotation_pool_batch_sample.csv
```

The current sample labels are bootstrap labels. Human-reviewed labels should be
kept in a separate qrels file and validated before using them for final IR
comparisons.

Export reviewed pool labels back to qrels:

```powershell
& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" evaluation\export_qrels_from_pool.py `
  --input data\annotations\annotation_pool_batch_sample.csv `
  --output data\annotations\human_qrels_v1.csv `
  --issues-output data\annotations\human_qrels_v1_export_issues.csv `
  --label-source human_v1
```

Run tests without installing pytest:

```powershell
& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" -m unittest discover -s tests
```

If `pytest` is installed, this also works:

```powershell
& "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe" -m pytest
```

## Ranking Formula

For document `d`, portfolio `P`, and decision time `t`:

```text
FinalScore(d, P, t) =
    alpha * SparseTextScore(d, query_P)
  + beta  * DenseEmbeddingScore(d, portfolio_profile_P)
  + gamma * EntityMatchScore(d, holdings_P)
  + delta * PortfolioExposureScore(d, P)
  + eta   * RecencyScore(d, t)
  + theta * EventImportanceScore(d)
```

The v1 default sets dense weight to `0.0`. Sparse, entity, portfolio exposure,
recency, and event scores are fully reported in each output row.

## Causal Rule

Financial retrieval must be point-in-time safe:

```text
available_at <= decision_time
```

The retrieval CLI filters unsafe documents before scoring. A future Apple
document is included in the sample corpus specifically to test that it is not
retrieved for the morning decision.

## FinGPT Handoff

`features/export_fingpt_contexts.py` produces JSONL records compatible with the
neighboring `Supportive_project_FinGPT_as_feature_engine` loader. Key fields:

- `portfolio_id`
- `decision_time`
- `retrieval_cutoff`
- `doc_id`
- `published_at`
- `available_at`
- `title`
- `body_excerpt`
- `matched_tickers`
- `matched_holdings`
- `evidence_scope`
- `retrieval_reason_tags`
- retrieval component scores
- source credibility and duplicate cluster IDs
- `document_hash`
- `fingpt_context`

The FinGPT Feature Engine should consume these records and perform structured
feature extraction. This repository should remain retrieval-first.

## Repository Layout

```text
FinPortfolio_IR/
  crawler/          raw collection and normalization
  indexing/         entity linking and BM25 sparse index
  retrieval/        portfolio query builder, hybrid ranker, retrieval CLI
  features/         FinGPT context export and retrieval diagnostics
  evaluation/       IR metrics and annotation pool helper
  configs/          ranker and sample portfolio configs
  data/             sample raw, processed, portfolios, qrels, exports
  docs/             methodology, contracts, experiments, active plan
  tests/            causal, entity, and ranking tests
  web/              static dashboard/search UI
  web_app.py        local standard-library HTTP server
```

## Future Work

The next milestones are:

1. Stabilize event-study diagnostics without leaking future returns into PPO.
2. Make daily retrieval route-aware across official macro, SEC, and company IR.
3. Build source-quality score v1 and quarantine weak sources.
4. Upgrade human adjudication and qrels for retrieval/extraction evaluation.
5. Improve Mistral/FinGPT extraction prompts using human and event-study
   feedback.
6. Run controlled PPO ablations:
   `base_macro` vs `base_macro + causal text`.
