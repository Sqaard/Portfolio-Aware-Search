# Evaluation

Evaluation focuses on retrieval quality, not trading performance.

Supported metrics:

- Precision@5
- Precision@10
- NDCG@5
- NDCG@10
- MAP
- MRR

Default human-label scale:

- `0`: irrelevant
- `1`: mentions holding/context but not decision-useful
- `2`: useful evidence
- `3`: highly relevant and timely for the portfolio decision

## Label Files

Current labels are bootstrap research labels for the sample corpus:

- `data/annotations/bootstrap_sample_qrels.csv`
- `data/annotations/sample_qrels.csv` kept as the default compatibility path

Future human-reviewed labels should be saved separately, for example by copying
`data/annotations/human_qrels_template.csv` and filling `relevance`,
`label_source`, `annotator`, and `notes`.

Example:

```powershell
python evaluation/evaluate_ir_metrics.py `
  --qrels data/annotations/sample_qrels.csv `
  --run data/exports/sample_run.csv `
  --output data/exports/sample_metrics.csv
```

Run all configured ranking ablations:

```powershell
python evaluation/run_ablation_suite.py `
  --documents data/processed_documents/documents.jsonl `
  --portfolio configs/sample_portfolio.yaml `
  --metadata data/processed_documents/ticker_metadata.csv `
  --decision-datetime 2022-03-15T09:30:00-05:00 `
  --top-k 10 `
  --qrels data/annotations/sample_qrels.csv `
  --output-dir data/exports/ablation_sample
```

The ablation runner writes one retrieval JSONL per method, a combined
`ablation_run.csv`, and `ablation_metrics.csv`.

For the multi-query sample set:

```powershell
python evaluation/run_ablation_suite.py `
  --documents data/processed_documents/documents.jsonl `
  --queries data/portfolios/sample_query_set.csv `
  --metadata data/processed_documents/ticker_metadata.csv `
  --top-k 10 `
  --qrels data/annotations/sample_qrels.csv `
  --output-dir data/exports/ablation_batch_sample
```

This additionally writes `ablation_metrics_by_method.csv`, which averages each
metric over all query IDs per method.

The ablation runner also writes retrieval diagnostics:

- `ablation_diagnostics.csv`
- `ablation_diagnostics_by_method.csv`

These include `CausalValidity@K`, `DuplicateRate@K`,
`PortfolioCoverage@K`, stock/sector/market scope rates, and mean source
credibility.

## Document vs Evidence-Unit Retrieval

Use this comparison before changing the default retrieval grain. It runs the
same query set over whole normalized documents and over decision-grade evidence
units, then evaluates both against the same qrels.

```powershell
python evaluation/compare_document_vs_evidence_units.py `
  --documents data\processed_documents\sec_dow30_ppo_2010_2023_1800_with_dis_legacy_sections_documents.jsonl,data\processed_documents\official_macro_2010_2023_documents.jsonl `
  --queries data\portfolios\sample_query_set.csv `
  --metadata data\processed_documents\ticker_metadata.csv `
  --top-k 10 `
  --method full_hybrid `
  --qrels data\annotations\sample_qrels.csv `
  --output-dir data\exports\document_vs_evidence_units_v1
```

The default `--evidence-eval-grain source_document` keeps SEC sections and
macro observations at their original row IDs, while company IR fact blocks are
collapsed back to their source document ID. This is the fairest mode when qrels
were labeled on the existing document corpus. Use `--evidence-eval-grain unit`
only when qrels label individual evidence-unit IDs, or `--evidence-eval-grain
parent` when qrels label parent filings/pages.

Key outputs:

- `comparison_run.csv`: combined document and evidence-unit run.
- `comparison_metrics_by_method.csv`: raw metric averages by method.
- `comparison_delta_by_method.csv`: evidence-unit minus document deltas.
- `qrels_coverage_summary.csv`: judged top-k coverage; do not trust metrics
  when this is low.
- `comparison_annotation_pool.csv`: review table for qrels matching this run.
- `evidence_unit_raw_retrieved_all.jsonl`: exact sections/fact blocks returned.
- `evidence_unit_eval_retrieved_all.jsonl`: qrels-grain collapsed run.

If coverage is low, seed a development qrels file from the generated pool:

```powershell
python evaluation/assistant_label_comparison_pool.py `
  --input data\exports\document_vs_evidence_units_v1\comparison_annotation_pool.csv `
  --output data\exports\document_vs_evidence_units_v1\comparison_annotation_pool_labeled_assistant_v1.csv

python evaluation/export_qrels_from_pool.py `
  --input data\exports\document_vs_evidence_units_v1\comparison_annotation_pool_labeled_assistant_v1.csv `
  --output data\annotations\document_vs_evidence_units_qrels_assistant_v1.csv `
  --issues-output data\annotations\document_vs_evidence_units_qrels_assistant_v1_issues.csv `
  --label-source assistant_document_vs_evidence_v1 `
  --strict
```

Current development check on the SEC-section + macro corpus:

- qrels coverage at 10: 1.000 after assistant labels;
- document `full_hybrid`: Precision@10 1.000, NDCG@10 0.836, MRR 1.000;
- evidence-unit `full_hybrid`: Precision@10 1.000, NDCG@10 0.836, MRR 1.000.

Interpretation: this corpus is already mostly evidence-unit shaped
(`sec_section`, `sec_exhibit`, `macro_observation`), so evidence-unit retrieval
does not improve ranking yet. The next useful test should include raw company
IR pages or whole filings where fact-block extraction actually changes the
retrieval grain.

Company-IR corpus check:

```powershell
python evaluation/compare_document_vs_evidence_units.py `
  --documents data\processed_documents\sec_macro_company_ir_ppo_2010_2023_documents.jsonl `
  --queries data\portfolios\sample_query_set.csv `
  --metadata data\processed_documents\ticker_metadata.csv `
  --top-k 10 `
  --method full_hybrid `
  --output-dir data\exports\document_vs_evidence_units_company_ir_v1
```

This run expands 26,368 source rows into 39,101 evidence units, including
13,850 `company_ir_fact_block` rows. Development qrels are stored in
`data/annotations/document_vs_evidence_units_company_ir_qrels_assistant_v1.csv`.

Current development result:

| Run | Precision@10 | NDCG@10 | MRR |
| --- | ---: | ---: | ---: |
| document `full_hybrid` | 1.000 | 0.833 | 1.000 |
| evidence-unit `full_hybrid` | 1.000 | 0.778 | 1.000 |

Interpretation: evidence-unit mode is not ready as the default on the mixed
SEC/macro/company-IR corpus. It increases retrieval grain, but top-10 is still
dominated by SEC sections and the changed corpus-level sparse normalization
slightly worsens ordering. The next ranking task is to calibrate evidence-unit
mode with source/type-aware weights before enabling it broadly.

Source/type-aware evidence-unit calibration:

```powershell
python evaluation/calibrate_evidence_unit_reranker.py `
  --input data\exports\document_vs_evidence_units_company_ir_v1\evidence_unit_raw_retrieved_all.jsonl `
  --output-dir data\exports\document_vs_evidence_units_company_ir_v1\calibrated_source_type_v1 `
  --qrels data\annotations\document_vs_evidence_units_company_ir_qrels_assistant_v1.csv `
  --document-summary data\exports\document_vs_evidence_units_company_ir_v1\comparison_metrics_by_method_assistant_v1.csv
```

The calibration uses transparent features only: evidence-unit claim type,
source/type family, and publication freshness. It does not call an LLM and does
not inspect future outcomes.

Current development result:

| Run | Precision@10 | NDCG@10 | MRR |
| --- | ---: | ---: | ---: |
| document `full_hybrid` | 1.000 | 0.833 | 1.000 |
| raw evidence-unit `full_hybrid` | 1.000 | 0.778 | 1.000 |
| calibrated evidence-unit `full_hybrid` | 1.000 | 0.976 | 1.000 |

Interpretation: a small source/type/freshness calibration recovers and improves
ordering on the development qrels. Do not promote it to production until the
same comparison is checked on independent human labels or a larger held-out
query set.

Held-out company-IR validation:

```powershell
python evaluation/compare_document_vs_evidence_units.py `
  --documents data\processed_documents\sec_macro_company_ir_ppo_2010_2023_documents.jsonl `
  --queries data\portfolios\evidence_unit_holdout_query_set_v1.csv `
  --metadata data\processed_documents\ticker_metadata.csv `
  --top-k 10 `
  --method full_hybrid `
  --output-dir data\exports\document_vs_evidence_units_company_ir_holdout_v1

python evaluation/assistant_label_comparison_pool.py `
  --input data\exports\document_vs_evidence_units_company_ir_holdout_v1\comparison_annotation_pool.csv `
  --output data\exports\document_vs_evidence_units_company_ir_holdout_v1\comparison_annotation_pool_labeled_assistant_v1.csv

python evaluation/export_qrels_from_pool.py `
  --input data\exports\document_vs_evidence_units_company_ir_holdout_v1\comparison_annotation_pool_labeled_assistant_v1.csv `
  --output data\annotations\document_vs_evidence_units_company_ir_holdout_qrels_assistant_v1.csv `
  --issues-output data\annotations\document_vs_evidence_units_company_ir_holdout_qrels_assistant_v1_issues.csv `
  --label-source assistant_document_vs_evidence_v1 `
  --strict

python evaluation/calibrate_evidence_unit_reranker.py `
  --input data\exports\document_vs_evidence_units_company_ir_holdout_v1\evidence_unit_raw_retrieved_all.jsonl `
  --output-dir data\exports\document_vs_evidence_units_company_ir_holdout_v1\calibrated_source_type_v1 `
  --qrels data\annotations\document_vs_evidence_units_company_ir_holdout_qrels_assistant_v1.csv `
  --document-summary data\exports\document_vs_evidence_units_company_ir_holdout_v1\comparison_metrics_by_method_assistant_v1.csv
```

Held-out assistant-development result:

| Run | Precision@10 | NDCG@10 | MRR |
| --- | ---: | ---: | ---: |
| document `full_hybrid` | 1.000 | 0.818 | 1.000 |
| raw evidence-unit `full_hybrid` | 1.000 | 0.825 | 1.000 |
| calibrated evidence-unit `full_hybrid` | 1.000 | 1.000 | 1.000 |

Interpretation: the same transparent source/type/freshness calibration improves
the independent held-out query set by +0.182 NDCG@10 versus document retrieval.
This is a stronger development signal than the first in-sample check, but it
still uses assistant labels. Promotion to the live search path should wait for
manual review of this held-out pool or for a larger human-labeled qrels file.

Build the manual held-out evidence-unit review queue:

```powershell
python evaluation/build_evidence_unit_review_queue.py `
  --document-run data\exports\document_vs_evidence_units_company_ir_holdout_v1\document_retrieved_all.jsonl `
  --raw-evidence-run data\exports\document_vs_evidence_units_company_ir_holdout_v1\evidence_unit_raw_retrieved_all.jsonl `
  --calibrated-run data\exports\document_vs_evidence_units_company_ir_holdout_v1\calibrated_source_type_v1\evidence_unit_calibrated_eval.jsonl `
  --qrels data\annotations\document_vs_evidence_units_company_ir_holdout_qrels_assistant_v1.csv `
  --output data\annotations\evidence_unit_holdout_review_queue_v1.csv `
  --summary-output data\annotations\evidence_unit_holdout_review_queue_v1_summary.csv `
  --prompt-output data\annotations\evidence_unit_holdout_review_queue_v1.md `
  --top-k 10 `
  --limit 80
```

