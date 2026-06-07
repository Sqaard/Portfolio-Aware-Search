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
- `evidence_scope`
- `portfolio_weight_sum`
- `sparse_score`
- `dense_score`
- `entity_score`
- `portfolio_exposure_score`
- `recency_score`
- `event_importance_score`
- `source_credibility_score`
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
