# Annotations

This folder separates development labels from future human-reviewed labels.

Files:

- `sample_qrels.csv`: compatibility default used by example commands.
- `bootstrap_sample_qrels.csv`: same bootstrap labels, explicitly marked as
  development labels.
- `annotation_pool_batch_sample.csv`: deduplicated review pool built from
  `data/exports/ablation_batch_sample/ablation_retrieved_all.jsonl`.
- `search_quality_queries_v1.csv`: stable user-facing query set for web search
  quality checks across company filings, macro signals, risk, and portfolio
  impact intents.
- `search_quality_pool_v1.csv`: generated human-review pool from the live web
  search surface, including folder/group context and dashboard document links.
- `search_quality_qrels_bootstrap_v1.csv`: deterministic bootstrap qrels for
  the web search pool. These are development labels, not final human labels.
- `search_quality_qrels_bootstrap_v1_summary.csv`: per-query label counts for
  the bootstrap web-search qrels.
- `search_quality_pool_intent_v2.csv`: regenerated web-search pool after
  intent-aware source reranking.
- `search_quality_pool_entity_v3.csv`: regenerated web-search pool after
  entity-aware retrieval fixes for company aliases, wrong-company risk results,
  and score-first search ordering.
- `search_quality_pool_field_v4.csv`: regenerated web-search pool after
  field-aware reranking for analyst intents such as earnings guidance, risk,
  litigation, supply chain, bank credit, and margin pressure.
- `search_quality_pool_calibrated_v6.csv`: offline reranked pool produced by
  the calibrated search-reranker experiment. It is an evaluation artifact, not
  the live web UI ranking.
- `search_quality_pool_calibrated_v7.csv`: offline reranked pool after
  retraining calibrated weights on mixed qrels that include the first human
  spot-check.
- `search_quality_human_review_queue_v1.csv`: prioritized 150-row manual review
  queue for converting bootstrap qrels into human-reviewed search labels.
- `search_quality_human_review_queue_v1_summary.csv`: per-query coverage for
  the manual review queue.
- `search_quality_human_review_queue_v2.csv`: coverage-expanded review queue
  that adds unjudged top-ranked baseline/candidate documents. The current file
  has `assistant_review_v1` labels filled in `human_relevance` for ranking
  development; replace them with independent human labels for final reporting.
- `search_quality_human_review_queue_v2_summary.csv`: per-query coverage-gap
  counts for the expanded review queue.
- `search_quality_human_review_queue_v3.csv`: coverage-expanded queue for the
  `entity_v3` candidate run, currently filled with `assistant_review_v3` labels.
- `search_quality_human_review_queue_v3_summary.csv`: coverage-gap counts for
  the `entity_v3` review queue.
- `search_quality_human_review_queue_v4.csv`: coverage-expanded queue for the
  `field_v4` candidate run, currently filled with `assistant_review_v4` labels.
- `search_quality_human_review_queue_v4_summary.csv`: coverage-gap counts for
  the `field_v4` review queue.
- `search_quality_human_review_queue_v5.csv`: coverage-expanded queue for the
  calibrated reranker candidate, currently filled with `assistant_review_v5`
  labels.
- `search_quality_human_review_queue_v5_summary.csv`: coverage-gap counts for
  the calibrated reranker review queue.
- `search_quality_human_review_queue_v6.csv`: review queue with per-row
  `label_source` / `annotator` provenance and the first chat-based human
  spot-check applied.
- `search_quality_human_spotcheck_v1.csv`: compact 10-row human spot-check
  extracted from chat review.
- `search_quality_human_spotcheck_queue_v2.csv`: active-learning queue for the
  next human review batch. It prioritizes weak queries, calibrated top-10 rows,
  rank disagreements, borderline labels, source-scope mismatches, and
  wrong-company cases.
- `search_quality_human_spotcheck_queue_v2_summary.csv`: per-query summary for
  the active-learning spot-check queue.
- `search_quality_human_spotcheck_queue_v2.md`: compact chat-friendly rendering
  of the same queue.
- `search_quality_qrels_human_v1.csv`: qrels exported from the review queue.
  In the current checkout it contains `assistant_review_v1` labels, not
  independent human annotation.