Current queue size: 45 rows across 4 held-out queries. The queue includes
document rank, raw evidence-unit rank, calibrated rank, rank deltas,
calibration tags, and the current assistant label. Use this file for manual
review before reporting the held-out improvement as human-validated.

For an assistant-reviewed development pass, fill suggested labels and export
qrels without pretending they are human labels:

```powershell
python evaluation/assistant_review_evidence_unit_queue.py `
  --input data\annotations\evidence_unit_holdout_review_queue_v1.csv `
  --output data\annotations\evidence_unit_holdout_review_queue_v1_assistant_reviewed.csv `
  --overwrite

python evaluation/export_qrels_from_evidence_unit_review_queue.py `
  --input data\annotations\evidence_unit_holdout_review_queue_v1_assistant_reviewed.csv `
  --output data\annotations\document_vs_evidence_units_company_ir_holdout_qrels_assistant_reviewed_v1.csv `
  --issues-output data\annotations\document_vs_evidence_units_company_ir_holdout_qrels_assistant_reviewed_v1_issues.csv `
  --label-source assistant_evidence_unit_review_v1 `
  --annotator codex_assistant `
  --strict

python evaluation/evaluate_ir_metrics.py `
  --qrels data\annotations\document_vs_evidence_units_company_ir_holdout_qrels_assistant_reviewed_v1.csv `
  --run data\exports\document_vs_evidence_units_company_ir_holdout_v1\comparison_run.csv `
  --output data\exports\document_vs_evidence_units_company_ir_holdout_v1\comparison_metrics_assistant_reviewed_v1.csv `
  --summary-output data\exports\document_vs_evidence_units_company_ir_holdout_v1\comparison_metrics_by_method_assistant_reviewed_v1.csv

python evaluation/calibrate_evidence_unit_reranker.py `
  --input data\exports\document_vs_evidence_units_company_ir_holdout_v1\evidence_unit_raw_retrieved_all.jsonl `
  --output-dir data\exports\document_vs_evidence_units_company_ir_holdout_v1\calibrated_source_type_assistant_reviewed_v1 `
  --qrels data\annotations\document_vs_evidence_units_company_ir_holdout_qrels_assistant_reviewed_v1.csv `
  --document-summary data\exports\document_vs_evidence_units_company_ir_holdout_v1\comparison_metrics_by_method_assistant_reviewed_v1.csv
```

Assistant-reviewed held-out result (`assistant_evidence_unit_review_v1`, 45
labels; relevance counts: 1=20, 2=15, 3=10):

| Run | Precision@10 | NDCG@10 | MRR |
| --- | ---: | ---: | ---: |
| document `full_hybrid` | 1.000 | 0.843 | 1.000 |
| raw evidence-unit `full_hybrid` | 1.000 | 0.813 | 1.000 |
| calibrated evidence-unit `full_hybrid` | 1.000 | 1.000 | 1.000 |

Interpretation: under a stricter assistant review, raw evidence-unit retrieval
is not good enough by itself, but the calibrated source/type/freshness layer
still improves held-out NDCG@10 by +0.157 versus document retrieval. The
remaining promotion blocker is independent human review, not code mechanics.

Compact human spot-check packet:

```powershell
python evaluation/build_evidence_unit_human_spotcheck.py `
  --input data\annotations\evidence_unit_holdout_review_queue_v1_assistant_reviewed.csv `
  --output data\annotations\evidence_unit_holdout_human_spotcheck_v1.csv `
  --summary-output data\annotations\evidence_unit_holdout_human_spotcheck_v1_summary.csv `
  --prompt-output data\annotations\evidence_unit_holdout_human_spotcheck_v1.md `
  --limit 15 `
  --max-per-query 4
```

The generated packet selects 15 high-impact rows for quick human judgment:
calibrated top results, promotions, document/unit rank disagreements,
borderline labels, and assistant label changes. The CSV deliberately leaves
`human_relevance` blank; do not report these rows as human labels until a
reviewer fills them with 0, 1, 2, or 3.

After the spot-check is filled, apply it to the full evidence-unit queue:

```powershell
python evaluation/apply_evidence_unit_human_spotcheck.py `
  --review-queue data\annotations\evidence_unit_holdout_review_queue_v1_assistant_reviewed.csv `
  --spotcheck data\annotations\evidence_unit_holdout_human_spotcheck_v1.csv `
  --output-queue data\annotations\evidence_unit_holdout_review_queue_v2_mixed.csv `
  --qrels-output data\annotations\document_vs_evidence_units_company_ir_holdout_qrels_mixed_v1.csv `
  --issues-output data\annotations\document_vs_evidence_units_company_ir_holdout_qrels_mixed_v1_issues.csv `
  --strict
```

This creates mixed qrels: human spot-check rows override assistant labels, and
all remaining rows stay explicitly marked as `assistant_evidence_unit_review_v1`.

For fast chat-based labeling, fill the spot-check CSV directly from a compact
answer string:

```powershell
python evaluation/fill_evidence_unit_spotcheck_labels.py `
  --input data\annotations\evidence_unit_holdout_human_spotcheck_v1.csv `
  --labels "1=3, 2=1, 3=0" `
  --output data\annotations\evidence_unit_holdout_human_spotcheck_v1.csv `
  --reviewer-notes "human chat review" `
  --strict
```

