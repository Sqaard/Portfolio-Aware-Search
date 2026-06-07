# Crawler

The v1 project is reproducible from local sample data. Live RSS collection is
provided only as an extension point.

The next corpus milestone is source-registry-first: prefer official APIs, RSS,
and downloadable data over brittle scraping. User favorite websites can affect
search priority, but they do not increase source credibility.

Normalize local raw documents:

```powershell
python crawler/normalize_documents.py `
  --input data/raw_documents/sample_documents.jsonl `
  --metadata data/processed_documents/ticker_metadata.csv `
  --source-registry data/source_registry/source_registry.csv `
  --output data/processed_documents/documents.jsonl
```

Documents without valid timezone-aware timestamps are excluded by default.

Official company archive discovery:

```powershell
python crawler/company_source_archive_discovery.py `
  --sources data/source_registry/official_company_source_urls_review_2026-05-13.csv `
  --metadata data/processed_documents/dow30_ticker_metadata.csv `
  --output-documents data/raw_documents/company_official_archive_documents.jsonl `
  --detail-manifest-output data/source_registry/company_official_archive_detail_manifest.csv `
  --source-manifest-output data/source_registry/company_official_archive_source_manifest.csv `
  --vendor-queue-output data/source_registry/company_official_archive_vendor_queue.csv `
  --summary-output data/source_registry/company_official_archive_summary.json `
  --start-year 2010 `
  --end-year 2023
```

This pass validates dated detail pages before writing documents. Top-level IR
or newsroom pages are not treated as documents. Sources whose archives are
behind Q4/GCS/QuoteMedia/WordPress/browser-only layers are written to the
vendor queue for source-specific adapters.

Fast Q4-only probe across the whole source list:

```powershell
python crawler/company_source_archive_discovery.py `
  --sources data/source_registry/official_company_source_urls_review_2026-05-13.csv `
  --metadata data/processed_documents/dow30_ticker_metadata.csv `
  --output-documents data/raw_documents/company_archive_q4_only_2021_probe_documents.jsonl `
  --detail-manifest-output data/source_registry/company_archive_q4_only_2021_probe_detail_manifest.csv `
  --source-manifest-output data/source_registry/company_archive_q4_only_2021_probe_source_manifest.csv `
  --vendor-queue-output data/source_registry/company_archive_q4_only_2021_probe_vendor_queue.csv `
  --summary-output data/source_registry/company_archive_q4_only_2021_probe_summary.json `
  --start-year 2021 `
  --end-year 2021 `
  --disable-rss `
  --disable-wordpress `
  --disable-generic-html
```

Use `--source-offset`, `--source-limit`, `--ticker`, and `--source-type` to
run large historical crawls in reproducible shards.
