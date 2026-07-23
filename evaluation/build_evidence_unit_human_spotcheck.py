"""Build a compact human spot-check queue for evidence-unit ranking labels.

The evidence-unit reranker is already measured with assistant-reviewed qrels.
This helper picks a small set of rows that are most likely to change the metric
conclusion after real human review: calibrated top results, rank disagreements,
promotions, label changes, and borderline labels.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional, Union


VALID_RELEVANCE = {"0", "1", "2", "3"}

OUTPUT_FIELDS = [
    "spotcheck_id",
    "priority",
    "reason",
    "query_id",
    "decision_time",
    "doc_id",
    "evidence_unit_id",
    "parent_doc_id",
    "document_rank",
    "raw_evidence_rank",
    "calibrated_rank",
    "raw_to_calibrated_delta",
    "document_to_calibrated_delta",
    "existing_relevance",
    "suggested_human_relevance",
    "human_relevance",
    "human_notes",
    "title",
    "source",
    "source_type",
    "evidence_unit_type",
    "evidence_unit_claim_type",
    "published_at",
    "available_at",
    "matched_tickers",
    "calibration_delta",
    "calibration_tags",
    "body_excerpt",
    "url",
]

SUMMARY_FIELDS = [
    "query_id",
    "selected_rows",
    "mean_priority",
    "reason_counts",
]


def read_csv(path: Union[str, Path]) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Union[str, Path], rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _clean(value: object) -> str:
    return str(value or "").strip()


def _repair_mojibake(text: object) -> str:
    value = _clean(text)
    replacements = {
        "вЂ™": "'",
        "вЂњ": '"',
        "вЂќ": '"',
        "вЂ“": "-",
        "вЂ”": "-",
        "вЂ¦": "...",
        "В ": " ",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    return value


def _safe_int(value: object, default: int = 9999) -> int:
    try:
        return int(float(_clean(value)))
    except ValueError:
        return default


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(_clean(value))
    except ValueError:
        return default


def _clip(text: str, limit: int = 900) -> str:
    normalized = " ".join(_repair_mojibake(text).split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _has_human_label(row: dict[str, str]) -> bool:
    relevance = _clean(row.get("human_relevance"))
    annotator = _clean(row.get("annotator")).lower()
    label_source = _clean(row.get("label_source")).lower()
    return relevance in VALID_RELEVANCE or annotator in {"human", "user_chat", "reviewer_1"} or label_source.startswith("human_")


def score_spotcheck_candidate(row: dict[str, str]) -> tuple[int, list[str]]:
    """Score one evidence-unit review row for compact human spot-checking."""

    priority = min(_safe_int(row.get("priority"), 0), 180)
    reasons: list[str] = []
    reason_text = _clean(row.get("reason"))
    reason_parts = {part.strip() for part in reason_text.split("|") if part.strip()}

    if _safe_int(row.get("calibrated_rank")) <= 3:
        priority += 35
        reasons.append("calibrated_top3")
    elif _safe_int(row.get("calibrated_rank")) <= 10:
        priority += 20
        reasons.append("calibrated_top10")

    if "promoted_by_calibration" in reason_parts:
        priority += 30
        reasons.append("promoted_by_calibration")
    if "document_unit_rank_disagreement" in reason_parts:
        priority += 25
        reasons.append("document_unit_rank_disagreement")
    if "borderline_label" in reason_parts:
        priority += 18
        reasons.append("borderline_label")

    existing = _clean(row.get("existing_relevance"))
    suggested = _clean(row.get("suggested_human_relevance"))
    if existing in VALID_RELEVANCE and suggested in VALID_RELEVANCE and existing != suggested:
        priority += 35
        reasons.append("assistant_changed_label")
    if suggested in {"1", "2"}:
        priority += 18
        reasons.append("borderline_suggested_label")

    if abs(_safe_int(row.get("raw_to_calibrated_delta"), 0)) >= 3:
        priority += 14
        reasons.append("large_raw_to_calibrated_move")
    if abs(_safe_int(row.get("document_to_calibrated_delta"), 0)) >= 3:
        priority += 10
        reasons.append("large_document_to_calibrated_move")

    calibration_delta = abs(_safe_float(row.get("calibration_delta"), 0.0))
    if calibration_delta >= 0.05:
        priority += 8
        reasons.append("meaningful_calibration_delta")

    claim_type = _clean(row.get("evidence_unit_claim_type")).lower()
    if claim_type in {"risk_factors", "mda", "financial_statements", "rates", "credit"}:
        priority += 6
        reasons.append(f"important_claim:{claim_type}")

    if not reasons:
        reasons.append("coverage_sample")
    return priority, reasons


def build_spotcheck_rows(
    rows: list[dict[str, str]],
    *,
    limit: int = 15,
    max_per_query: int = 4,
) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for row in rows:
        if not _clean(row.get("query_id")) or not _clean(row.get("doc_id")):
            continue
        if _has_human_label(row):
            continue
        priority, reasons = score_spotcheck_candidate(row)
        enriched = dict(row)
        enriched["_spotcheck_priority"] = str(priority)
        enriched["_spotcheck_reason"] = "|".join(reasons)
        candidates.append(enriched)

    candidates.sort(
        key=lambda row: (
            -_safe_int(row.get("_spotcheck_priority"), 0),
            _safe_int(row.get("calibrated_rank")),
            _safe_int(row.get("document_rank")),
            _clean(row.get("query_id")),
            _clean(row.get("doc_id")),
        )
    )

    selected: list[dict[str, str]] = []
    selected_keys: set[tuple[str, str]] = set()
    per_query: Counter[str] = Counter()

    def add_candidate(candidate: dict[str, str]) -> None:
        query_id = _clean(candidate.get("query_id"))
        key = (query_id, _clean(candidate.get("doc_id")))
        if not query_id or key in selected_keys or len(selected) >= limit:
            return
        if per_query[query_id] >= max_per_query:
            return
        selected_keys.add(key)
        per_query[query_id] += 1
        selected.append(candidate)

    # First pass forces broad query coverage.
    seen_queries: set[str] = set()
    for candidate in candidates:
        query_id = _clean(candidate.get("query_id"))
        if query_id in seen_queries:
            continue
        add_candidate(candidate)
        seen_queries.add(query_id)
        if len(selected) >= limit:
            break

    # Second pass fills the remaining budget by priority.
    for candidate in candidates:
        add_candidate(candidate)
        if len(selected) >= limit:
            break

    output: list[dict[str, str]] = []
    for index, row in enumerate(selected, start=1):
        reason = _clean(row.get("_spotcheck_reason")) or _clean(row.get("reason"))
        output.append(
            {
                **row,
                "spotcheck_id": f"eu_spot_{index:04d}",
                "priority": _clean(row.get("_spotcheck_priority")),
                "reason": reason,
                "human_relevance": "",
                "human_notes": _repair_mojibake(row.get("human_notes")),
                "title": _repair_mojibake(row.get("title")),
                "body_excerpt": _clip(row.get("body_excerpt", "")),
            }
        )
    return output


def summarize(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    by_query: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_query[_clean(row.get("query_id"))].append(row)

    summary: list[dict[str, str]] = []
    for query_id in sorted(by_query):
        query_rows = by_query[query_id]
        priorities = [_safe_int(row.get("priority"), 0) for row in query_rows]
        reasons = Counter()
        for row in query_rows:
            reasons.update(part for part in _clean(row.get("reason")).split("|") if part)
        reason_text = "; ".join(f"{key}:{value}" for key, value in sorted(reasons.items()))
        summary.append(
            {
                "query_id": query_id,
                "selected_rows": str(len(query_rows)),
                "mean_priority": f"{sum(priorities) / len(priorities):.1f}" if priorities else "0.0",
                "reason_counts": reason_text,
            }
        )
    return summary


def write_prompt(path: Union[str, Path], rows: list[dict[str, str]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Evidence-unit human spot-check v1",
        "",
        "Fill `human_relevance` with:",
        "",
        "- 3 = highly relevant evidence for the query",
        "- 2 = useful but partial evidence",
        "- 1 = weak fallback evidence",
        "- 0 = irrelevant / wrong source / wrong company",
        "",
        "Quick reply format is enough: `1=3, 2=1, 3=0 ...`.",
        "",
    ]
    for index, row in enumerate(rows, start=1):
        lines.extend(
            [
                f"## {index}. {row.get('query_id', '')}",
                "",
                f"- Spotcheck ID: `{row.get('spotcheck_id', '')}`",
                f"- Suggested: `{row.get('suggested_human_relevance', '')}`; previous: `{row.get('existing_relevance', '')}`",
                f"- Reason: `{row.get('reason', '')}`",
                f"- Ranks: document `{row.get('document_rank', '')}`, raw unit `{row.get('raw_evidence_rank', '')}`, calibrated `{row.get('calibrated_rank', '')}`",
                f"- Claim/source: `{row.get('evidence_unit_claim_type', '')}` / `{row.get('source_type', '')}`",
                f"- Tickers/date: `{row.get('matched_tickers', '')}` / `{row.get('published_at', '')}`",
                f"- Title: {row.get('title', '')}",
                "",
                "Excerpt:",
                "",
                f"> {row.get('body_excerpt', '')}",
                "",
                "Human relevance: ___",
                "",
            ]
        )
    output.write_text("\n".join(lines), encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build compact evidence-unit human spot-check CSV/Markdown.")
    parser.add_argument("--input", required=True, help="Assistant-reviewed evidence-unit queue CSV.")
    parser.add_argument("--output", required=True, help="Output spot-check CSV.")
    parser.add_argument("--summary-output", default="", help="Optional query summary CSV.")
    parser.add_argument("--prompt-output", default="", help="Optional Markdown prompt for chat review.")
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--max-per-query", type=int, default=4)
    args = parser.parse_args(argv)

    rows = build_spotcheck_rows(read_csv(args.input), limit=args.limit, max_per_query=args.max_per_query)
    write_csv(args.output, rows, OUTPUT_FIELDS)
    if args.summary_output:
        write_csv(args.summary_output, summarize(rows), SUMMARY_FIELDS)
    if args.prompt_output:
        write_prompt(args.prompt_output, rows)
    print(f"spotcheck_rows={len(rows)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
