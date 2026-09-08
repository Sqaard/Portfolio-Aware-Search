# Innovative IR Implementation Plan

Status date: 2026-06-15.

This document turns the source-detective methodology into an engineering plan
for Portfolio-Aware Search.

The north star:

```text
source -> evidence -> meaning -> feature -> action -> improvement
```

Portfolio-Aware Search owns the first permission:

```text
right to read
```

It decides which public information is reliable, timely, traceable, and
portfolio-relevant enough to be passed to FinGPT and then to CHRL.

## Local Methodology Sources

Core project methodology:

- `docs/MAIN_METHODOLOGY.md`
- `docs/DATA_SCHEMA.md`
- `docs/FINGPT_HANDOFF.md`
- `docs/CURRENT_ARTIFACTS_AND_EXPERIMENTS.md`
- `crawler/README.md`
- `evaluation/README.md`

World-picture synthesis:

External to this repository, in the companion RL project
(`data_RLagent_for_Joseph/experience/WORLD OF BEST TRADING BOT/`):

- `IR_01_BEST_FINGPT_WORLD_PICTURE_ECONOMIC.md`
- `IR_02_BEST_FINGPT_WORLD_PICTURE_TECHNICAL.md`
- `IR_03_BEST_FINGPT_WORLD_PICTURE_INTEGRATED.md`
- `IR_E_INNOVATIVE_METHODOLOGY_AND_PLAN.md`

Current implementation anchors:

- `crawler/source_registry.py`
- `crawler/normalize_documents.py`
- `crawler/live_incremental_fetch.py`
- `indexing/build_search_index.py`
- `retrieval/hybrid_ranker.py`
- `finportfolio_ir/query_intent.py`
- `features/export_evidence_bundles.py`
- `features/export_fingpt_contexts.py`
- `evaluation/evaluate_ir_metrics.py`
- `evaluation/calibrate_search_reranker.py`

## Agent Skill Optimization Layer

SkillLens/SkillOpt is used as a lightweight self-improvement discipline for the
IR agent itself. It does not train model weights and does not change ranking
behavior directly. Instead, it keeps a project-specific `agent_skill.md`
aligned with the current methodology:

```powershell
python tools\skillopt_ir\run_ir_skill_loops.py --loops 5 --restart
```

Stage closure protocol:

```text
stage-specific checks -> SkillLens/SkillOpt loop -> methodology closure report
```

Concretely, after every methodology stage or material retrieval/crawler change:

```powershell
python tools\skillopt_ir\run_ir_skill_loops.py --loops 5
python evaluation\build_ir_methodology_closure.py --strict
```

A stage is not considered closed until the latest SkillOpt loop artifact shows
no failed checks and the closure report remains green. For a clean reset of the
skill document, use `--restart`; for normal after-stage operation, omit it so
the loop validates the current skill in place.

The five deterministic loops mirror the active IR stages:

1. source/crawler discipline;
2. evidence-unit retrieval;
3. hybrid ranking and promotion gates;
4. live stability and smoke regression;
5. FinGPT/CHRL handoff.

Each loop proposes one bounded edit, scores the resulting skill against
required project terms and safety constraints, and accepts the edit only if the
validation score improves. Current run artifacts:

- `agent_skill.md`
- `data/exports/skillopt_ir_loops_v1/loop_summary.csv`
- `data/exports/skillopt_ir_loops_v1/run_summary.json`
- `data/exports/skillopt_ir_loops_v1/best_agent_skill.md`
- `data/exports/methodology_closure_v1/phase_status.csv`
- `data/exports/methodology_closure_v1/methodology_closure.md`

Current validation result: 5/5 loops accepted, final skill score 56/56. The
installed Microsoft tools are available through `skilllens`, `skillopt`, and
`skillopt_sleep`; the Codex-facing SkillOpt skill is installed outside this
repository, at `~/.agents/skills/skillopt-sleep/SKILL.md`.

## External Source Anchors

Official source APIs and crawling discipline:

- SEC EDGAR APIs:
  https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- SEC EDGAR fair access and User-Agent/rate guidance:
  https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data
- FRED API:
  https://fred.stlouisfed.org/docs/api/fred/
