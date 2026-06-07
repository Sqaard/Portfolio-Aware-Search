# Theory And Benchmarks

This file is the compact theoretical foundation for FinPortfolio IR. It keeps
the research logic visible without preserving long historical review notes.

## Core Theoretical Position

The project is a causal financial information retrieval system for PPO feature
generation. It is not:

- an LLM trader;
- a generic search engine;
- a scrape-everything news dashboard;
- a sentiment-only feature pipeline.

The central modeling idea is:

```text
trusted, point-in-time evidence
    -> structured text features
    -> low-dimensional PPO inputs
    -> controlled OOS ablation
```

This follows the strongest practical lesson from financial NLP systems:
financial text is useful only when source identity, timestamps, provenance,
duplicates, and downstream task boundaries are controlled.

## Why Source Discipline Comes First

Financial text has several failure modes:

- future leakage through publication/availability timestamp mistakes;
- duplicate or syndicated text dominating retrieval;
- source bias and promotional company language;
- unstable URLs and changing web archives;
- generic boilerplate in SEC filings;
- macro data revisions and release lags;
- LLM overconfidence on ambiguous evidence.

Therefore source handling is a first-class research layer, not preprocessing.
Every source needs:

- identity and source family;
- access method;
- timestamp policy;
- reliability tier;
- robots/compliance note;
- license/storage note;
- coverage diagnostics;
- fetch/retry behavior;
- point-in-time eligibility.

## Retrieval Method Positioning

BM25 remains the mandatory baseline because it is transparent, reproducible,
and hard to beat without judged labels.

Current strongest local method:

```text
full_hybrid_diversified =
    BM25
    + entity/ticker match
    + portfolio exposure
    + recency/freshness
    + event/risk cues
    + source reliability
    + duplicate and diversification controls
```

Target method:

```text
route-aware hybrid retrieval =
    deterministic query-intent routing
    + source-family candidate slots
    + BM25 baseline
    + optional dense/ColBERT/RRF recall channel after qrels
    + transparent reranking
```

Dense retrieval and learned reranking are deferred until human qrels exist.

## Query-Intent Theory

Finance queries are not homogeneous. A question about a Fed rate signal, an
8-K exhibit, a company risk factor, and a favorite website post should not
retrieve from the same route by default.

The deterministic router should classify:

- filing search;
- filing fact lookup;
- structured numeric lookup;
- macro regime lookup;
- portfolio impact;
- news/sentiment lookup;
- favorite source lookup;
- company event search;
- general financial search.

The router should then choose routes:

- official macro;
- SEC filings/sections/exhibits;
- company IR;
- structured facts;
- market news;
- favorite websites;
- external web;
- local corpus.

The important point is not just metadata. Routing should control candidate
allocation and diagnostics.

## Financial LLM Positioning

LLMs are feature extractors, not trading agents.

Allowed LLM tasks:

- classify event/signal type;
- summarize portfolio impact;
- extract risk, uncertainty, sentiment, forward-looking statements;
- explain affected holdings;
- produce structured JSON under schema constraints;
- support human adjudication.

Disallowed LLM tasks:

- direct trading decisions;
- hidden portfolio rebalancing;
- unconstrained raw text features in PPO;
- feature generation without provenance;
- bypassing retrieval causality.

Current extractor hierarchy:

```text
human adjudication
    > schema-valid LLM extraction
    > event-study diagnostics
    > Codex-rule / Mistral disagreement
```

Codex-rule is a deterministic teacher baseline, not truth. Mistral/FinGPT must
beat it on validated labels before scaling.

## Benchmark Positioning

Primary external benchmark target:

- FinDER: closest to realistic financial evidence retrieval for financial QA.

Secondary benchmark references:

- FinanceBench: open-book financial QA over real documents;
- SEC-QA / DocFinQA / FinTextQA: long financial context and filings QA;
- FinAgentBench: agentic retrieval and tool use;
- FiQA / FinQA / TAT-QA: sentiment, numerical reasoning, and hybrid text/table
  reasoning;
- FinBen / PIXIU-style suites: broad financial LLM capability checks.

Internal benchmarks matter more for the final claim:

- judged qrels with `nDCG@10`;
- source-quality score;
- human extraction adjudication;
- event-study feedback;
- PPO OOS ablation.

## Papers And Ideas Adopted

Text/sentiment foundations:

- Tetlock: media tone can affect or reflect investor sentiment.
- Loughran-McDonald: generic dictionaries fail on financial language.
- Bollen / Preis: alternative textual/behavioral signals can correlate with
  markets, but are not automatically causal or PPO-safe.

Financial NLP and LLMs:

- FinBERT: finance-specific language matters.
- FinGPT / BloombergGPT / PIXIU: domain adaptation and instruction data matter,
  but model outputs need task-specific evaluation.
- ConFIRM-style routing: query intent and knowledge-base labels should be
  explicit before retrieval.

Retrieval/RAG:

- BM25 is the mandatory baseline.
- ColBERT is a future recall/reranking candidate.
- Longformer/RAPTOR-style ideas matter for long SEC documents, but only after
  section/exhibit chunking and qrels are stable.

Event-study feedback:

- realized returns are useful diagnostics for label/source behavior;
- realized returns are not semantic ground truth;
- event-study outputs are forbidden as PPO features.

## Methodological Kill Rules

- If `available_at` is unreliable, the document is not PPO-safe.
- If route-aware retrieval does not beat BM25/hybrid on judged qrels, keep the
  simpler method.
- If Mistral/FinGPT does not beat Codex-rule on validated extraction labels,
  do not scale it as the main extractor.
- If source quality is argued only through trading metrics, the IR layer is not
  scientifically validated.
- If text features improve PPO but violate PIT/provenance rules, the result is
  invalid.

