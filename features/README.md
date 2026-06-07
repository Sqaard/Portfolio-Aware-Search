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