- Federal Reserve Data Download Program:
  https://www.federalreserve.gov/datadownload/
- BLS Public Data API:
  https://www.bls.gov/bls/api_features.htm
- BEA API for developers:
  https://www.bea.gov/resources/for-developers
- Treasury Fiscal Data API:
  https://fiscaldata.treasury.gov/api-documentation/
- New York Fed Markets Data API:
  https://markets.newyorkfed.org/static/docs/markets-api.html
- EIA Open Data API:
  https://www.eia.gov/opendata/documentation.php
- Census Data API:
  https://www.census.gov/data/developers/guidance/api-user-guide.html
- robots.txt guidance:
  https://developers.google.com/search/docs/crawling-indexing/robots/intro

Evaluation and backtest discipline:

- White Reality Check:
  https://www.ssc.wisc.edu/~bhansen/718/White2000.pdf
- Probability of Backtest Overfitting:
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253
- NIST AI Risk Management Framework:
  https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf

## Phase 1. Source Registry

Goal:

```text
define what the bot is allowed to read
```

Implemented first-step artifacts:

- `data/source_registry/source_registry.csv`
- `crawler/source_registry.py`
- `tests/test_source_registry.py`

The registry now records both the old compatibility fields and richer source
card fields:

| Field | Meaning |
|---|---|
| `source_registry_id` | stable ID used by documents and retrieval outputs |
| `source_type` | source family for routing and ranking |
| `source_reliability_tier` | official, company, user_preferred, unknown |
| `source_credibility` | single compatibility score |
| `authority_score` | institutional authority of the source |
| `timeliness_score` | expected release speed/freshness |
| `legal_liability_score` | cost of false disclosure |
| `numeric_density_score` | expected structured numeric content |
| `promotion_risk_score` | incentive to frame/market the information |
| `fetch_method` | api, rss, sitemap, html, csv, etc. |
| `update_frequency` | expected refresh frequency |
| `point_in_time_policy` | how `available_at` should be assigned |
| `preferred_endpoints` | safest endpoints to fetch first |
| `documentation_url` | official documentation or policy page |
| `coverage_scope` | what the source is good for |

Acceptance:

- registry validates with `python crawler/source_registry.py --registry data/source_registry/source_registry.csv --validate-only`;
- favorite websites do not automatically increase credibility;
- official APIs are preferred over brittle scraping;
- source cards can enrich normalized documents without breaking old schema.

## Phase 2. Crawler

Goal:

```text
turn source cards into reproducible documents
```

Use three modes:

| Mode | Purpose |
|---|---|
| historical backfill | build training/evaluation corpus |
| incremental refresh | update known official sources |
| live adapter | fetch small fresh official slice during demo/use |

Required crawler behavior:

- obey source card fetch method and robots/access notes;
- declare User-Agent for SEC and respect fair-access rate limits;
- prefer official API/RSS/sitemap endpoints;
- store raw snapshot plus normalized text;
- assign `published_at`, `first_seen_at`, `available_at`, `ingested_at`;
- compute `canonical_url`, `document_hash`, and duplicate cluster ID;
- never let top-level archive pages masquerade as documents.

Implementation anchors:

- `crawler/live_incremental_fetch.py`
- `crawler/company_source_archive_discovery.py`
- `crawler/company_source_adapter_backfill.py`
- `crawler/normalize_documents.py`

## Phase 3. Evidence Ledger

Goal:

```text
store evidence units, not just pages
```

Document units:

- SEC section;
- SEC exhibit;
- company IR release;
- investor presentation/report;
- macro observation;
- official regulator release;
- LLM extracted claim with citation.

Required fields come from `docs/DATA_SCHEMA.md`.

Next implementation:

- use `features/build_evidence_units.py` to export schema-compatible
  `sec_section`, `sec_exhibit`, `company_ir_fact_block`, and
  `macro_observation` rows;
- preserve source card fields in evidence bundle exports;
- store claim-level citations for LLM summaries.

## Phase 4. Indexing

Goal:

```text
make evidence fast to retrieve and easy to audit
```

Use:

- SQLite FTS/BM25 for exact financial text;
- structured fields for ticker/date/source/doc-type filters;
- optional vector side index for semantic expansion;
- entity graph for company-sector-macro links.

