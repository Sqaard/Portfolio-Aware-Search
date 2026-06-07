# Implementation Plan

Status date: 2026-05-15.

This file is the active engineering plan. It is intentionally compact. The
project methodology lives in `MAIN_METHODOLOGY.md`; current datasets and
experiments live in `CURRENT_ARTIFACTS_AND_EXPERIMENTS.md`.

## Current Objective

Implement the next auditable FinIR engineering layer:

```text
stronger event-study diagnostics
    + route-aware retrieval
    + source-quality scoring
    + human-adjudication support
```

while preserving point-in-time safety and keeping all future-return diagnostics
out of PPO features.

## Non-Negotiable Constraints

- Enforce `available_at <= retrieval_cutoff <= decision_time`.
- Never write event-study columns into merge-ready PPO panels.
- Keep source quality, extraction quality, and user favorites separate.
- Keep BM25 as the mandatory retrieval baseline.
- Preserve provenance fields required by `DATA_SCHEMA.md` and
  `FINGPT_HANDOFF.md`.
- Do not add learned reranking before human qrels exist.

## Sprint Ticket 1: Stabilize Event-Study Feedback

Priority: highest.

Current script:

```text
features/build_event_study_feedback.py
```

Implemented already:

- event windows: pre-10d, pre-5d, event day, +1d, +3d, +5d, +10d, +21d;
- `decision_date` and `available_at_first_trading_day` policies;
- equal-weight Dow abnormal return;
- Codex/Mistral direction accuracy;
- numeric correlation diagnostics;
- machine-readable CSV/JSON/markdown reports.

Next deliverables:

- sector-neutral abnormal returns;
- SPY/DIA benchmark option with graceful fallback;
- before-open / after-close event-date policy;
- duplicate-event collapse by `duplicate_cluster_id`, `document_hash`, ticker,
  and event date;
- volume reaction metrics;
- volatility or absolute-return surprise metrics;
- confidence intervals by source type/source family;
- event-study columns in human adjudication exports.

Acceptance:

- existing CLI remains backward compatible;
- outputs clearly mark future-return columns as diagnostic-only;
- no event-study diagnostics are written to PPO panels;
- tests cover duplicate collapse, benchmark fallback, and event-date policy.

## Sprint Ticket 2: Make Retrieval Route-Aware

Current relevant files:

```text
finportfolio_ir/query_intent.py
features/build_daily_retrieval_contexts.py
retrieval/hybrid_ranker.py
retrieval/retrieve_for_portfolio.py
```

Goal:

Use deterministic query-intent routing to choose candidate channels, not only
to report metadata.

Routes to support now:

- `official_macro`;
- `sec_filing_section`;
- `sec_filing_exhibit`;
- `company_ir`.

Future routes:

- `structured_facts`;
- `market_news`;
- `favorite_websites`;
- `external_web`;
- `local_corpus`.

Required behavior:

- portfolio-level retrieval prefers macro/market/rates/credit evidence;
- stock-level retrieval prefers ticker-specific SEC/company IR evidence;
- generic macro should not fill stock slots unless explicitly linked to the
  holding, sector, or event.

Diagnostics to add:

- `selected_routes`;
- `candidate_count_by_route`;
- `final_topk_count_by_route`;
- `empty_route_warnings`;
- `fallback_route_used`;
- `source_family_slots`.

Acceptance:

- route-aware mode can be disabled;
- route-aware output is deterministic;
- schema/provenance fields remain intact;
- tests cover macro portfolio route, SEC ticker route, company IR slot, and
  zero-candidate fallback.

## Sprint Ticket 3: Source-Quality Score v1

Goal:

Create a deterministic source-quality score that decides source-family
eligibility for extraction and PPO ablations.

Inputs:

- source registry;
- daily retrieval exports;
- source-quality audit outputs;
- event-study feedback;
- human adjudication CSVs when available.

Score components:

- `provenance_integrity_score`;
- `timestamp_reliability_score`;
- `dated_detail_url_quality_score`;
- `body_length_structure_score`;
- `duplicate_penalty_score`;
- `extraction_readiness_score`;
- `human_adjudication_score`;
- `event_study_diagnostic_score`;
- `coverage_by_ticker_date_regime_score`.

Required reports:

- by source family;
- by source type;
- by source;
- by source registry id;
- by ticker;
- by train/test split;
- by regime.

Acceptance:

- favorite sources do not automatically receive higher credibility;
- timestamp failures block PPO eligibility;
- quarantine sources are explicit;
- score is reproducible and explainable by components.

## Sprint Ticket 4: Human Adjudication And Qrels Upgrade

Goal:

Turn preference-only review into a validated human-labeled evaluation set.

Relevant files:

```text
evaluation/build_annotation_pool.py
evaluation/export_qrels_from_pool.py
evaluation/validate_qrels.py
docs/ANNOTATION_GUIDE.md
```

Required adjudication columns:

- `preferred_extractor`;
- `correct_impact_direction`;
- `correct_event_signal_tags`;
- `risk_intensity`;
- `uncertainty_intensity`;
- `sentiment_proxy`;
- `portfolio_action_relevance`;
- `short_justification`;
- `event_study_event_day_label`;
- `event_study_post_1d_label`;
- `event_study_post_3d_label`;
- `event_study_post_10d_label`;
- `event_study_post_21d_label`.

Acceptance:

- bootstrap labels stay development-only;
- human labels are stored separately, e.g.
  `data/annotations/human_qrels_v1.csv`;
- qrels validation is strict;
- BM25, hybrid, and route-aware hybrid are evaluated against human labels.

## Next PPO-Facing Plan

After the four tickets:

1. Build source-quality-gated feature sets.
2. Rerun Mistral with improved prompts and source-family instructions.
3. Compare Codex-rule, Mistral v1/v2, and FinGPT extraction against human
   labels and event-study diagnostics.
4. Export clean feature sets for PPO.
5. Run controlled ablations:
   - E0: numerical baseline;
   - E1: base macro;
   - E2: base macro + Codex-rule text;
   - E3: base macro + source-quality-gated text;
   - E4/E5: base macro + improved Mistral/FinGPT text.

## Validation Commands

Run tests after code changes:

```powershell
python -B -m unittest discover -s tests
```

Event-study tests:

```powershell
python -B -m unittest tests.test_event_study_feedback
```

Handoff validation:

```powershell
python features/validate_fingpt_handoff.py `
  --contexts data/exports/daily_retrieval_ppo_full_dis_legacy/retrieved_contexts.jsonl `
  --output-report data/exports/daily_retrieval_ppo_full_dis_legacy/handoff_validation_check.json
```

Future qrels validation:

```powershell
python evaluation/validate_qrels.py `
  --qrels data/annotations/human_qrels_v1.csv `
  --run data/exports/ablation_batch_sample/ablation_run.csv `
  --output data/exports/ablation_batch_sample/human_qrels_v1_validation.csv
```

## Out Of Scope

- LLM direct trading decisions.
- Event-study diagnostics inside PPO inputs.
- Dense-only retrieval as default.
- Learned reranking before human qrels.
- Social media/trends as PPO features.
- Anti-bot scraping or source-term violations.
- New feature columns without provenance and validation.

