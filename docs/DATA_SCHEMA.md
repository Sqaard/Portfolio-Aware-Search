# Data Schema

## Normalized Document JSONL

Required fields:

| Field | Type | Description |
| --- | --- | --- |
| `doc_id` | string | Stable document identifier. |
| `title` | string | Headline or title. |
| `body` | string | Text body used for indexing and excerpts. |
| `source` | string | Dataset, feed, or fixture name. |
| `source_type` | string | Sample, SEC filing, company IR, RSS, news, or other source class. |
| `url` | string | Source URL or source-local ID. |
| `source_registry_id` | string | Stable ID from the source registry when available. |
| `canonical_url` | string | Normalized URL for duplicate checks, health checks, and favorite matching. |
| `source_reliability_tier` | string | Source quality tier such as `official`, `company`, `licensed`, `user_preferred`, or `unknown`. |
| `robots_policy` | string | Human-readable crawl/API policy note captured before live ingestion. |
| `last_url_check_at` | timestamp | Last URL health-check timestamp, if checked. |
| `fetch_status` | string | Last fetch status such as `ok`, `failed`, or empty for unchecked. |
| `content_license_note` | string | Redistribution/use note for downstream display and storage decisions. |
| `published_at` | timestamp | Original publication timestamp. |
| `first_seen_at` | timestamp | First timestamp the project observed the document. |
| `available_at` | timestamp | Earliest timestamp allowed for model use. |
| `ingested_at` | timestamp | Timestamp when the local corpus ingested this version. |
| `version_id` | string | Source-local version ID, default `v1`. |
| `is_revision` | bool | Whether this row is a revised document version. |
| `revision_of` | string | Prior document/version ID if this is a revision. |
| `duplicate_cluster_id` | string | Content-derived near/exact duplicate cluster key. |
| `tickers_detected` | list[string] | Linked tickers and pseudo-entities. |
| `matched_tickers` | list[string] | Tickers used by retrieval/ranking. |
| `matched_holdings` | list[string] | Matched portfolio holdings, excluding broad `MARKET`. |
| `company_names_detected` | list[string] | Linked company names. |
| `sectors_detected` | list[string] | Linked sectors. |
| `sector_tags` | list[string] | Sector tags for ranking/export. |
| `event_tags` | list[string] | Event tags for ranking/export. |
| `risk_terms` | list[string] | Matched risk terms. |
| `source_credibility` | float | Transparent source prior for future reranking. |
| `event_type` | string | Optional source or rule-based event tag. |
| `language` | string | Language code. |
| `document_hash` | string | SHA-256 fingerprint of the text and timestamps. |

## Portfolio YAML

```yaml
portfolio_id: sample_portfolio_001
holdings:
  AAPL: 0.12
  MSFT: 0.10
  JPM: 0.07
  UNH: 0.06
```

Weights are used as ranking exposure signals. They are not trading orders.

## Retrieval Result JSONL

Each row is one retrieved document for one portfolio decision time.

Key fields:

- `query_id`
- `decision_id`
- `portfolio_id`
- `portfolio_snapshot_id`
- `decision_date`
- `decision_time`
- `decision_datetime`
- `retrieval_cutoff`
- `retrieval_query_lex`
- `retrieval_query_sem`
- `evidence_bundle_id`
- `rank`
- `doc_id`
- `evidence_unit_id`
- `parent_doc_id`
- `evidence_unit_type`
- `evidence_unit_index`
- `evidence_unit_claim_type`
- `source`
- `source_type`
- `source_registry_id`
- `source_reliability_tier`
- `published_at`
- `first_seen_at`
- `available_at`
- `ingested_at`
- `duplicate_cluster_id`
- `title`
- `body_excerpt`
- `url`
- `canonical_url`
- `fetch_status`
- `matched_tickers`
- `matched_holdings`
- `event_tags`
- `risk_terms`
- `source_credibility`
- `source_authority`
- `source_timeliness`
- `source_legal_liability`
- `source_numeric_density`
- `source_promotion_risk`
- `source_fetch_method`
- `source_update_frequency`
- `source_point_in_time_policy`
- `source_documentation_url`
- `source_coverage_scope`
- `evidence_scope`
- `portfolio_weight_sum`
- `sparse_score`
- `dense_score`
- `entity_score`
- `portfolio_exposure_score`
- `recency_score`
- `event_importance_score`
- `source_credibility_score`
- `source_authority_score`
- `source_timeliness_score`
- `source_legal_liability_score`
- `source_numeric_density_score`
- `source_promotion_risk_score`
- `source_quality_score`
- `final_score`
- `retrieval_reason_tags`
- `diversification_applied`
- `ranking_stage`
- `reason`
- `document_hash`

Hard invariant:

```text
available_at <= retrieval_cutoff
```

## FinGPT Context JSONL

The context export keeps the retrieval fields needed by the neighboring FinGPT
Feature Engine and adds:

- `fingpt_context`: compact prompt-ready evidence text.
- `retrieval_reason_tags`: deterministic tags such as `exact_ticker`,
  `high_exposure`, `fresh_24h`, and `high_source_credibility`.

No FinGPT inference happens in this project.

## Evidence Unit JSONL

`features/build_evidence_units.py` writes schema-compatible retrieval units.
Each row can still be loaded as a `FinancialDocument`, but it has extra
evidence-unit metadata:

- `evidence_unit_id`
- `parent_doc_id`
- `evidence_unit_type`
- `evidence_unit_index`
- `evidence_unit_claim_type`

Current unit types:

- `sec_section`
- `sec_exhibit`
- `company_ir_fact_block`
- `macro_observation`
- `document_block` when unknown documents are explicitly included

The purpose is to let retrieval rank decision-grade units such as Item 1A risk
factors, earnings exhibits, company IR fact blocks, and macro observations
instead of only whole pages.

## Evidence Bundle JSONL

`features/export_evidence_bundles.py` groups retrieved rows by
`query_id/method` and writes one JSON object per decision bundle.

Key fields:

- `evidence_bundle_id`
- `query_id`
- `decision_id`
- `method`
- `portfolio_id`
- `portfolio_snapshot_id`
- `portfolio_holdings`
- `decision_time`
- `retrieval_cutoff`
- `retrieval_query_lex`
- `retrieval_query_sem`
- `stock_evidence`
- `sector_evidence`
- `market_evidence`
- `portfolio_evidence`
- `diagnostics`

The evidence arrays preserve deterministic retrieval scores, timestamps,
duplicate cluster IDs, matched holdings, and reason tags. This is the preferred
handoff shape for the FinGPT Feature Engine once its loader supports grouped
contexts.

## Event Ledger V1

`features/build_event_ledger_v1.py` creates a point-in-time event ledger from
retrieved official documents plus optional SEC submissions/companyfacts API
caches. This is a READ/FEATURE layer artifact only; it does not promote
features into PPO state.

Core files:

- `data/event_ledger_v1/events.jsonl`
- `data/event_ledger_v1/daily_event_features.csv`
- `data/event_ledger_v1/coverage_by_ticker_year.csv`
- `data/event_ledger_v1/pit_validation.json`
- `data/event_ledger_v1/source_cards_v1.csv`
- `data/event_ledger_v1/manifest.json`

Required event fields:

- `event_id`
- `source_id`
- `source_family`
- `ticker`
- `company_id`
- `cik`
- `event_type`
- `event_subtype`
- `event_time_utc`
- `available_at_utc`
- `retrieval_cutoff_utc`
- `decision_time_utc`
- `market_session_tag`
- `before_open`
- `during_market`
- `after_close`
- `document_hash`
- `raw_source_url`
- `source_document_id`
- `title`
- `body_excerpt`
- `extracted_features`
- `source_reliability_score`
- `timestamp_confidence`
- `point_in_time_valid_flag`
- `pit_failure_reason`
- `metadata`

Hard invariant:

```text
available_at_utc <= retrieval_cutoff_utc <= decision_time_utc
```

Daily CHRL-compatible features are conservative aggregations by
`ticker/decision_date`. They are not PPO-ready until coverage, PIT, IC,
macro/sector/source controls, temporal nulls, and an obs-dim matched noise
placebo pass.

## Source Registry CSV

`data/source_registry/source_registry.csv` records source-level provenance
before any large crawling expansion. Required fields:

- `source_registry_id`
- `name`
- `base_url`
- `source_type`
- `source_reliability_tier`
- `robots_policy`
- `content_license_note`
- `source_credibility`
- `preferred_for_v1`
- `notes`

Source cards v1 may also include richer fields used for explainable source
quality and route-aware ranking:

- `authority_score`
- `timeliness_score`
- `legal_liability_score`
- `numeric_density_score`
- `promotion_risk_score`
- `fetch_method`
- `update_frequency`
- `point_in_time_policy`
- `preferred_endpoints`
- `documentation_url`
- `coverage_scope`

Favorite websites are allowed to affect local ranking priority, but they do not
raise `source_credibility` unless the source registry explicitly assigns a
higher reliability tier.

## FinGPT Handoff Package

`features/build_fingpt_handoff_package.py` writes a first-test handoff
directory:

- `retrieved_contexts.jsonl`
- `evidence_bundles.jsonl`
- `handoff_manifest.json`
- `handoff_validation.json`
- `handoff_report.html`

`retrieved_contexts.jsonl` is the flat compatibility input for the current
`Supportive_project_FinGPT_as_feature_engine` loader. The bundle file preserves
the richer grouped evidence shape for the next loader revision.

## Qrels CSV

```csv
query_id,doc_id,relevance
sample_portfolio_001_2022-03-15,doc_000001,2
```

Labels:

- `0`: irrelevant
- `1`: mentions holding/context but not decision-useful
- `2`: useful evidence
- `3`: highly relevant and timely for the portfolio decision

Qrels files may include extra provenance columns such as `label_source`,
`annotator`, and `notes`; the evaluator reads only `query_id`, `doc_id`, and
`relevance`.

Current sample qrels are bootstrap labels, not a substitute for a human-reviewed
test set.

## Annotation Pool CSV

`evaluation/build_annotation_pool.py` creates a review table from retrieval
outputs. It deduplicates documents retrieved by multiple methods.

Important fields:

- `query_id`
- `portfolio_id`
- `decision_time`
- `doc_id`
- `title`
- `matched_tickers`
- `best_rank`
- `methods`
- `ranks_by_method`
- `scores_by_method`
- `review_priority`
- `existing_relevance`
- `relevance`
- `label_source`
- `annotator`
- `notes`

## Query Set CSV

Batch evaluation can use a query set CSV:

```csv
query_id,portfolio,decision_datetime,notes
sample_portfolio_001_2022-03-15,configs/sample_portfolio.yaml,2022-03-15T09:30:00-05:00,Balanced sample.
```

Required columns:

- `query_id`
- `portfolio`
- `decision_datetime`

The `portfolio` path is resolved relative to the project root when used by
`evaluation/run_ablation_suite.py`.
