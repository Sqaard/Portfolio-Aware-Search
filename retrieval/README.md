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
