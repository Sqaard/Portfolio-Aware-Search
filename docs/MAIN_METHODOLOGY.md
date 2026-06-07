# FinPortfolio IR Main Methodology

Status date: 2026-05-15.

This is the main methodology file for the project. It is intended to remain the
project-level source of truth until the end of the work. More specific files can
describe implementation details, experiments, prompts, schemas, or UI plans,
but methodological decisions should be reconciled with this document.

## 1. Mission

FinPortfolio IR is not an LLM trader and not a generic search engine. It is a
causal, source-aware financial information retrieval system for US equities and
Dow 30 portfolio experiments.

The target pipeline is:

```text
trusted source discovery
    -> point-in-time document normalization
    -> portfolio/ticker/macro-aware causal retrieval
    -> evidence bundles with provenance
    -> Codex-rule / FinGPT / Mistral structured text features
    -> low-dimensional PPO feature sets
    -> controlled out-of-sample PPO ablations
```

The core scientific hypothesis is:

```text
PPO with causal, source-aware financial text features should outperform PPO
with numerical/base-macro features only on out-of-sample Dow 30 backtesting.
```

The purpose of FinPortfolio IR is therefore not to predict returns directly. It
must build a defensible evidence layer that can be passed to a feature engine
and later tested inside PPO under strict leakage controls.

## 2. Hard Invariants

These rules are non-negotiable.

### 2.1 Point-In-Time Safety

No document may enter retrieval, feature extraction, or PPO input if any of the
following conditions is true:

```text
available_at > retrieval_cutoff
available_at > decision_time
retrieval_cutoff > decision_time
available_at < published_at
```

The critical timestamp is `available_at`. `published_at` is informative, but it
is not enough to protect a backtest.

### 2.2 Future Returns Are Diagnostic Only

Event-study feedback uses realized future returns. It is allowed only for:

- extractor diagnostics;
- prompt improvement;
- source-quality diagnostics;
- human-review prioritization;
- offline methodology reports.

It must never be merged into any PPO input panel.

### 2.3 Source Quality Is Not User Preference

Favorite websites and manually preferred sources can affect UI display or local
ranking priority. They must not increase:

- `source_credibility`;
- `source_reliability_tier`;
- source-quality score;
- PPO eligibility.

### 2.4 Source Quality Is Not Extraction Quality

A reliable source can produce low-value text for a specific task. A noisy source
can occasionally produce a useful document. The methodology therefore keeps
these concepts separate:

- source reliability;
- URL/document integrity;
- timestamp reliability;
- retrieval usefulness;
- extraction correctness;
- event-study diagnostic behavior;
- human qrels relevance.

### 2.5 BM25 Remains Mandatory

BM25 is the mandatory retrieval baseline. Dense retrieval, ColBERT-style
late-interaction retrieval, RRF, LLM query rewriting, and learned reranking are
allowed only as additional channels after judged qrels exist. They should not
replace the baseline without evidence.

### 2.6 Auditability Comes Before Model Complexity

Every derived feature must preserve enough provenance to answer:

```text
which document, from which source, available when, selected why, transformed how
```

Required provenance fields include:

- `doc_id`;
- `document_hash`;
- `duplicate_cluster_id`;
- `source`;
- `source_type`;
- `source_registry_id`;
- `source_reliability_tier`;
- `url`;
- `published_at`;
- `first_seen_at`;
- `available_at`;
- `retrieval_cutoff`;
- `decision_time`;
- `matched_tickers`;
- `matched_holdings`;
- `query_intent_primary`;
- `retrieval_reason_tags`;
- `component_scores`;
- `document_split`.

## 3. Why This Methodology

The methodology is built around the weaknesses of financial backtesting and
financial text:

1. Financial text is highly biased, duplicated, delayed, revised, and
   source-dependent.
2. PPO backtests are extremely vulnerable to future information leakage.
3. A generic search engine can find relevant-looking documents that are not
   point-in-time safe.
4. An LLM can produce plausible labels that are not causally usable.
5. Trading performance alone cannot validate IR quality because it mixes
   retrieval, extraction, market noise, PPO instability, and regime luck.

Therefore the project separates the system into auditable layers:

```text
source discipline
    -> point-in-time normalization
    -> causal retrieval
    -> extraction
    -> extraction evaluation
    -> PPO ablation
```

