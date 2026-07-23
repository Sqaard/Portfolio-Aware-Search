# Features

The retrieval layer exports two downstream artifacts:

- `export_fingpt_contexts.py`: JSONL evidence contexts for the FinGPT Feature
  Engine. It does not call FinGPT.
- `export_evidence_bundles.py`: grouped evidence bundles with stock, sector,
  market, and full portfolio evidence arrays.
- `validate_fingpt_handoff.py`: leakage/schema validation before the FinGPT
  Feature Engine consumes retrieved contexts.
- `build_fingpt_handoff_package.py`: one-command package builder for first
  FinGPT-side smoke tests.
- `run_fingpt_handoff_smoke.py`: cross-project smoke runner that calls the
  FinGPT Feature Engine and summarizes prompt, extraction, feature, and
  provenance outputs.
- `build_retrieval_features.py`: diagnostic numeric retrieval features for
  later analysis. These are not PPO inputs until a separate controlled ablation.
- `build_daily_retrieval_contexts.py`: PPO-aligned daily causal retrieval over
  a base panel, with portfolio-level and stock-level layers.
- `build_text_feature_baseline.py`: deterministic Codex-rule document,
  stock-day, and portfolio-day text features from retrieved contexts.
- `build_evidence_units.py`: build schema-compatible evidence units from SEC
  sections/exhibits, company IR fact blocks, and official macro observations.
- `build_evidence_unit_retrieval_package.py`: one-command package builder that
  creates evidence units, retrieves over them, exports a TREC-style run CSV, and
  writes grouped evidence bundles.
- `merge_text_features_with_base_panel.py`: left-join daily text features into
  a PPO base panel for immediate `base_macro + text` experiments.
- `normalize_sec_section_documents_fast.py`: SEC-specific fast normalizer for
  large section/exhibit corpora where ticker/source metadata is already
  authoritative.
- `combine_jsonl_by_key.py`: combine generated JSONL corpora while dropping
  duplicate `doc_id` rows.
- `run_mistral_teacher_seed_comparison.py`: run Mistral on
  `codex_teacher_seed.jsonl` and produce a disagreement report against
  Codex-rule teacher labels.
- `build_companyfacts_enrichment_v1.py`: checkpointed SEC XBRL companyfacts
  enrichment. It writes one ticker checkpoint at a time, creates a retry queue
  for failed/time-out tickers, and can merge structured facts back onto the
  event ledger without modifying the stable base ledger.

Example:

```powershell
python features/export_fingpt_contexts.py `
  --input data/exports/retrieved_docs_sample.jsonl `
  --output data/exports/fingpt_contexts_sample.jsonl
```

Grouped evidence bundle export:

```powershell
python features/export_evidence_bundles.py `
  --input data/exports/retrieved_docs_sample.jsonl `
  --output data/exports/evidence_bundles_sample.jsonl
```

Evidence-unit export for future section/fact-block retrieval:

```powershell
python features/build_evidence_units.py `
  --input data\processed_documents\sec_dow30_ppo_2010_2023_1800_with_dis_legacy_sections_documents.jsonl,data\processed_documents\official_macro_2010_2023_documents.jsonl `
  --output data\processed_documents\evidence_units_v1.jsonl `
  --summary-output data\processed_documents\evidence_units_v1_summary.json
```

Evidence-unit retrieval package:

```powershell
python features/build_evidence_unit_retrieval_package.py `
  --input data\processed_documents\sec_dow30_ppo_2010_2023_1800_with_dis_legacy_sections_documents.jsonl,data\processed_documents\official_macro_2010_2023_documents.jsonl `
  --output-dir data\exports\evidence_unit_retrieval_v1 `
  --decision-datetime 2022-03-15T09:30:00-05:00 `
  --top-k 10 `
  --method full_hybrid
```

First-test handoff package:

```powershell
python features/build_fingpt_handoff_package.py `
  --retrieval data/exports/retrieved_docs_sample.jsonl `
  --output-dir data/exports/fingpt_handoff_sample
```

Run the FinGPT Feature Engine smoke test from this project:

```powershell
python features/run_fingpt_handoff_smoke.py `
  --handoff-dir data/exports/fingpt_handoff_sample `
  --fingpt-project ../Supportive_project_FinGPT_as_feature_engine
```

Full PPO daily text feature package:

```powershell
python features/build_daily_retrieval_contexts.py `
  --base-panel ..\processed_final_fixed_external_lagclean_full.csv `
  --documents data\processed_documents\sec_dow30_ppo_2010_2023_1800_with_dis_legacy_sections_documents.jsonl,data\processed_documents\official_macro_2010_2023_documents.jsonl `
  --portfolio-top-k 5 `
  --ticker-top-k 1 `
  --ticker-date-stride 8 `
  --lookback-days 365 `
  --max-contexts-total 30000 `
  --output data\exports\daily_retrieval_ppo_full_dis_legacy\retrieved_contexts.jsonl `
  --manifest-output data\exports\daily_retrieval_ppo_full_dis_legacy\manifest_daily.json

python features/build_text_feature_baseline.py `
  --contexts data\exports\daily_retrieval_ppo_full_dis_legacy\retrieved_contexts.jsonl `
  --output-dir data\exports\daily_retrieval_ppo_full_dis_legacy\codex_rule_text_features `
  --teacher-size 300
```

Mistral-vs-Codex seed comparison:

```powershell
$env:MISTRAL_API_KEY="..."
python features/run_mistral_teacher_seed_comparison.py `
  --seed data\exports\daily_retrieval_ppo_full_dis_legacy\codex_rule_text_features\codex_teacher_seed.jsonl `
  --output-dir data\exports\mistral_vs_codex_seed
```

Checkpointed SEC companyfacts enrichment:

```powershell
python features/build_companyfacts_enrichment_v1.py `
  --base-events data\event_ledger_v1\events.jsonl `
  --output-dir data\event_ledger_companyfacts_v1 `
  --ticker-timeout-seconds 90 `
  --request-retries 4 `
  --max-retries 2
```
