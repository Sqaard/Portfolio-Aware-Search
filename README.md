# FinPortfolio IR

**Portfolio-Aware Search** — a source-first financial retrieval engine, and the
Big Data course project for ITMO, 2nd semester. The additive layer under
[`bigdata/`](bigdata) re-expresses the corpus-wide processing as MapReduce on
Apache Spark; the write-up is
[docs/BIG_DATA_INFRASTRUCTURE.md](docs/BIG_DATA_INFRASTRUCTURE.md), the live-demo
sequences are in [docs/DEFENCE_RUNBOOK.md](docs/DEFENCE_RUNBOOK.md), and the deck
is [presentations/BigData_ITMO.pptx](presentations/BigData_ITMO.pptx).

**To reproduce this on a clean machine, start at [REPRODUCE.md](REPRODUCE.md).**
Clone, install `requirements.txt`, and every command in that guide runs against
the 993-document corpus committed here — no downloads, no Spark, no API keys.

**Portfolio-Aware Search** is a source-first information-retrieval system for
US equities, and the Big Data course project for ITMO, 2nd semester. To
reproduce it on a clean machine, start at [REPRODUCE.md](REPRODUCE.md): it goes
from `git clone` to a green test run and prints the expected output of every
step. The distributed layer under [`bigdata/`](bigdata) re-expresses the
corpus-wide processing as MapReduce on Apache Spark; the write-up is
[docs/BIG_DATA_INFRASTRUCTURE.md](docs/BIG_DATA_INFRASTRUCTURE.md), the
live-demo sequences are in [docs/DEFENCE_RUNBOOK.md](docs/DEFENCE_RUNBOOK.md),
and the defence deck is
[presentations/BigData_ITMO.pptx](presentations/BigData_ITMO.pptx).

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

### Running the commands below

Every command uses `$py`. Set it once per shell to an interpreter that has this
project's requirements installed:

```powershell
$py = "python"
```

The Big Data commands additionally need PySpark, which is often installed in a
separate environment — point `$py` at that one, or let
[`deploy/run_spark.ps1`](deploy/run_spark.ps1) find it for you:

```powershell
$py = "$env:USERPROFILE\anaconda3\envs\tensorflow\python.exe"   # example
.\deploy\run_spark.ps1 bigdata.run_all --corpus repo_demo         # or this, which needs no $py
```

The blocks below are PowerShell. On Linux or macOS the same commands work with
three mechanical substitutions: drop the `& $py` prefix and call `python3`
directly, use forward slashes in paths, and replace PowerShell's trailing
backtick line-continuation with a trailing `\`.

## Big Data Infrastructure (course project)

An additive Big Data layer lives under [`bigdata/`](bigdata) and re-expresses the
heavy, corpus-wide processing (tokenization, inverted-index / BM25 statistics,
corpus analytics) as **MapReduce** on **Apache Spark**, with a portable
pure-Python multiprocessing engine behind the same interface for portability and
verification. No existing code was modified.

```powershell
# optional: install Spark (else the pure-Python local engine runs)
& $py -m pip install -r requirements-bigdata.txt

# full pipeline over the corpus committed here -> analytics.json, bm25_stats.json,
# REPORT.md; 993 documents, about a second of work, no Spark and no JVM needed
& $py -m bigdata.run_all --corpus repo_demo --query "inflation interest rates"

# choose the engine explicitly (default auto -> Spark if installed, else local)
& $py -m bigdata.run_inverted_index  --corpus repo_demo --engine spark --master "local[*]"
& $py -m bigdata.run_corpus_analytics --corpus repo_demo --engine local
```

`repo_demo` is the default corpus and the only large one in the repository
(`data/processed_documents/repo_demo_documents.jsonl`, 993 documents, 11.5 MB).
Artifacts go to `data/exports/bigdata/local_runs/<job>/`, which is git-ignored,
so a run never touches the committed reports.

The distributed output was verified **byte-identical** to the single-machine
`indexing/build_sparse_index.py` (`BM25Index`) on the 18,240-document macro
corpus, plus a Structured-Streaming / incremental auto-updater and a Docker Spark
cluster. That corpus is ~39 MB and is not in the repository; the two runs behind
the claim are, as
[`data/exports/bigdata/report_macro_local/`](data/exports/bigdata/report_macro_local)
and
[`report_macro_cluster/`](data/exports/bigdata/report_macro_cluster), whose
`document_frequencies.csv` files are the same 45,030 bytes. What a fresh clone
re-verifies for itself is set out in
[docs/BIG_DATA_INFRASTRUCTURE.md](docs/BIG_DATA_INFRASTRUCTURE.md) § 8; see also
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
git clone https://github.com/Sqaard/Portfolio-Aware-Search.git
cd Portfolio-Aware-Search
& $py -m pip install -r requirements.txt
```