Then run the promotion gate:

```powershell
python evaluation/evaluate_evidence_unit_promotion_gate.py `
  --qrels data\annotations\document_vs_evidence_units_company_ir_holdout_qrels_mixed_v1.csv `
  --comparison-run data\exports\document_vs_evidence_units_company_ir_holdout_v1\comparison_run.csv `
  --calibrated-run data\exports\document_vs_evidence_units_company_ir_holdout_v1\calibrated_source_type_assistant_reviewed_v1\evidence_unit_calibrated_run.csv `
  --output-dir data\exports\document_vs_evidence_units_company_ir_holdout_v1\promotion_gate_mixed_v1 `
  --prefix evidence_unit_promotion_mixed_v1
```

The gate requires enough human spot-check labels, positive calibrated NDCG@10
delta versus document retrieval, no material Precision@10 drop, and complete
top-10 qrels coverage. The current dry-run output, which contains no human
labels, is stored in
`data\exports\document_vs_evidence_units_company_ir_holdout_v1\promotion_gate_mixed_dryrun_v1`
and correctly returns `block_promotion` with reason
`pending_human_spotcheck_labels`.

After applying 15 user-confirmed spot-check labels, the mixed-qrels gate output
is stored in
`data\exports\document_vs_evidence_units_company_ir_holdout_v1\promotion_gate_mixed_v1`
and returns `accept_guarded_promotion`.

| Run | Precision@10 | NDCG@10 | MRR |
| --- | ---: | ---: | ---: |
| document `full_hybrid` | 0.950 | 0.824 | 1.000 |
| raw evidence-unit `full_hybrid` | 0.950 | 0.823 | 1.000 |
| calibrated evidence-unit `full_hybrid` | 0.950 | 0.982 | 1.000 |

This accepts only the calibrated, intent-guarded evidence-unit path. Raw
evidence-unit retrieval remains rejected as a default path.

Live search smoke regression:

```powershell
python evaluation/run_live_search_smoke.py `
  --output-dir data\exports\live_search_smoke_v1 `
  --strict
```

For a running local server:

```powershell
python evaluation/run_live_search_smoke.py `
  --base-url http://127.0.0.1:8780 `
  --output-dir data\exports\live_search_smoke_cloudflare_preflight `
  --strict
```

The smoke suite checks four representative queries: SEC risk factors, broad
company overview, macro rates/credit, and SEC 8-K earnings guidance. Current
baseline: 4/4 passed, max latency 7402.4 ms, mean latency 2905.8 ms, promotion
status `accepted` for all cases.

Live gating note: `retrieval/evidence_unit_gate.py` now exposes a deterministic
guard. Evidence-unit mode is allowed only for high-specificity intents such as
Item 1A risk factors, earnings/guidance, legal proceedings, and official macro
observations. Broad company-overview queries stay on document-level retrieval.

Live guarded switch:

- `FINPORTFOLIO_EVIDENCE_UNIT_SEARCH=1` enables the guarded switch; this is the
  default.
- `FINPORTFOLIO_EVIDENCE_UNIT_SEARCH=0` disables it and forces the old
  document/index path.
- The switch is still intent-gated. It activates only when
  `evidence_unit_gate.enabled=true`; broad queries keep `search_grain=document`.
- The response payload includes `evidence_unit_gate.active`,
  `active_result_count`, and `feature_flag_enabled`.
- Offline and live calibration share `retrieval/evidence_unit_calibration.py`,
  so evaluation and demo behavior use the same source/type/freshness weights.

Smoke check on the full local corpus:

| Query | Gate | Grain | Time |
| --- | --- | --- | ---: |
| `Apple risk factors in the latest 10-K` | enabled/active | `evidence_unit` Item 1A | 7.3s |
| `Apple company overview and product history` | disabled | `document` | 4.8s |
| `Fed rates and credit spreads` | enabled/active | `evidence_unit` macro observations | 3.6s |

Macro evidence-unit search uses a smaller query-selective source window to
avoid Cloudflare-style timeout behavior; it no longer builds all macro evidence
units for a live request.

Build a compact static HTML report:

```powershell
python evaluation/build_html_report.py `
  --metrics data/exports/ablation_batch_sample/ablation_metrics_by_method.csv `
  --diagnostics data/exports/ablation_batch_sample/ablation_diagnostics_by_method.csv `
  --output data/exports/ablation_batch_sample/retrieval_report.html `
  --title "FinPortfolio IR Batch Retrieval Report"
```

## Annotation Pool

Build a deduplicated annotation pool from a combined ablation output:

```powershell
python evaluation/build_annotation_pool.py `
  --input data/exports/ablation_batch_sample/ablation_retrieved_all.jsonl `
  --qrels data/annotations/bootstrap_sample_qrels.csv `
  --output data/annotations/annotation_pool_batch_sample.csv
```

The pool contains one row per `query_id/doc_id`, with method lists, per-method
ranks, per-method scores, and an empty `relevance` column for human review.

Validate qrels and run coverage:

```powershell
python evaluation/validate_qrels.py `
  --qrels data/annotations/bootstrap_sample_qrels.csv `
  --run data/exports/ablation_batch_sample/ablation_run.csv `
  --output data/exports/ablation_batch_sample/qrels_validation.csv `
  --top-k 10