This fits the AI4Finance/CERB-style direction: text is not a trading oracle. It
is transformed into controlled evidence atoms and low-dimensional text features
that PPO can use or ignore.

## 4. Project Architecture

### 4.1 Source Discipline Layer

This layer defines what sources exist and whether they are eligible for
backtests.

It owns:

- source registry;
- source family/type normalization;
- access method;
- robots/compliance notes;
- content-license notes;
- URL health checks;
- fetch status;
- source-level coverage diagnostics;
- source exclusion/quarantine logic.

Preferred source families:

- official macro data: FRED/Fed, BLS, BEA, Treasury, EIA, Census;
- SEC EDGAR: 10-K, 10-Q, 8-K, exhibits, EX-99.1 earnings materials;
- official company IR: earnings releases, press releases, investor
  presentations, reports, RSS/API feeds;
- structured official facts: company facts/XBRL where feasible;
- carefully labeled market/news sources only after quality gates.

Not v1 PPO sources:

- social media;
- Google Trends-like behavioral proxies;
- satellite/alternative imagery;
- user favorite websites without source validation.

These can be research tracks later, but they should not enter PPO until
timestamp, licensing, coverage, and quality are defensible.

### 4.2 Point-In-Time Normalization Layer

This layer converts heterogeneous inputs into one document schema.

Every row should describe a concrete retrievable unit, not just a website. A
unit can be:

- SEC section;
- SEC exhibit;
- official macro observation;
- company IR release;
- company report;
- dated archive item.

The normalized row must include:

```text
doc_id, source, source_type, source_registry_id, title, body, url,
canonical_url, published_at, first_seen_at, available_at, ingested_at,
version_id, duplicate_cluster_id, matched_tickers, matched_holdings,
event_tags, risk_terms, source_credibility, document_hash
```

### 4.3 Causal Retrieval Layer

This is FinIR proper. For every decision time it answers:

```text
what documents were actually available before this decision, relevant to the
portfolio/ticker/macro regime, and safe to pass downstream as evidence?
```

Current retrieval logic:

```text
for each decision_date:
    build portfolio-level query
    build ticker-level queries
    filter documents by available_at <= decision_time
    score candidates
    diversify by duplicate cluster and holdings
    export retrieved contexts/evidence bundles
```

The retrieval score can combine:

- BM25 score;
- entity match score;
- portfolio weight score;
- ticker specificity score;
- source reliability score;
- freshness score;
- event severity score;
- risk-term score;
- macro-regime relevance score;
- duplicate penalty;
- diversity penalty.

### 4.4 Route-Aware Retrieval Layer

The next retrieval upgrade is route-aware candidate selection.

The deterministic query-intent router should not only write metadata. It should
select candidate channels.

Core routes:

- `official_macro`;
- `sec_filings`;
- `sec_filing_section`;
- `sec_filing_exhibit`;
- `company_ir`;
- `structured_facts`;
- `market_news`;
- `favorite_websites`;
- `external_web`;
- `local_corpus`.

Immediate implemented/available routes should be:

- official macro;
- SEC sections/exhibits;
- company IR.

Portfolio-level retrieval should prioritize macro, credit, volatility, rates,
and broad market evidence.

Stock-level retrieval should prioritize ticker-specific SEC/company IR evidence
and should not pull generic macro rows unless explicitly linked to the holding,
sector, or event.

Every route-aware decision bundle should report:

- `selected_routes`;
- `candidate_count_by_route`;
- `final_topk_count_by_route`;
- `empty_route_warnings`;
- `fallback_route_used`;
- `source_family_slots`.

### 4.5 Evidence Bundle Layer

FinIR should export evidence, not raw search results.

Evidence bundle types:

- stock-level evidence;
- sector-level evidence;
- market/portfolio-level evidence;
- source-family-specific slots such as SEC, company IR, macro, future news.

Reason:

If company IR, SEC sections, SEC exhibits, and macro documents all compete for
one single slot, useful source families can disappear from the feature panel.
Separate route/source slots make the feature set more interpretable and less
sparse.

### 4.6 Structured Feature Extraction Layer

The extraction layer converts retrieved evidence into structured features. It
must not make trading decisions.

Current extractors:

- Codex-rule deterministic baseline;
- macro-rule engine;
- Mistral comparison runner;
- FinGPT handoff pipeline.

Target features include:

- impact direction;
- risk intensity;
- uncertainty intensity;
- sentiment proxy;
- opportunity intensity;
- forward-looking intensity;
- portfolio action relevance;
- event/signal flags;
- source/provenance features;
- text quality controls.

The extracted features must remain low-dimensional enough for PPO.

### 4.7 PPO Ablation Layer

The PPO layer should compare controlled feature sets.

Recommended experiment ladder:

| Code | Meaning |
|---|---|
| E0 | numerical/technical baseline |
| E1 | base macro only |
| E2 | base macro + Codex-rule deterministic text features |
| E3 | base macro + source-quality-gated Codex-rule features |
| E4 | base macro + Mistral v1 features |
| E5 | base macro + corrected Mistral prompt features |
| E6 | base macro + source-family-slot features |
| E7 | base macro + text-quality gated features |
| E8 | final selected causal text feature set |

The central scientific comparison remains:

```text
base_macro vs base_macro + causal text
```

## 5. Current Implemented State

### 5.1 Validated Daily Retrieval Package

Current validated full daily package:

```text
data/exports/daily_retrieval_ppo_full_dis_legacy/retrieved_contexts.jsonl
```

Known validated properties:

| Metric | Value |
|---|---:|
| Contexts | 28,493 |
| Unique documents | 2,141 |
| Portfolio contexts | 16,550 |
| Stock contexts | 11,943 |
| Official macro contexts | 16,550 |
| SEC section contexts | 8,733 |
| SEC exhibit contexts | 3,210 |
| Unique decision dates | 3,310 |
| Unique stock tickers | 29 |
| Train contexts | 25,447 |
| Test contexts | 3,046 |
| Strict leakage rows | 0 |
| Hard validation issues | 0 |

Current merge-ready panel:

```text
data/exports/daily_retrieval_ppo_full_dis_legacy/rl_panel_codex_rule_text_features.csv
```

Current PPO ablation package:

```text
data/exports/daily_retrieval_ppo_full_dis_legacy/ppo_ablation_package/
```

### 5.2 Trusted Source/Data Package

Current trusted source package:

```text
data/exports/trusted_source_data_package_2026_05_14.zip
```

Current counts:

| Bucket | Count |
|---|---:|
| Trusted domains | 32 |
| Trusted source labels | 38 |
| Quality/review document units | 20,005 |
| Official macro observations | 18,240 |
| SEC filing exhibits | 655 |
| Company IR core documents | 844 |
| Company IR review documents | 266 |
| Excluded/not-core official or noisy units | 6,363 |

### 5.3 Current Extraction Baselines

The current deterministic `codex_rule_text_features` layer is a reproducible
teacher baseline, not final truth.

It is useful because:

- it runs cheaply over the full daily retrieval package;
- it creates a merge-ready PPO text feature panel;
- it provides a stable reference for Mistral/FinGPT comparison;
- it exposes failures that are hidden in pure LLM outputs.

The macro-rule engine is a separate rule layer for official macro observations.
Official macro should not be labeled by generic prose sentiment logic.

### 5.4 Mistral vs Codex Finding

Current finding:

```text
Codex-rule extraction aligns better with +3d/+10d/+21d realized reaction.
Mistral is sometimes more competitive on event-day/+1d reaction.
Neither is final ground truth.
```

This result means:

- Codex-rule labels currently look better for medium-horizon portfolio
  interpretation;
- Mistral may capture short-term event tone but is not yet reliable enough for
  final extraction;
- prompt design and source-specific extraction should be improved before large
  Mistral runs;
- human adjudication remains necessary.

## 6. Evaluation Methodology

The project uses several evaluation layers because no single metric is enough.

### 6.1 Retrieval Evaluation

Retrieval should be evaluated with judged qrels, not only with trading results.

Graded relevance scale:

| Label | Meaning |
|---:|---|
| 0 | irrelevant |
| 1 | mentions holding/context but not decision-useful |
| 2 | useful evidence |
| 3 | highly relevant and timely for the portfolio decision |

Primary metric:

- `nDCG@10`.

Secondary metrics:

- `Precision@5`;
- `Recall@20`;
- `MAP`;
- `MRR`;
- `CausalValidity@K`;
- `DuplicateRate@K`;
- `PortfolioCoverage@K`;
- route coverage;
- source-family coverage.

Candidate retrieval systems to compare:

- Boolean ticker/company baseline;
- BM25;
- current hybrid diversified ranker;
- route-aware hybrid;
- future dense/RRF hybrid;
- future learned reranker after qrels.