Implementation anchors:

- `indexing/build_search_index.py`
- `indexing/build_sparse_index.py`
- `indexing/build_dense_index.py`
- `indexing/entity_linking.py`

## Phase 5. Baseline Retrieval

Goal:

```text
measure a simple baseline before clever ranking
```

Baseline:

```text
BM25 + point-in-time filter + ticker/date/source filters
```

Invariant:

```text
available_at <= retrieval_cutoff <= decision_time
```

Implementation anchors:

- `retrieval/retrieve_for_portfolio.py`
- `retrieval/hybrid_ranker.py`

## Phase 6. Hybrid Ranking

Goal:

```text
rank by decision-grade evidence, not just text similarity
```

Candidate formula:

```text
score =
  bm25
  + ticker_match
  + intent_source_match
  + field_match
  + freshness
  + source_authority
  + evidence_specificity
  + portfolio_relevance
  - duplicate_penalty
  - stale_penalty
  - promotion_risk
```

Route examples:

| Query | Preferred route |
|---|---|
| Apple risk factors | SEC 10-K/10-Q Item 1A |
| Apple earnings | 8-K exhibit or 10-Q financial statements |
| rates pressure | FRED/Fed/Treasury |
| energy shock | EIA plus official company filings |
| portfolio risk | macro + holdings-specific filings |

Implementation anchors:

- `finportfolio_ir/query_intent.py`
- `retrieval/hybrid_ranker.py`
- `evaluation/calibrate_search_reranker.py`

## Phase 7. LLM Layer

Goal:

```text
interpret retrieved evidence without inventing evidence
```

LLM is downstream of retrieval. It should:

- summarize a specific retrieved document/evidence bundle;
- extract facts and numbers;
- identify risks/upside/missing information;
- produce a short analyst-style comment;
- attach citations;
- report confidence.

It should not:

- replace source selection;
- silently use uncited web knowledge;
- hide uncertainty;
- make PPO features without provenance.

Implementation anchors:

- `web_app.py`
- `features/export_fingpt_contexts.py`
- `features/export_evidence_bundles.py`

## Phase 8. Evaluation

Goal:

```text
prove search got better
```

Use qrels:

```text
query_id, doc_id, relevance
```

Metrics:

| Metric | Meaning |
|---|---|
| NDCG@10 | best documents are near top |
| MRR | first useful result appears quickly |
| Precision@10 | top page usefulness |
| source-family accuracy | correct source type for intent |
| duplicate rate | wasted ranking slots |
| freshness error | stale documents outrank fresh evidence |

Implementation anchors:

- `evaluation/build_search_quality_pool.py`
- `evaluation/export_qrels_from_pool.py`
- `evaluation/validate_qrels.py`
- `evaluation/evaluate_ir_metrics.py`
- `evaluation/evaluate_retrieval_diagnostics.py`

## Phase 9. FinGPT Handoff

Goal:

```text
send structured evidence bundles to feature extraction
```

Expected output:

```json
{
  "ticker": "AAPL",
  "as_of_date": "2026-06-15",
  "evidence_bundle_id": "...",
  "source_quality": 0.86,
  "retrieval_confidence": 0.78,
  "documents": ["doc_1", "doc_2"]
}
```

FinGPT then extracts:

- downside risk;
- uncertainty;
- macro stress;
- earnings pressure;
- balance-sheet stress;
- signal confidence;
- evidence specificity;
- numeric evidence density;
- boilerplate intensity.

Implementation anchors:

- `docs/FINGPT_HANDOFF.md`
- `features/build_fingpt_handoff_package.py`
- `features/export_fingpt_contexts.py`
- `features/export_evidence_bundles.py`

## Phase 10. Production Monitoring

Monitor:

- crawl failures;
- source freshness;
- duplicate clusters;
- search latency;
- LLM latency;
- citation coverage;
- source-card coverage;
- qrels metric drift;
- user failed searches;
- downstream feature stability.

## Phase 11. Self-Improvement Loop

HL / Quant Agent may propose retrieval improvements only through a fixed
validation firewall:

```text
hypothesis -> code patch -> retrieval metrics -> feature ablation ->
CHRL validation -> failure memory
```