```

After human review, export a qrels file from the annotation pool:

```powershell
python evaluation/export_qrels_from_pool.py `
  --input data/annotations/annotation_pool_batch_sample.csv `
  --output data/annotations/human_qrels_v1.csv `
  --issues-output data/annotations/human_qrels_v1_export_issues.csv `
  --label-source human_v1
```

For bootstrap development only, fallback to `existing_relevance`:

```powershell
python evaluation/export_qrels_from_pool.py `
  --input data/annotations/annotation_pool_batch_sample.csv `
  --output data/annotations/bootstrap_from_pool_qrels.csv `
  --issues-output data/annotations/bootstrap_from_pool_issues.csv `
  --fallback-existing `
  --label-source bootstrap
```

## Web Search Quality Pool

The web UI applies extra behavior that offline ablations do not cover:
company-name expansion, foldering, grouped filings, favorite-site annotation,
fresh-first sorting, and document viewer links. Build a separate review pool
from the actual search surface before changing ranking logic:

```powershell
python evaluation/build_search_quality_pool.py `
  --queries data/annotations/search_quality_queries_v1.csv `
  --output data/annotations/search_quality_pool_v1.csv `
  --run-output data/exports/search_quality_run_v1.csv `
  --top-k 10
```

The pool includes one row per `query_id/doc_id`, an empty `relevance` field for
human review, and `document_path` values such as `/documents/<doc_id>` so the
same document can be opened through a local or Cloudflare-served dashboard.

After review, export qrels with the existing exporter:

```powershell
python evaluation/export_qrels_from_pool.py `
  --input data/annotations/search_quality_pool_v1.csv `
  --output data/annotations/search_quality_qrels_v1.csv `
  --issues-output data/annotations/search_quality_qrels_v1_issues.csv `
  --label-source human_search_v1
```

Then evaluate the current web ranking run:

```powershell
python evaluation/evaluate_ir_metrics.py `
  --qrels data/annotations/search_quality_qrels_v1.csv `
  --run data/exports/search_quality_run_v1.csv `
  --output data/exports/search_quality_metrics_v1.csv
```

For development before human labels are ready, build deterministic bootstrap
qrels from the web-search pool and measure the current ranking:

```powershell
python evaluation/build_search_quality_qrels.py `
  --input data/annotations/search_quality_pool_v1.csv `
  --output data/annotations/search_quality_qrels_bootstrap_v1.csv `
  --summary-output data/annotations/search_quality_qrels_bootstrap_v1_summary.csv

python evaluation/evaluate_ir_metrics.py `
  --qrels data/annotations/search_quality_qrels_bootstrap_v1.csv `
  --run data/exports/search_quality_run_v1.csv `
  --output data/exports/search_quality_metrics_baseline_v1.csv `
  --summary-output data/exports/search_quality_metrics_baseline_v1_summary.csv
```

After changing source-intent ranking, regenerate a separate run and compare
against the same qrels:

```powershell
python evaluation/build_search_quality_pool.py `
  --queries data/annotations/search_quality_queries_v1.csv `
  --output data/annotations/search_quality_pool_intent_v2.csv `
  --run-output data/exports/search_quality_run_intent_v2.csv `
  --top-k 10 `
  --method web_search_intent_v2

python evaluation/evaluate_ir_metrics.py `
  --qrels data/annotations/search_quality_qrels_bootstrap_v1.csv `
  --run data/exports/search_quality_run_intent_v2.csv `
  --output data/exports/search_quality_metrics_intent_v2.csv `
  --summary-output data/exports/search_quality_metrics_intent_v2_summary.csv
```

Current bootstrap result:

- baseline `web_search_current`: Precision@10 0.752, NDCG@10 0.741, MRR 0.816
- intent-aware `web_search_intent_v2`: Precision@10 0.824, NDCG@10 0.892, MRR 0.946

Build the next-step human review queue from the same artifacts:

```powershell
python evaluation/build_search_review_queue.py `
  --baseline-pool data/annotations/search_quality_pool_v1.csv `
  --candidate-pool data/annotations/search_quality_pool_intent_v2.csv `
  --qrels data/annotations/search_quality_qrels_bootstrap_v1.csv `
  --candidate-metrics data/exports/search_quality_metrics_intent_v2.csv `
  --output data/annotations/search_quality_human_review_queue_v1.csv `
  --summary-output data/annotations/search_quality_human_review_queue_v1_summary.csv `
  --limit 150 `
  --min-per-query 4
```

The queue first guarantees top candidate-run coverage per query, then adds
baseline top rows, borderline bootstrap labels, possible false positives/false
negatives, weak queries, and rows whose rank changed after intent-aware
reranking. This prevents metrics from treating unjudged top candidate results as
irrelevant.

After filling `human_relevance` in the review queue, export final human qrels:

```powershell
python evaluation/export_qrels_from_review_queue.py `
  --input data/annotations/search_quality_human_review_queue_v1.csv `
  --output data/annotations/search_quality_qrels_human_v1.csv `
  --issues-output data/annotations/search_quality_qrels_human_v1_issues.csv `
  --label-source human_search_v1 `
  --annotator reviewer_1 `
  --strict
```

Then evaluate both ranking runs against the same human labels:

```powershell
python evaluation/evaluate_ir_metrics.py `
  --qrels data/annotations/search_quality_qrels_human_v1.csv `
  --run data/exports/search_quality_run_v1.csv `
  --output data/exports/search_quality_metrics_baseline_human_v1.csv `
  --summary-output data/exports/search_quality_metrics_baseline_human_v1_summary.csv