`requirements.txt` is the complete set the test suite and every command on this
page need. `requirements-bigdata.txt` adds PySpark and is optional — without it
the Big Data jobs run on the portable pure-Python engine.

If the bare `python` on your machine resolves to PyPy or to an environment you do
not control, set `$py` to a CPython 3.9+ executable as above and use it
throughout.

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

Everything below writes into `data\exports\local_runs\`, which is git-ignored.
The similarly named files already committed under `data\exports\`
(`retrieved_docs_sample.jsonl`, `sample_run.csv`, `fingpt_contexts_sample.jsonl`,
`evidence_bundles_sample.jsonl`, `sample_metrics.csv`) are reference copies from
earlier runs — compare against them, but nothing here overwrites them.

Normalize local raw documents:

```powershell
& $py crawler\normalize_documents.py `
  --input data\raw_documents\sample_documents.jsonl `
  --metadata data\processed_documents\ticker_metadata.csv `
  --output data\exports\local_runs\documents.jsonl
```

Retrieve top-k causal documents for the sample portfolio:

```powershell
& $py retrieval\retrieve_for_portfolio.py `
  --documents data\exports\local_runs\documents.jsonl `
  --portfolio configs\sample_portfolio.yaml `
  --metadata data\processed_documents\ticker_metadata.csv `
  --decision-datetime 2022-03-15T09:30:00-05:00 `
  --top-k 10 `
  --output data\exports\local_runs\retrieved_docs_sample.jsonl `
  --run-csv data\exports\local_runs\sample_run.csv
```

Export FinGPT-ready contexts:

```powershell
& $py features\export_fingpt_contexts.py `
  --input data\exports\local_runs\retrieved_docs_sample.jsonl `
  --output data\exports\local_runs\fingpt_contexts_sample.jsonl
```

Export grouped evidence bundles:

```powershell
& $py features\export_evidence_bundles.py `
  --input data\exports\local_runs\retrieved_docs_sample.jsonl `
  --output data\exports\local_runs\evidence_bundles_sample.jsonl
```

Build the first-test FinGPT handoff package:

```powershell
& $py features\build_fingpt_handoff_package.py `
  --retrieval data\exports\local_runs\retrieved_docs_sample.jsonl `
  --output-dir data\exports\fingpt_handoff_sample
```

Run the cross-project FinGPT smoke test:

```powershell
& $py features\run_fingpt_handoff_smoke.py `
  --handoff-dir data\exports\fingpt_handoff_sample `
  --fingpt-project ..\Supportive_project_FinGPT_as_feature_engine
```

Build the SEC Dow 30 300-document PPO-aligned handoff:

```powershell
& $py features\build_sec_300_corpus.py `
  --inputs data\raw_documents\sec_dow30_filings_2010_2023.jsonl,data\raw_documents\sec_dow30_missing7_2010_2023.jsonl `
  --output-raw data\raw_documents\sec_dow30_2010_2023_300.jsonl `
  --output-processed data\processed_documents\sec_dow30_2010_2023_300_documents.jsonl `
  --metadata data\processed_documents\dow30_ticker_metadata.csv `
  --source-registry data\source_registry\source_registry.csv `
  --summary-output data\processed_documents\sec_dow30_2010_2023_300_summary.json `
  --target-docs 300

& $py features\build_sec_dow30_300_contexts.py `
  --documents data\processed_documents\sec_dow30_2010_2023_300_documents.jsonl `
  --metadata data\processed_documents\dow30_ticker_metadata.csv `
  --config configs\default.yaml `
  --portfolios-dir data\portfolios\sec_dow30_single `
  --output data\exports\sec_dow30_2010_2023\retrieved_contexts.jsonl `
  --manifest-output data\exports\sec_dow30_2010_2023\manifest.json `
  --output-count 300 `
  --rank-search-k 300

& $py features\validate_fingpt_handoff.py `
  --contexts data\exports\sec_dow30_2010_2023\retrieved_contexts.jsonl `
  --output data\exports\sec_dow30_2010_2023\handoff_validation.json

& $py features\run_fingpt_handoff_smoke.py `
  --handoff-dir data\exports\sec_dow30_2010_2023 `
  --fingpt-project ..\Supportive_project_FinGPT_as_feature_engine
```