### 6.2 Extraction Evaluation

Extraction quality is evaluated by:

- Codex-rule vs Mistral comparison;
- source-stratified disagreement analysis;
- human adjudication;
- event-study feedback;
- later PPO feature contribution.

The correct hierarchy is:

```text
human adjudication > schema correctness > event-study diagnostics > rule/LLM disagreement
```

Event-study feedback is useful but not ground truth.

### 6.3 Event-Study Feedback

The event-study layer connects:

```text
retrieved document
    -> extracted label
    -> event date
    -> realized reaction
```

Current implementation:

```text
features/build_event_study_feedback.py
```

Current windows:

- pre-10d;
- pre-5d;
- event day;
- +1d;
- +3d;
- +5d;
- +10d;
- +21d.

Current first-pass abnormal return:

```text
ticker_return - equal_weight_dow_panel_return
```

Required next improvements:

- sector-neutral returns;
- SPY/DIA benchmark option;
- before-open / after-close policy;
- duplicate-event collapse;
- volume reaction;
- volatility or absolute-return surprise;
- confidence intervals by source type/family;
- export event-study columns into human adjudication files.

### 6.4 Source-Quality Evaluation

Source quality score v1 should be transparent and componentized.

Required components:

- `provenance_integrity_score`;
- `timestamp_reliability_score`;
- `dated_detail_url_quality_score`;
- `body_length_structure_score`;
- `duplicate_penalty_score`;
- `extraction_readiness_score`;
- `human_adjudication_score`;
- `event_study_diagnostic_score`;
- `coverage_by_ticker_date_regime_score`.

The score should be reported by:

- source family;
- source type;
- source;
- source registry id;
- ticker;
- train/test split;
- regime.

Quarantine rule:

```text
No source can be PPO-eligible if timestamp reliability fails.
```

## 7. Human Adjudication And Qrels

The project must move from preference-only labels to validated human labels.

Current state:

- a balanced top-50 disagreement sample exists;
- rows 1-5 and 7-25 were preference-labeled in favor of Codex;
- row 6 requires original document review;
- this is not yet a final gold set.

Human adjudication v1 should store:

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

Bootstrap labels must stay marked as development-only. Human qrels should be
stored separately, for example:

```text
data/annotations/human_qrels_v1.csv
```

## 8. Main Project Roadmap

### Stage A: Source Discipline And PIT Corpus

Goal:

```text
build a trusted, timestamp-safe, auditable document universe
```

Status: implemented for SEC, official macro, and a first company IR layer.

Next:

- improve company IR coverage through official APIs/RSS/static/PDF adapters;
- add official macro press-release detail pages where timestamps are known;
- keep social/media/alternative data out of PPO until validated.

### Stage B: Daily PPO-Aligned Retrieval

Goal:

```text
build dense daily evidence coverage for the PPO base panel
```

Status: implemented with 28,493 contexts and 0 strict leakage rows.

Next:

- make retrieval route-aware;
- add source-family slots;
- improve diagnostics by route and source family;
- preserve all current schema/provenance fields.

### Stage C: Extraction Tournament

Goal:

```text
find which extractor and prompt produce the most reliable structured features
```

Status: Codex-rule baseline and Mistral comparison exist.

Next:

- improve Mistral prompt using observed failures;
- split prompts by source family;
- compare Codex-rule, Mistral v1, Mistral v2, and FinGPT extraction;
- validate against human adjudication and event-study diagnostics.

### Stage D: Source-Quality Score

Goal:

```text
decide which sources are eligible for PPO features
```

Status: proxy source-quality audit exists.

Next:

- implement source-quality score v1;
- separate source reliability, extraction quality, and user preference;
- quarantine timestamp-unsafe or low-integrity sources;
- report source quality by family/type/source/ticker/regime.

### Stage E: Human Qrels And Retrieval Metrics

Goal:

```text
evaluate IR quality as IR, not only through PPO performance
```

Status: annotation tooling exists; preference-only labels started.

Next:

- create `human_qrels_v1.csv`;
- validate qrels strictly;
- evaluate BM25, hybrid, and route-aware retrieval with `nDCG@10` and `P@5`;
- use trading results only after IR quality is defensible.

### Stage F: PPO Ablation

Goal:

```text
test whether causal text features improve OOS PPO performance
```