python evaluation/evaluate_ir_metrics.py `
  --qrels data/annotations/search_quality_qrels_human_v1.csv `
  --run data/exports/search_quality_run_intent_v2.csv `
  --output data/exports/search_quality_metrics_intent_human_v1.csv `
  --summary-output data/exports/search_quality_metrics_intent_human_v1_summary.csv
```

For pipeline checks only, `--fallback-bootstrap` can export a dry-run qrels file
from `bootstrap_relevance`; do not report that file as human evaluation.

Before trusting metrics from any partial label file, measure judged coverage:

```powershell
python evaluation/evaluate_qrels_coverage.py `
  --qrels data/annotations/search_quality_qrels_human_v1.csv `
  --run data/exports/search_quality_run_intent_v2.csv `
  --output data/exports/search_quality_coverage_intent_human_v1.csv `
  --summary-output data/exports/search_quality_coverage_intent_human_v1_summary.csv
```

If `mean_judged_rate_at_10` is low, add those unjudged top-ranked documents to
the review queue before making ranking decisions.

Build an expanded coverage-gap review queue from the unjudged top-10 documents:

```powershell
python evaluation/build_coverage_gap_review_queue.py `
  --existing-queue data/annotations/search_quality_human_review_queue_v1.csv `
  --baseline-pool data/annotations/search_quality_pool_v1.csv `
  --candidate-pool data/annotations/search_quality_pool_intent_v2.csv `
  --qrels data/annotations/search_quality_qrels_human_v1.csv `
  --bootstrap-qrels data/annotations/search_quality_qrels_bootstrap_v1.csv `
  --output data/annotations/search_quality_human_review_queue_v2.csv `
  --summary-output data/annotations/search_quality_human_review_queue_v2_summary.csv `
  --top-k 10 `
  --limit 300
```

For development dry-runs, use `search_quality_qrels_review_queue_dryrun.csv` as
`--qrels`; for final evaluation, use only human qrels.

For a reproducible assistant-reviewed pass that unblocks ranking experiments
before independent human labeling, fill the expanded queue with an explicit
rubric:

```powershell
python evaluation/assistant_label_search_queue.py `
  --input data/annotations/search_quality_human_review_queue_v2.csv `
  --output data/annotations/search_quality_human_review_queue_v2.csv `
  --overwrite

python evaluation/export_qrels_from_review_queue.py `
  --input data/annotations/search_quality_human_review_queue_v2.csv `
  --output data/annotations/search_quality_qrels_human_v1.csv `
  --issues-output data/annotations/search_quality_qrels_human_v1_issues.csv `
  --label-source assistant_review_v1 `
  --annotator codex_assistant `
  --strict
```

`assistant_review_v1` is not a replacement for independent human annotation.
Keep the label source visible when reporting results, and overwrite these labels
only after a human reviewer fills the same `human_relevance` field.

Validate that the qrels cover the evaluated window:

```powershell
python evaluation/validate_qrels.py `
  --qrels data/annotations/search_quality_qrels_human_v1.csv `
  --run data/exports/search_quality_run_intent_v2.csv `
  --output data/exports/search_quality_qrels_human_v1_validation.csv `
  --top-k 10 `
  --strict
```

Current coverage-expanded review-queue dry-run result, using bootstrap labels only:

- baseline `web_search_current`: Precision@10 0.752, NDCG@10 0.781, MRR 0.816,
  mean judged@10 1.000
- intent-aware `web_search_intent_v2`: Precision@10 0.824, NDCG@10 0.931, MRR 0.946,
  mean judged@10 1.000

These are still bootstrap labels, not human evaluation. The coverage report
now confirms that the dry-run top-10 comparison is pooled; final claims still
require human `human_relevance` labels.

Current assistant-reviewed result (`assistant_review_v1`, 270 labels; relevance
counts: 0=93, 1=28, 2=29, 3=120):

- baseline `web_search_current`: Precision@10 0.628, NDCG@10 0.728, MRR 0.716,
  mean judged@10 1.000
- intent-aware `web_search_intent_v2`: Precision@10 0.636, NDCG@10 0.758,
  MRR 0.784, mean judged@10 1.000

The strict assistant-reviewed labels still show failures for `web_jpm_credit`,
`web_mmm_filings`, and `web_mmm_litigation`; those queries need retrieval
coverage or entity/source normalization work before reranking alone can fix
them.

Entity-aware retrieval update:

```powershell
python evaluation/build_search_quality_pool.py `
  --queries data/annotations/search_quality_queries_v1.csv `
  --output data/annotations/search_quality_pool_entity_v3.csv `
  --run-output data/exports/search_quality_run_entity_v3.csv `
  --top-k 10 `
  --method web_search_entity_v3

python evaluation/build_coverage_gap_review_queue.py `
  --existing-queue data/annotations/search_quality_human_review_queue_v2.csv `
  --baseline-pool data/annotations/search_quality_pool_v1.csv `
  --candidate-pool data/annotations/search_quality_pool_entity_v3.csv `
  --qrels data/annotations/search_quality_qrels_human_v1.csv `
  --bootstrap-qrels data/annotations/search_quality_qrels_bootstrap_v1.csv `
  --output data/annotations/search_quality_human_review_queue_v3.csv `
  --summary-output data/annotations/search_quality_human_review_queue_v3_summary.csv `
  --top-k 10 `
  --limit 500
```

The `entity_v3` run fixes two measured retrieval failures: numeric company names
such as `3M` now map to their ticker (`MMM`), and entity-specific risk queries
no longer admit generic high-signal documents from unrelated companies ahead of
the requested company. After assistant-reviewing the expanded 427-row queue
(`assistant_review_v3`, relevance counts: 0=119, 1=43, 2=45, 3=220), the pooled
top-10 comparison is:

- baseline `web_search_current`: Precision@10 0.628, NDCG@10 0.670, MRR 0.716,
  mean judged@10 1.000
- intent-aware `web_search_intent_v2`: Precision@10 0.636, NDCG@10 0.677,
  MRR 0.784, mean judged@10 1.000
- entity-aware `web_search_entity_v3`: Precision@10 0.832, NDCG@10 0.871,
  MRR 0.964, mean judged@10 1.000

Field-aware reranking update:

```powershell
python evaluation/build_search_quality_pool.py `
  --queries data/annotations/search_quality_queries_v1.csv `
  --output data/annotations/search_quality_pool_field_v4.csv `
  --run-output data/exports/search_quality_run_field_v4.csv `
  --top-k 10 `
  --method web_search_field_v4

python evaluation/build_coverage_gap_review_queue.py `
  --existing-queue data/annotations/search_quality_human_review_queue_v3.csv `
  --baseline-pool data/annotations/search_quality_pool_entity_v3.csv `
  --candidate-pool data/annotations/search_quality_pool_field_v4.csv `
  --qrels data/annotations/search_quality_qrels_assistant_v3.csv `
  --bootstrap-qrels data/annotations/search_quality_qrels_bootstrap_v1.csv `
  --output data/annotations/search_quality_human_review_queue_v4.csv `
  --summary-output data/annotations/search_quality_human_review_queue_v4_summary.csv `
  --top-k 10 `
  --limit 550
```

The `field_v4` run keeps the entity/source logic from `entity_v3`, then adds
query-field profiles for analyst intents: earnings guidance, company risk,
legal/regulatory, supply chain, energy, consumer demand, bank credit, and
margin pressure. This fixes cases where the right company and source family
were found, but the wrong section was ranked first, for example product press
releases ahead of earnings guidance or earnings exhibits ahead of risk-factor
sections.

After assistant-reviewing the expanded 453-row queue (`assistant_review_v4`,
relevance counts: 0=122, 1=44, 2=48, 3=239), all compared top-10 runs validate
with zero qrels issues and mean judged@10 1.000. The pooled comparison is:

| Method | Precision@10 | NDCG@10 | MRR |
| --- | ---: | ---: | ---: |
| baseline `web_search_current` | 0.628 | 0.661 | 0.716 |
| source-intent `web_search_intent_v2` | 0.636 | 0.667 | 0.784 |
| entity-aware `web_search_entity_v3` | 0.832 | 0.838 | 0.964 |
| field-aware `web_search_field_v4` | 0.860 | 0.917 | 1.000 |

The largest remaining weak spots are `web_earnings_risk`, `web_apple_guidance`,
and `web_chevron_energy`; the next ranking step should use the accumulated
assistant/human labels to calibrate or learn field weights instead of adding
more one-off rules.

Calibrated reranker experiment:

```powershell
python evaluation/calibrate_search_reranker.py `
  --pool data/annotations/search_quality_pool_field_v4.csv `
  --queries data/annotations/search_quality_queries_v1.csv `
  --qrels data/annotations/search_quality_qrels_assistant_v5.csv `
  --run-output data/exports/search_quality_run_calibrated_v6.csv `
  --pool-output data/annotations/search_quality_pool_calibrated_v6.csv `
  --weights-output data/exports/search_reranker_calibrated_v6_weights.json `
  --cv-output data/exports/search_reranker_calibration_v6_cv.csv `
  --summary-output data/exports/search_reranker_calibration_v6_summary.csv `
  --method web_search_calibrated_v6
```

This keeps the live system rule based, but tests whether accumulated qrels can
calibrate the reranker. The optimizer uses small interpretable features
(`rank_prior`, source-scope match, expected ticker match, wrong-company penalty,
and field mismatch penalties) with query-level cross-validation. Query-level
folds are important because documents inside one query are highly correlated.

The first calibrated candidate exposed 9 previously unjudged top-10 documents,
so the review queue was expanded before reporting metrics:

```powershell
python evaluation/build_coverage_gap_review_queue.py `
  --existing-queue data/annotations/search_quality_human_review_queue_v4.csv `
  --baseline-pool data/annotations/search_quality_pool_field_v4.csv `
  --candidate-pool data/annotations/search_quality_pool_calibrated_v5.csv `
  --qrels data/annotations/search_quality_qrels_assistant_v4.csv `
  --bootstrap-qrels data/annotations/search_quality_qrels_bootstrap_v1.csv `
  --output data/annotations/search_quality_human_review_queue_v5.csv `
  --summary-output data/annotations/search_quality_human_review_queue_v5_summary.csv `
  --top-k 10 `
  --limit 650
```

After assistant-reviewing the 462-row expanded queue (`assistant_review_v5`,
relevance counts: 0=122, 1=44, 2=50, 3=246), `calibrated_v6` validates with
zero top-10 qrels issues and mean judged@10 1.000. The pooled comparison on
`search_quality_qrels_assistant_v5.csv` is:

| Method | Precision@10 | NDCG@10 | MRR |
| --- | ---: | ---: | ---: |
| baseline `web_search_current` | 0.628 | 0.660 | 0.716 |
| source-intent `web_search_intent_v2` | 0.636 | 0.666 | 0.784 |
| entity-aware `web_search_entity_v3` | 0.832 | 0.837 | 0.964 |
| field-aware `web_search_field_v4` | 0.860 | 0.911 | 1.000 |
| calibrated `web_search_calibrated_v6` | 0.872 | 0.934 | 1.000 |