Build the full SEC section/exhibit-level handoff:

```powershell
& $py features\build_sec_full_section_corpus.py `
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

& $py features\build_sec_section_contexts.py `
  --documents data\processed_documents\sec_dow30_2010_2023_300_sections_documents.jsonl `
  --metadata data\processed_documents\dow30_ticker_metadata.csv `
  --config configs\default.yaml `
  --output data\exports\sec_dow30_2010_2023_sections\retrieved_contexts.jsonl `
  --manifest-output data\exports\sec_dow30_2010_2023_sections\manifest.json `
  --output-count 300 `
  --rank-search-k 1000

& $py features\validate_fingpt_handoff.py `
  --contexts data\exports\sec_dow30_2010_2023_sections\retrieved_contexts.jsonl `
  --output data\exports\sec_dow30_2010_2023_sections\handoff_validation.json

& $py features\run_fingpt_handoff_smoke.py `
  --handoff-dir data\exports\sec_dow30_2010_2023_sections `
  --fingpt-project ..\Supportive_project_FinGPT_as_feature_engine
```

Build official macro release documents:

```powershell
& $py features\build_official_macro_documents.py `
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
& $py features\build_daily_retrieval_contexts.py `
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
& $py features\build_text_feature_baseline.py `
  --contexts data\exports\daily_retrieval_ppo_full_dis_legacy\retrieved_contexts.jsonl `
  --output-dir data\exports\daily_retrieval_ppo_full_dis_legacy\codex_rule_text_features `
  --teacher-size 300

& $py features\merge_text_features_with_base_panel.py `
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
& $py web_app.py --host 127.0.0.1 --port 8765
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

Evaluate the sample ranking, using the run CSV written above:

```powershell
& $py evaluation\evaluate_ir_metrics.py `
  --qrels data\annotations\sample_qrels.csv `
  --run data\exports\local_runs\sample_run.csv `
  --output data\exports\local_runs\sample_metrics.csv
```

The committed `data\exports\sample_metrics.csv` is a reference copy from an
earlier configuration. A fresh run writes to `data\exports\local_runs\` and its
figures need not match it exactly; what should match is the ranking behaviour.

Run all configured ranking ablations:

```powershell
& $py evaluation\run_ablation_suite.py `
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
& $py evaluation\run_ablation_suite.py `
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
& $py evaluation\build_html_report.py `
  --metrics data\exports\ablation_batch_sample\ablation_metrics_by_method.csv `
  --diagnostics data\exports\ablation_batch_sample\ablation_diagnostics_by_method.csv `
  --output data\exports\ablation_batch_sample\retrieval_report.html `
  --title "FinPortfolio IR Batch Retrieval Report"
```

Build an annotation pool for later human review:

```powershell
& $py evaluation\build_annotation_pool.py `
  --input data\exports\ablation_batch_sample\ablation_retrieved_all.jsonl `
  --qrels data\annotations\bootstrap_sample_qrels.csv `
  --output data\annotations\annotation_pool_batch_sample.csv
```

The current sample labels are bootstrap labels. Human-reviewed labels should be
kept in a separate qrels file and validated before using them for final IR
comparisons.

Export reviewed pool labels back to qrels:

```powershell
& $py evaluation\export_qrels_from_pool.py `
  --input data\annotations\annotation_pool_batch_sample.csv `
  --output data\annotations\human_qrels_v1.csv `
  --issues-output data\annotations\human_qrels_v1_export_issues.csv `
  --label-source human_v1
```

Run the tests. Both runners work after `pip install -r requirements.txt`:

```powershell
& $py -m unittest discover -s tests
# -> Ran 243 tests, OK (skipped=5)

& $py -m pytest -q
# -> 238 passed, 5 skipped
```

Five skips on a fresh clone: the four real-Spark parity tests, plus the
shipped-index servability check, which has nothing to check until the search
index is built (see [REPRODUCE.md](REPRODUCE.md) step 5). After that it is four. The Spark four execute once
`requirements-bigdata.txt` and a Java 8/11/17 runtime are installed. See
[docs/BIG_DATA_INFRASTRUCTURE.md](docs/BIG_DATA_INFRASTRUCTURE.md) § 8 for what
they do and do not cover.

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