The agent must not change labels, test split, or evaluator after seeing
performance.

## Current First-Step Status

Done now:

- source registry expanded into source cards v1;
- loader remains backward-compatible with older CSVs;
- source metadata enrichment now preserves rich source-card scores;
- registry validator added;
- implementation plan created.
- hybrid ranker now exports and can weight source-card components:
  `source_authority_score`, `source_timeliness_score`,
  `source_legal_liability_score`, `source_numeric_density_score`,
  `source_promotion_risk_score`, and `source_quality_score`;
- `bm25_only` remains a clean baseline, while hybrid methods use source-card
  boosts/penalties.
- evidence-unit builder added for SEC sections/exhibits, company IR fact
  blocks, and official macro observations.
- evidence-unit retrieval package added:
  `features/build_evidence_unit_retrieval_package.py` builds
  `evidence_units.jsonl`, retrieves over units, writes `run.csv`, and exports
  grouped `evidence_bundles.jsonl` with `parent_doc_id` preserved.

Next immediate implementation task:

```text
compare document-level vs evidence-unit retrieval on qrels, then decide whether
to make evidence units the default search grain for high-value source families
```

Implemented comparison tool:

- `evaluation/compare_document_vs_evidence_units.py`
- output files:
  `comparison_run.csv`, `comparison_metrics_by_method.csv`,
  `comparison_delta_by_method.csv`, `evidence_unit_raw_retrieved_all.jsonl`,
  and `evidence_unit_eval_retrieved_all.jsonl`.

Decision rule:

```text
accept evidence-unit default only if NDCG@10 and MRR improve or stay flat,
Precision@10 does not degrade materially, and raw unit inspection shows cleaner
citations rather than accidental parent-document credit
```

Current development result:

- `evaluation/assistant_label_comparison_pool.py` seeds explicit
  assistant-development qrels for the generated comparison pool.
- On `sec_section + sec_exhibit + official_macro` input, evidence-unit and
  document-level runs are identical after source-document collapse:
  Precision@10 1.000, NDCG@10 0.836, MRR 1.000 for both.
- Interpretation: this corpus is already section/macro-observation level. The
  next evidence-unit experiment should include whole company IR pages or raw
  filings where splitting into fact blocks changes what retrieval can rank.

Company-IR experiment:

- Input: `data/processed_documents/sec_macro_company_ir_ppo_2010_2023_documents.jsonl`.
- Evidence-unit expansion: 26,368 rows -> 39,101 units, including 13,850
  `company_ir_fact_block` units.
- Development qrels:
  `data/annotations/document_vs_evidence_units_company_ir_qrels_assistant_v1.csv`.
- Result: document `full_hybrid` beats evidence-unit `full_hybrid` on NDCG@10
  (0.833 vs 0.778), while Precision@10 and MRR stay 1.000.

Next implementation implication:

```text
do not make evidence-unit retrieval the default yet; add source/type-aware
calibration for evidence-unit mode and require it to recover NDCG@10 on the
mixed SEC/macro/company-IR comparison before promotion
```

Source/type-aware calibration candidate:

- Utility:
  `evaluation/calibrate_evidence_unit_reranker.py`.
- Features: evidence-unit claim type, source/type family, and publication
  freshness.
- Result on assistant-development qrels:
  - document `full_hybrid`: NDCG@10 0.833;
  - raw evidence-unit `full_hybrid`: NDCG@10 0.778;
  - calibrated evidence-unit `full_hybrid`: NDCG@10 0.976.
- Held-out result on
  `data/portfolios/evidence_unit_holdout_query_set_v1.csv` with 45
  assistant-development judgments:
  - document `full_hybrid`: NDCG@10 0.818;
  - raw evidence-unit `full_hybrid`: NDCG@10 0.825;
  - calibrated evidence-unit `full_hybrid`: NDCG@10 1.000.

Promotion rule:

```text
keep calibrated evidence-unit mode behind evaluation scripts until the held-out
pool is manually reviewed or the same source/type weights pass a larger
human-labeled qrels file
```

Implementation implication:

```text
evidence-unit retrieval is now technically viable, but not yet the live default;
the next step is a small manual review of the held-out comparison pool, then a
guarded live flag for high-value intents such as Item 1A risk, earnings
guidance, legal proceedings, and macro observations
```

Guarded live flag status:

- `retrieval/evidence_unit_gate.py` defines the first deterministic guard.
- `web_app.py` exposes `evidence_unit_gate` in the search payload next to
  `query_intent`.
- `web_app.py` now also uses the guard as a real live search switch:
  high-specificity intents search over calibrated evidence units; broad
  overview queries keep the document/index path.
- The feature flag is `FINPORTFOLIO_EVIDENCE_UNIT_SEARCH`. It defaults to on
  but remains constrained by the deterministic guard; set it to `0` to force
  document-level retrieval.
- The live response reports `evidence_unit_gate.active`,
  `active_result_count`, and `feature_flag_enabled`.
- Offline calibration and live search share
  `retrieval/evidence_unit_calibration.py`, preventing drift between evaluation
  metrics and demo ranking behavior.
- Enabled examples:
  - `Apple risk factors in the latest 10-K` ->
    `sec_section:risk_factors`;
  - `JPM credit risk and legal proceedings` ->
    `sec_section:risk_factors` / `sec_section:legal_proceedings`;
  - `Fed rates and credit spreads` -> `macro_observation`;
  - `Apple earnings guidance` -> `sec_exhibit:earnings_or_guidance`.
- Disabled examples:
  - broad company overview;
  - product history;
  - favorite/external-source browsing where page-level context is safer.

Manual held-out review queue:

- `evaluation/build_evidence_unit_review_queue.py`
- `data/annotations/evidence_unit_holdout_review_queue_v1.csv`
- `data/annotations/evidence_unit_holdout_review_queue_v1.md`

This queue is the next reviewer-facing artifact. It shows document rank, raw
unit rank, calibrated unit rank, current label provenance, and calibration
tags. After manual labels are added, rerun the same held-out metrics before
enabling the guard as a real retrieval switch.

Live smoke status:

| Query | Expected behavior | Observed |
|---|---|---|
| `Apple risk factors in the latest 10-K` | calibrated Item 1A evidence units | active, top rows are AAPL `risk_factors`, ~7.3s |
| `Apple company overview and product history` | document-level broad search | inactive, `search_grain=document`, ~4.8s |
| `Fed rates and credit spreads` | calibrated macro observations | active, top rows are `macro_observation`, ~3.6s |

The macro path uses a query-selective source window before evidence-unit
construction. This keeps the demo path stable without weakening the offline
evaluation rule: broad evidence-unit retrieval is still rejected; only guarded,
intent-specific unit search is live.

Assistant-reviewed validation status:

- `evaluation/assistant_review_evidence_unit_queue.py` fills
  `suggested_human_relevance`; it does not create human labels.
- `evaluation/export_qrels_from_evidence_unit_review_queue.py` exports qrels
  with `label_source=assistant_evidence_unit_review_v1`.
- Reviewed held-out label counts: 1=20, 2=15, 3=10.
- Metrics on these stricter assistant-reviewed qrels:
  - document `full_hybrid`: NDCG@10 0.843;
  - raw evidence-unit `full_hybrid`: NDCG@10 0.813;
  - calibrated evidence-unit `full_hybrid`: NDCG@10 1.000.

Updated decision:

```text
raw evidence-unit retrieval is not accepted as a default; calibrated
evidence-unit retrieval can move to a guarded feature flag after independent
human review confirms the held-out improvement
```

Human spot-check packet:

- `evaluation/build_evidence_unit_human_spotcheck.py` creates a compact
  reviewer packet from the assistant-reviewed evidence-unit queue.
- Generated artifacts:
  - `data/annotations/evidence_unit_holdout_human_spotcheck_v1.csv`;
  - `data/annotations/evidence_unit_holdout_human_spotcheck_v1.md`;
  - `data/annotations/evidence_unit_holdout_human_spotcheck_v1_summary.csv`.
- The packet contains 15 rows, capped at four rows per held-out query. It
  prioritizes calibrated top results, rank disagreements, calibration
  promotions, borderline labels, and assistant label changes.
