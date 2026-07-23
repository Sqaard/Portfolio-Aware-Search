"""Seed document-vs-evidence-unit comparison labels with an explicit rubric.

These labels are development qrels, not independent human evaluation. The goal
is to unblock retrieval-grain experiments while keeping provenance visible.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from typing import Optional, Union

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.build_annotation_pool import POOL_FIELDS, write_pool  # noqa: E402


LABEL_SOURCE = "assistant_document_vs_evidence_v1"
ANNOTATOR = "codex_assistant"


def _text(row: dict[str, str], *fields: str) -> str:
    return " ".join(str(row.get(field, "") or "") for field in fields).lower()


def _filed_year(row: dict[str, str]) -> int:
    match = re.search(r"filed\s+(\d{4})-\d{2}-\d{2}", str(row.get("title", "")), flags=re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _decision_year(row: dict[str, str]) -> int:
    text = " ".join(str(row.get(field, "") or "") for field in ("decision_time", "query_id"))
    match = re.search(r"(20\d{2})-\d{2}-\d{2}", text)
    return int(match.group(1)) if match else 2022


def _company(row: dict[str, str]) -> str:
    doc_id = str(row.get("doc_id", "")).lower()
    title = str(row.get("title", "")).lower()
    if "aapl" in doc_id or "apple" in title:
        return "AAPL"
    if "jpm" in doc_id or "jpmorgan" in title:
        return "JPM"
    if "unh" in doc_id or "unitedhealth" in title:
        return "UNH"
    if "msft" in doc_id or "microsoft" in title:
        return "MSFT"
    return ""


def _doc_kind(row: dict[str, str]) -> str:
    text = _text(row, "doc_id", "title", "body_excerpt")
    if "risk_factors" in text or "risk factors" in text or "item 1a" in text:
        return "risk_factors"
    if "earnings release" in text or "exhibit 99" in text or "guidance" in text:
        return "earnings_release"
    if "item_7_mda" in text or "item 7 management" in text or "part1_item_2_mda" in text or "management's discussion" in text or "management’s discussion" in text:
        return "mda"
    if "financial_statements" in text or "financial statements" in text or "item 8" in text:
        return "financial_statements"
    if "item_1_business" in text or "item 1 business" in text:
        return "business"
    return "filing_section"


def _freshness_bucket(year: int, decision_year: int) -> str:
    if year >= decision_year - 1:
        return "fresh"
    if year >= decision_year - 3:
        return "recent"
    if year >= decision_year - 6:
        return "old"
    return "stale"


def _base_relevance_for_kind(kind: str) -> int:
    if kind in {"earnings_release", "risk_factors"}:
        return 3
    if kind == "mda":
        return 2
    if kind in {"financial_statements", "business"}:
        return 1
    return 1


def _apply_freshness(value: int, year: int, decision_year: int) -> int:
    if value <= 0:
        return 0
    if year >= decision_year - 1:
        return value
    if year >= decision_year - 3:
        return min(value, 2)
    if year >= decision_year - 6:
        return max(min(value, 2), 1)
    return min(value, 1)


def _query_family(query_id: str) -> str:
    lowered = query_id.lower()
    if "tech_rates" in lowered:
        return "tech_rates"
    if "banks_macro" in lowered:
        return "banks_macro"
    if "health_macro" in lowered:
        return "health_macro"
    return "balanced"


def label_pool_row(row: dict[str, str]) -> tuple[int, str]:
    query_id = str(row.get("query_id", ""))
    company = _company(row)
    kind = _doc_kind(row)
    year = _filed_year(row)
    decision_year = _decision_year(row)
    freshness = _freshness_bucket(year, decision_year)
    family = _query_family(query_id)

    if family == "balanced":
        if company not in {"AAPL", "MSFT", "JPM", "UNH"}:
            return 0, "wrong company for balanced portfolio query"
        relevance = _apply_freshness(_base_relevance_for_kind(kind), year, decision_year)
        if company == "AAPL" and kind == "earnings_release" and year >= decision_year - 1:
            relevance = 3
        return relevance, f"{company} {kind} evidence for balanced portfolio; freshness:{freshness}"

    if family == "tech_rates":
        if company not in {"AAPL", "MSFT"}:
            return 0, "wrong company for tech-heavy portfolio query"
        relevance = _apply_freshness(_base_relevance_for_kind(kind), year, decision_year)
        if kind == "earnings_release" and year >= decision_year - 1:
            relevance = 3
        elif kind == "earnings_release":
            relevance = max(relevance, 2)
        elif kind == "business":
            relevance = min(relevance, 1)
        return relevance, f"{company} tech-position evidence; rates dimension not directly covered; freshness:{freshness}"

    if family == "banks_macro":
        if company != "JPM":
            return 0, "wrong company for bank-heavy portfolio query"
        if kind == "risk_factors" and year >= decision_year - 1:
            return 3, "fresh JPM risk-factor evidence for bank credit/macro query"
        if kind == "earnings_release" and year >= decision_year - 1:
            return 3, "fresh JPM earnings/investor evidence for bank credit/macro query"
        relevance = _apply_freshness(_base_relevance_for_kind(kind), year, decision_year)
        if kind == "business" and year >= decision_year - 1:
            relevance = 2
        return relevance, f"JPM {kind} evidence for bank portfolio; freshness:{freshness}"

    if family == "health_macro":
        if company != "UNH":
            return 0, "wrong company for health/macro portfolio query"
        if kind == "mda" and year >= decision_year - 1:
            return 3, "fresh UNH MD&A evidence for healthcare portfolio"
        relevance = _apply_freshness(_base_relevance_for_kind(kind), year, decision_year)
        if kind == "business" and year >= decision_year - 1:
            relevance = 2
        return relevance, f"UNH {kind} evidence for healthcare portfolio; freshness:{freshness}"

    return 0, "unknown comparison query id"


def label_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    labeled: list[dict[str, str]] = []
    for row in rows:
        output = dict(row)
        relevance, note = label_pool_row(row)
        output["relevance"] = str(relevance)
        output["label_source"] = LABEL_SOURCE
        output["annotator"] = ANNOTATOR
        existing_notes = str(output.get("notes", "") or "").strip()
        output["notes"] = f"{existing_notes} | {note}".strip(" |")
        labeled.append(output)
    return labeled


def read_pool(path: Union[str, Path]) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Assistant-label document-vs-evidence-unit comparison pool.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    labeled = label_rows(read_pool(args.input))
    write_pool(args.output, labeled)
    counts: dict[str, int] = {}
    for row in labeled:
        counts[row["relevance"]] = counts.get(row["relevance"], 0) + 1
    print({"rows": len(labeled), "label_source": LABEL_SOURCE, "relevance_counts": dict(sorted(counts.items()))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
