# Indexing

This folder contains the v1 sparse retrieval path and rule-based entity linking.

The working path is:

```powershell
python indexing/entity_linking.py `
  --input data/raw_documents/sample_documents.jsonl `
  --metadata data/processed_documents/ticker_metadata.csv `
  --output data/processed_documents/documents.jsonl

python indexing/build_sparse_index.py `
  --documents data/processed_documents/documents.jsonl `
  --output data/processed_documents/sparse_index.json
```

Dense indexing is a future extension and is deliberately kept behind a stub.

## Browser search index

The local dashboard can use a derived SQLite/FTS5 search index for the full
PPO-aligned corpus. It keeps three layers together:

- `documents_fts`: BM25-style full-text lookup over title/body/tickers/tags.
- `documents`: causal metadata, source family, timestamps, duplicate cluster,
  provenance, and the compact JSON record used by the web API.
- `document_features`: precomputed rule-based text signals used as a
  signal-aware ranking layer, not as the only retrieval key.

Build it with:

```powershell
python -B indexing/build_search_index.py `
  --documents data/processed_documents/sec_macro_company_ir_ppo_2010_2023_documents.jsonl `
  --output data/search_index/finportfolio_search.sqlite
```

The web app only uses the index when its manifest matches the current documents
file and mtime; otherwise it safely falls back to the JSONL scan path.