- These rows are intentionally blank in `human_relevance`. They are not counted
  as human labels until a reviewer fills 0/1/2/3 labels.

Reviewer command:

```powershell
python evaluation/build_evidence_unit_human_spotcheck.py `
  --input data/annotations/evidence_unit_holdout_review_queue_v1_assistant_reviewed.csv `
  --output data/annotations/evidence_unit_holdout_human_spotcheck_v1.csv `
  --summary-output data/annotations/evidence_unit_holdout_human_spotcheck_v1_summary.csv `
  --prompt-output data/annotations/evidence_unit_holdout_human_spotcheck_v1.md `
  --limit 15 `
  --max-per-query 4
```

After the 15 labels are filled, apply them without losing provenance:

```powershell
python evaluation/apply_evidence_unit_human_spotcheck.py `
  --review-queue data/annotations/evidence_unit_holdout_review_queue_v1_assistant_reviewed.csv `
  --spotcheck data/annotations/evidence_unit_holdout_human_spotcheck_v1.csv `
  --output-queue data/annotations/evidence_unit_holdout_review_queue_v2_mixed.csv `
  --qrels-output data/annotations/document_vs_evidence_units_company_ir_holdout_qrels_mixed_v1.csv `
  --issues-output data/annotations/document_vs_evidence_units_company_ir_holdout_qrels_mixed_v1_issues.csv `
  --strict
```

The resulting qrels will mix `human_evidence_unit_spotcheck_v1` rows with
`assistant_evidence_unit_review_v1` fallback rows. Report them as mixed qrels,
not as a fully human-labeled benchmark.

Three-step continuation gate:

1. Fill the compact spot-check file from a chat-style label string:

```powershell
python evaluation/fill_evidence_unit_spotcheck_labels.py `
  --input data/annotations/evidence_unit_holdout_human_spotcheck_v1.csv `
  --labels "1=3, 2=1, 3=0" `
  --output data/annotations/evidence_unit_holdout_human_spotcheck_v1.csv `
  --reviewer-notes "human chat review" `
  --strict
```

2. Apply the spot-check to the full held-out queue and export mixed qrels:

```powershell
python evaluation/apply_evidence_unit_human_spotcheck.py `
  --review-queue data/annotations/evidence_unit_holdout_review_queue_v1_assistant_reviewed.csv `
  --spotcheck data/annotations/evidence_unit_holdout_human_spotcheck_v1.csv `
  --output-queue data/annotations/evidence_unit_holdout_review_queue_v2_mixed.csv `
  --qrels-output data/annotations/document_vs_evidence_units_company_ir_holdout_qrels_mixed_v1.csv `
  --issues-output data/annotations/document_vs_evidence_units_company_ir_holdout_qrels_mixed_v1_issues.csv `
  --strict
```

3. Run the promotion gate:

```powershell
python evaluation/evaluate_evidence_unit_promotion_gate.py `
  --qrels data/annotations/document_vs_evidence_units_company_ir_holdout_qrels_mixed_v1.csv `
  --comparison-run data/exports/document_vs_evidence_units_company_ir_holdout_v1/comparison_run.csv `
  --calibrated-run data/exports/document_vs_evidence_units_company_ir_holdout_v1/calibrated_source_type_assistant_reviewed_v1/evidence_unit_calibrated_run.csv `
  --output-dir data/exports/document_vs_evidence_units_company_ir_holdout_v1/promotion_gate_mixed_v1 `
  --prefix evidence_unit_promotion_mixed_v1
```

Dry-run status without human labels:

- `data/annotations/document_vs_evidence_units_company_ir_holdout_qrels_mixed_dryrun_v1.csv`
  contains 45 assistant fallback rows and 0 human rows.
- Promotion-gate output:
  `data/exports/document_vs_evidence_units_company_ir_holdout_v1/promotion_gate_mixed_dryrun_v1/evidence_unit_promotion_mixed_dryrun_v1_acceptance.csv`.
- Result: `block_promotion`, reason `pending_human_spotcheck_labels`.
- Metrics still show the calibrated reranker is promising:
  document NDCG@10 0.843 -> calibrated NDCG@10 1.000, delta +0.157, with no
  Precision@10 drop. This is not enough for final promotion until at least 10
  human spot-check labels are present.