Status: merge-ready Codex-rule text panel exists.

Next:

- run E0/E1/E2 first;
- add quality-gated and Mistral features only after extraction QA;
- compare OOS metrics and regime behavior;
- report where text helps, hurts, or does nothing.

### Stage G: UI And Research Product

Goal:

```text
make the system usable as an English financial discovery dashboard
```

Status: local UI slice exists.

Next:

- keep UI separate from credibility scoring;
- show macro dashboard, portfolio summary, source/favorite controls, My Vibe;
- preserve privacy: no API key persistence by default, no hidden portfolio
  sharing, full post text hidden from UI.

## 9. Immediate Engineering Sprint

The next sprint has four tickets.

### Ticket 1: Stabilize Event-Study Feedback

Priority: highest.

Deliver:

- sector-neutral abnormal returns;
- SPY/DIA benchmark option with fallback;
- before-open / after-close event policy;
- duplicate-event collapsing by `duplicate_cluster_id`, `document_hash`,
  ticker, and event date;
- volume reaction metrics;
- volatility or absolute-return surprise metrics;
- confidence intervals by source type/family;
- event-study columns in human adjudication exports.

Acceptance:

- backward-compatible CLI;
- no event-study columns written to PPO panels;
- diagnostic-only labels clearly marked;
- tests for duplicate collapse, benchmark fallback, and event-date policy.

### Ticket 2: Make Retrieval Route-Aware

Deliver:

- use the deterministic query-intent router to choose candidate channels;
- implement available routes: official macro, SEC, company IR;
- separate portfolio and stock routing;
- write route diagnostics.

Acceptance:

- current retrieval still works with route-aware mode disabled;
- route-aware output is deterministic;
- required schema fields remain intact.

### Ticket 3: Source-Quality Score v1

Deliver:

- componentized source-quality report;
- quarantine flags;
- PPO-eligibility flags;
- separation of favorites from credibility.

Acceptance:

- timestamp failures block PPO eligibility;
- report is deterministic and reproducible;
- quality is explainable through components, not just one score.

### Ticket 4: Human Adjudication And Qrels Upgrade

Deliver:

- adjudication export with correct labels and event-study columns;
- separate `human_qrels_v1.csv`;
- strict qrels validation;
- retrieval evaluation against human qrels.

Acceptance:

- bootstrap labels remain development-only;
- BM25, hybrid, route-aware hybrid are evaluated against human labels.

## 10. Explicit Out Of Scope

Do not implement the following unless the methodology is explicitly revised:

- LLM direct trading decisions;
- hidden future-return features;
- event-study columns inside PPO input panels;
- dense-only retrieval as default;
- learned reranking before human qrels;
- social media or trend data as PPO features;
- anti-bot scraping or source-term violations;
- new feature columns without provenance and validation;
- source credibility boosts from user favorites.

## 11. Validation Commands

Baseline local tests:

```powershell
python -B -m unittest discover -s tests
```

Event-study unit tests:

```powershell
python -B -m unittest tests.test_event_study_feedback
```

FinGPT handoff validation:

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

## 12. Relationship To Other Documents

This file is the main methodology. Other files are supporting documents:

- `README.md`: compact docs index and hygiene rules;
- `IMPLEMENTATION_PLAN.md`: active engineering sprint, acceptance criteria, and
  validation commands;
- `CURRENT_ARTIFACTS_AND_EXPERIMENTS.md`: current datasets, exports, experiment
  results, and known caveats;
- `THEORY_AND_BENCHMARKS.md`: theoretical foundation, baseline decisions,
  benchmark positioning, and adopted research ideas;
- `DATA_SCHEMA.md`: field-level schema contract;
- `FINGPT_HANDOFF.md`: downstream handoff contract;
- `ANNOTATION_GUIDE.md`: human/qrels labeling rules.

If a future implementation decision conflicts with this file, update this file
first and explain why the methodology changed.

## 13. Final Operating Principle

The final project should be judged by this chain:

```text
trusted causal evidence
    -> validated retrieval quality
    -> validated extraction quality
    -> source-quality gating
    -> low-dimensional PPO features
    -> controlled OOS ablation
```

The system succeeds if it can show that text features improve PPO in a way that
is point-in-time safe, source-auditable, reproducible, and explainable. If text
does not improve PPO, the system should still be scientifically useful by
showing which source families, extraction methods, and market regimes failed.