Cross-validation for `calibrated_v6` gives mean test NDCG@10 0.928 and mean
test Precision@10 0.868. Treat this as a development result until the same
queue is independently reviewed by a human annotator.

Human spot-check update:

The first manual chat review is stored in
`data/annotations/search_quality_human_spotcheck_v1.csv`. It overrides 10
judgments in `search_quality_human_review_queue_v6.csv` while preserving
`label_source` and `annotator` provenance. The spot-check clarified that:

- Apple risk-factor queries should prefer `Item 1A Risk Factors`; non-Item-1A
  Apple evidence is only weak fallback evidence, and wrong-company evidence is
  irrelevant.
- Bank credit-cycle queries should prefer macro/bank-cycle evidence; generic
  bank earnings material is weak fallback evidence, and non-bank company risk is
  irrelevant.
- Some product-launch and yield-curve documents are useful analyst evidence for
  broader Apple earnings/demand and macro-risk intents.

Using mixed qrels (`search_quality_qrels_mixed_v6.csv`: 10 human spot-check
labels plus assistant-reviewed labels for the remaining rows), the comparison is:

| Method | Precision@10 | NDCG@10 | MRR |
| --- | ---: | ---: | ---: |
| field-aware `web_search_field_v4` | 0.860 | 0.939 | 1.000 |
| calibrated `web_search_calibrated_v7` | 0.872 | 0.955 | 1.000 |

`calibrated_v7` has zero top-10 qrels issues and mean judged@10 1.000 on the
mixed qrels. Its query-level cross-validation mean test NDCG@10 is 0.954.

Active-learning human spot-check queue:

```powershell
python evaluation/build_human_spotcheck_queue.py `
  --review-queue data/annotations/search_quality_human_review_queue_v6.csv `
  --primary-pool data/annotations/search_quality_pool_calibrated_v7.csv `
  --comparison-pool data/annotations/search_quality_pool_field_v4.csv `
  --qrels data/annotations/search_quality_qrels_mixed_v6.csv `
  --output data/annotations/search_quality_human_spotcheck_queue_v2.csv `
  --summary-output data/annotations/search_quality_human_spotcheck_queue_v2_summary.csv `
  --prompt-output data/annotations/search_quality_human_spotcheck_queue_v2.md `
  --limit 30 `
  --max-per-query 4
```

This is the next annotation step before changing live ranking. It prioritizes
rows that are most likely to change measured conclusions: top-10 calibrated
results, weak-query results, rank disagreements versus `field_v4`, borderline
assistant labels, source-scope mismatches, wrong-company cases, and rows near
queries already touched by the first human spot-check. The generated
`search_quality_human_spotcheck_queue_v2.md` is formatted for quick chat review
without requiring the reviewer to inspect the full CSV.

Manual spot-check calibration update:

The next chat spot-check rounds are stored as:

- `data/annotations/search_quality_human_spotcheck_v2.csv`
- `data/annotations/search_quality_human_spotcheck_v3.csv`
- `data/annotations/search_quality_human_spotcheck_v4.csv`
- `data/annotations/search_quality_human_spotcheck_v5.csv`

These labels produce `search_quality_qrels_mixed_v10.csv` with 464 judged rows.
The accepted stable calibrated run is:

```powershell
python evaluation/calibrate_search_reranker.py `
  --pool data/annotations/search_quality_pool_field_v4.csv `
  --queries data/annotations/search_quality_queries_v1.csv `
  --qrels data/annotations/search_quality_qrels_mixed_v10.csv `
  --run-output data/exports/search_quality_run_calibrated_v19.csv `
  --pool-output data/annotations/search_quality_pool_calibrated_v19.csv `
  --weights-output data/exports/search_reranker_calibrated_v19_weights.json `
  --cv-output data/exports/search_reranker_calibration_v19_cv.csv `
  --summary-output data/exports/search_reranker_calibration_v19_summary.csv `
  --method web_search_calibrated_v19
```

Accepted metrics on `mixed_v10`:

| Method | Precision@5 | Precision@10 | NDCG@10 | MRR | Top-10 judged |
| --- | ---: | ---: | ---: | ---: | ---: |
| calibrated `web_search_calibrated_v19` | 1.000 | 0.872 | 0.957 | 1.000 | 1.000 |

The calibration script also contains optional section-intent features for
`Item 1A`, MD&A, `Item 2.02`, `Exhibit 99`, `Item 9.01`, financial-statement
sections, non-financial press releases, and energy-themed evidence:

```powershell
python evaluation/calibrate_search_reranker.py `
  --pool data/annotations/search_quality_pool_field_v4.csv `
  --queries data/annotations/search_quality_queries_v1.csv `
  --qrels data/annotations/search_quality_qrels_mixed_v10.csv `
  --run-output data/exports/search_quality_run_calibrated_v20_section.csv `
  --pool-output data/annotations/search_quality_pool_calibrated_v20_section.csv `
  --weights-output data/exports/search_reranker_calibrated_v20_section_weights.json `
  --cv-output data/exports/search_reranker_calibration_v20_section_cv.csv `
  --summary-output data/exports/search_reranker_calibration_v20_section_summary.csv `
  --method web_search_calibrated_v20_section `
  --include-section-features `
  --precision-weight 1.0 `
  --regularization-strength 0.02
```

This candidate is intentionally **not accepted** yet: it reduced cross-validated
NDCG@10 and introduced unjudged top-10 rows. The acceptance record is
`data/exports/search_quality_section_feature_acceptance_v1.csv`. Keeping these
features behind `--include-section-features` prevents a visually plausible but
overfit improvement from silently changing the production calibration.
