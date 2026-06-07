# Annotation Guide

This project evaluates retrieval quality with query-document relevance labels.

## Label Scale

- `0`: irrelevant to the portfolio decision.
- `1`: mentions a holding or context but is not decision-useful.
- `2`: useful evidence.
- `3`: highly relevant and timely for the portfolio decision.

## What To Judge

For each `query_id/doc_id`, judge whether the document would be useful evidence
for the portfolio at the specified decision time.

Consider:

- portfolio holdings and weights;
- direct ticker or company relevance;
- sector and macro relevance;
- event importance;
- whether the document was available before `decision_time`.

Do not reward documents that are only relevant with future knowledge.

## Workflow

1. Run retrieval ablations.
2. Build an annotation pool:

```powershell
python evaluation/build_annotation_pool.py `
  --input data/exports/ablation_batch_sample/ablation_retrieved_all.jsonl `
  --qrels data/annotations/bootstrap_sample_qrels.csv `
  --output data/annotations/annotation_pool_batch_sample.csv
```

3. Fill the empty `relevance`, `label_source`, `annotator`, and `notes` columns.
   Start with rows marked `high_missing_label` in `review_priority`.
4. Save human labels as a separate qrels file, for example:
   `data/annotations/human_qrels_v1.csv`.
5. Export reviewed labels from the pool:

```powershell
python evaluation/export_qrels_from_pool.py `
  --input data/annotations/annotation_pool_batch_sample.csv `
  --output data/annotations/human_qrels_v1.csv `
  --issues-output data/annotations/human_qrels_v1_export_issues.csv `
  --label-source human_v1 `
  --strict
```

6. Validate the labels:

```powershell
python evaluation/validate_qrels.py `
  --qrels data/annotations/human_qrels_v1.csv `
  --run data/exports/ablation_batch_sample/ablation_run.csv `
  --output data/exports/ablation_batch_sample/human_qrels_v1_validation.csv
```

7. Evaluate retrieval methods against the validated human qrels.

Bootstrap labels are useful for development only. They should not be presented
as final retrieval quality evidence.