Human-confirmed mixed-qrels status:

- Human spot-check labels were applied to
  `data/annotations/evidence_unit_holdout_human_spotcheck_v1.csv`.
- Mixed qrels:
  `data/annotations/document_vs_evidence_units_company_ir_holdout_qrels_mixed_v1.csv`.
- Label provenance: 15 `human_evidence_unit_spotcheck_v1` rows and 30
  `assistant_evidence_unit_review_v1` fallback rows.
- Promotion-gate output:
  `data/exports/document_vs_evidence_units_company_ir_holdout_v1/promotion_gate_mixed_v1/evidence_unit_promotion_mixed_v1_acceptance.csv`.
- Decision: `accept_guarded_promotion`.
- Reason: `all_thresholds_passed`.

Human-confirmed mixed-qrels metrics:

| Run | Precision@10 | NDCG@10 | MRR |
| --- | ---: | ---: | ---: |
| document `full_hybrid` | 0.950 | 0.824 | 1.000 |
| raw evidence-unit `full_hybrid` | 0.950 | 0.823 | 1.000 |
| calibrated evidence-unit `full_hybrid` | 0.950 | 0.982 | 1.000 |

Interpretation:

```text
the evidence-unit idea is not accepted in raw form, but the calibrated,
intent-guarded evidence-unit path passes the current mixed-qrels promotion gate:
NDCG@10 improves by +0.158 with no Precision@10 drop and complete top-10 qrels
coverage
```

Live-code promotion contract:

- `retrieval/evidence_unit_promotion.py` loads the accepted promotion-gate CSV.
- `retrieval/evidence_unit_gate.py` now requires this accepted status before
  enabling evidence-unit search for specific intents.
- If the acceptance artifact is missing or says `block_promotion`, the gate
  returns `mode=document` and adds `promotion_gate_not_accepted`.
- The search API payload now exposes promotion provenance through
  `evidence_unit_gate.promotion_status`,
  `evidence_unit_gate.promotion_reason`, and
  `evidence_unit_gate.promotion_artifact`.

Current methodology stage:

```text
Stage 4 -> accepted guarded deployment contract.

Completed:
1. source/crawler foundation;
2. document-level and evidence-unit retrieval comparison;
3. assistant-reviewed and user-confirmed mixed qrels;
4. promotion gate accepted;
5. live gate tied to the accepted promotion artifact.

Next:
build a regression/smoke suite for representative live queries and monitor
latency, active evidence-unit count, and promotion status on every demo/server
run.
```

Live search smoke regression:

- `evaluation/run_live_search_smoke.py` checks representative live search
  behavior through either in-process service calls or a running HTTP server.
- The smoke checks:
  - promotion status from the accepted artifact;
  - whether the evidence-unit gate is enabled/active;
  - active evidence-unit count;
  - top leaf result grain, unit type, and claim type;
  - latency per query.
- The Cloudflare demo launcher supports `-RunSmoke` to block tunnel startup if
  live search regressions are detected.

Default command:

```powershell
python evaluation/run_live_search_smoke.py `
  --output-dir data/exports/live_search_smoke_v1 `
  --strict
```

HTTP preflight command:

```powershell
python evaluation/run_live_search_smoke.py `
  --base-url http://127.0.0.1:8780 `
  --output-dir data/exports/live_search_smoke_cloudflare_preflight `
  --strict
```

Current baseline:

| Case | Query | Expected | Result |
| --- | --- | --- | --- |
| `sec_risk_factors` | `Apple risk factors in the latest 10-K` | evidence-unit `risk_factors` | passed |
| `broad_company_overview` | `Apple company overview and product history` | document grain | passed |
| `macro_rates_credit` | `Fed rates and credit spreads` | evidence-unit macro observation | passed |
| `sec_earnings_guidance` | `Apple 8-K earnings guidance` | evidence-unit SEC exhibit | passed |

Smoke result:

- output:
  `data/exports/live_search_smoke_v1/live_search_smoke_summary.csv`;
- status: `passed`;
- cases: 4/4 passed;
- max latency: 7402.4 ms;
- mean latency: 2905.8 ms;
- promotion status: `accepted` for all four cases.
