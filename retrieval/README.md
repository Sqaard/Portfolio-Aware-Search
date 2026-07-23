# Retrieval

This folder contains the portfolio query builder, hybrid ranker, and retrieval
CLI.

Run the end-to-end sample:

```powershell
python retrieval/retrieve_for_portfolio.py `
  --documents data/processed_documents/documents.jsonl `
  --portfolio configs/sample_portfolio.yaml `
  --metadata data/processed_documents/ticker_metadata.csv `
  --decision-datetime 2022-03-15T09:30:00-05:00 `
  --top-k 10 `
  --method full_hybrid `
  --output data/exports/retrieved_docs_sample.jsonl `
  --run-csv data/exports/sample_run.csv
```

The ranker always filters documents by `available_at <= decision_time` before
scoring.

Configured methods include `bm25_only`, `bm25_entity`,
`bm25_entity_portfolio`, `full_hybrid`, and `full_hybrid_diversified`.

`full_hybrid_diversified` keeps the same transparent score components, adds a
small source-credibility feature, and applies top-k post-processing:

- max documents per duplicate cluster;
- max documents per matched portfolio holding;
- minimum market and sector evidence slots when available.

Evidence-unit retrieval uses the same ranker, but the input rows are smaller
decision-grade units such as SEC sections, earnings exhibits, company IR fact
blocks, and macro observations:

```powershell
python features/build_evidence_unit_retrieval_package.py `
  --input data\processed_documents\sec_dow30_ppo_2010_2023_1800_with_dis_legacy_sections_documents.jsonl,data\processed_documents\official_macro_2010_2023_documents.jsonl `
  --output-dir data\exports\evidence_unit_retrieval_v1 `
  --decision-datetime 2022-03-15T09:30:00-05:00 `
  --top-k 10 `
  --method full_hybrid
```

The output preserves `parent_doc_id` and `evidence_unit_type`, so downstream
LLM features can cite the exact section or fact block instead of a whole page.