- `search_quality_qrels_assistant_v3.csv`: pooled qrels exported from
  `search_quality_human_review_queue_v3.csv`; use for development comparison of
  baseline, `intent_v2`, and `entity_v3`.
- `search_quality_qrels_assistant_v4.csv`: pooled qrels exported from
  `search_quality_human_review_queue_v4.csv`; use for development comparison of
  baseline, `intent_v2`, `entity_v3`, and `field_v4`.
- `search_quality_qrels_assistant_v5.csv`: pooled qrels exported from
  `search_quality_human_review_queue_v5.csv`; use for development comparison of
  baseline, `intent_v2`, `entity_v3`, `field_v4`, and calibrated reranker runs.
- `search_quality_qrels_mixed_v6.csv`: qrels exported from
  `search_quality_human_review_queue_v6.csv`; includes
  `human_spotcheck_v1` labels for 10 rows and assistant labels for the remaining
  rows.
- `search_quality_qrels_review_queue_dryrun.csv`: generated smoke qrels from
  `bootstrap_relevance` in the review queue. Use only to test the pipeline.
- `search_quality_qrels_review_queue_dryrun_issues.csv`: export issues for the
  dry-run qrels file.
- `human_qrels_template.csv`: empty template for future human-reviewed qrels.

Bootstrap qrels are useful for checking the mechanics of evaluation, but they
are incomplete. Run:

```powershell
python evaluation/validate_qrels.py `
  --qrels data/annotations/bootstrap_sample_qrels.csv `
  --run data/exports/ablation_batch_sample/ablation_run.csv `
  --output data/exports/ablation_batch_sample/qrels_validation.csv
```

The current validation report intentionally highlights unlabeled retrieved
documents that should be reviewed before using metrics as research evidence.

After filling `annotation_pool_batch_sample.csv`, export reviewed qrels:

```powershell
python evaluation/export_qrels_from_pool.py `
  --input data/annotations/annotation_pool_batch_sample.csv `
  --output data/annotations/human_qrels_v1.csv `
  --issues-output data/annotations/human_qrels_v1_export_issues.csv `
  --label-source human_v1
```

Build the web-search review pool:

```powershell
python evaluation/build_search_quality_pool.py `
  --queries data/annotations/search_quality_queries_v1.csv `
  --output data/annotations/search_quality_pool_v1.csv `
  --run-output data/exports/search_quality_run_v1.csv `
  --top-k 10
```

Create bootstrap qrels for a measurable web-search baseline:

```powershell
python evaluation/build_search_quality_qrels.py `
  --input data/annotations/search_quality_pool_v1.csv `
  --output data/annotations/search_quality_qrels_bootstrap_v1.csv `
  --summary-output data/annotations/search_quality_qrels_bootstrap_v1_summary.csv
```

After manual review of `search_quality_human_review_queue_v1.csv`, export human
qrels:

```powershell
python evaluation/export_qrels_from_review_queue.py `
  --input data/annotations/search_quality_human_review_queue_v1.csv `
  --output data/annotations/search_quality_qrels_human_v1.csv `
  --issues-output data/annotations/search_quality_qrels_human_v1_issues.csv `
  --label-source human_search_v1 `
  --annotator reviewer_1 `
  --strict
```

Before using partial human qrels for ranking conclusions, run judged-coverage
diagnostics with `evaluation/evaluate_qrels_coverage.py`. Low top-10 coverage
means the next review queue should include the unjudged top-ranked documents
before comparing ranking methods. Use
`evaluation/build_coverage_gap_review_queue.py` to build that expanded queue.
Then validate only the evaluated window with `evaluation/validate_qrels.py
--top-k 10 --strict`; validating the full run requires labels beyond the
reported top-k metrics.

If independent human labels are not ready, the expanded queue can be filled with
a reproducible assistant-reviewed rubric:

```powershell
python evaluation/assistant_label_search_queue.py `
  --input data/annotations/search_quality_human_review_queue_v2.csv `
  --output data/annotations/search_quality_human_review_queue_v2.csv `
  --overwrite
```

Those labels are useful for development comparisons, but keep
`label_source=assistant_review_v1` when exporting qrels and do not present them
as external human relevance judgments.
