"""Assistant-review an evidence-unit held-out review queue.

This fills `suggested_human_relevance` for development only. It deliberately
does not write `human_relevance`, because these labels are not independent
human judgments.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Optional, Union

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.build_evidence_unit_review_queue import REVIEW_FIELDS, write_csv


LABEL_SOURCE = "assistant_evidence_unit_review_v1"
ANNOTATOR = "codex_assistant"


def _text(row: dict[str, str], *fields: str) -> str:
    return " ".join(str(row.get(field, "") or "") for field in fields).lower()


def _query_family(row: dict[str, str]) -> str:
    query_id = str(row.get("query_id", "")).lower()
    if "tech_rates" in query_id:
        return "tech_rates"
    if "banks_macro" in query_id:
        return "banks_macro"
    if "health_macro" in query_id:
        return "health_macro"
    return "balanced"


def _company(row: dict[str, str]) -> str:
    text = _text(row, "doc_id", "title", "matched_tickers")
    if "aapl" in text or "apple" in text:
        return "AAPL"
    if "msft" in text or "microsoft" in text:
        return "MSFT"
    if "jpm" in text or "jpmorgan" in text:
        return "JPM"
    if "unh" in text or "unitedhealth" in text:
        return "UNH"
    return ""


def _year_from(value: str) -> int:
    match = re.search(r"(20\d{2}|19\d{2})-\d{2}-\d{2}", value or "")
    return int(match.group(1)) if match else 0


def _published_year(row: dict[str, str]) -> int:
    return _year_from(str(row.get("published_at", ""))) or _year_from(str(row.get("title", "")))


def _decision_year(row: dict[str, str]) -> int:
    return _year_from(str(row.get("decision_time", ""))) or _year_from(str(row.get("query_id", ""))) or 2022


def _freshness(row: dict[str, str]) -> str:
    year = _published_year(row)
    decision_year = _decision_year(row)
    if not year:
        return "unknown"
    if year >= decision_year - 1:
        return "fresh"
    if year >= decision_year - 3:
        return "recent"
    if year >= decision_year - 6:
        return "old"
    return "stale"


def _kind(row: dict[str, str]) -> str:
    claim = str(row.get("evidence_unit_claim_type", "") or "").lower()
    text = _text(row, "doc_id", "title", "body_excerpt")
    if claim:
        if claim == "filing_exhibit":
            return "earnings_release" if ("earnings" in text or "guidance" in text or "investor" in text) else "filing_exhibit"
        return claim
    if "risk_factors" in text or "risk factors" in text or "item 1a" in text:
        return "risk_factors"
    if "legal proceedings" in text:
        return "legal_proceedings"
    if "earnings release" in text or "guidance" in text or "exhibit 99" in text:
        return "earnings_release"
    if "item_7_mda" in text or "management" in text and "discussion" in text:
        return "mda"
    if "financial statements" in text:
        return "financial_statements"
    if "item 1 business" in text or "item_1_business" in text:
        return "business"
    return "filing_section"


def _base_relevance(kind: str) -> int:
    if kind in {"risk_factors", "earnings_release"}:
        return 3
    if kind in {"mda", "legal_proceedings"}:
        return 2
    if kind in {"financial_statements", "business", "filing_exhibit"}:
        return 1
    return 1


def _apply_freshness(value: int, freshness: str, *, allow_old_useful: bool = True) -> int:
    if value <= 0:
        return 0
    if freshness == "fresh":
        return value
    if freshness == "recent":
        return min(value, 2)
    if freshness == "old":
        return min(value, 2) if allow_old_useful else min(value, 1)
    if freshness == "stale":
        return min(value, 1)
    return min(value, 1)


def review_row(row: dict[str, str]) -> tuple[int, str]:
    family = _query_family(row)
    company = _company(row)
    kind = _kind(row)
    freshness = _freshness(row)

    if family == "balanced":
        if company not in {"AAPL", "MSFT", "JPM", "UNH"}:
            return 0, "wrong company for balanced portfolio"
        value = _apply_freshness(_base_relevance(kind), freshness)
        return value, f"{company} {kind}; balanced portfolio evidence; freshness:{freshness}"

    if family == "tech_rates":
        if company not in {"AAPL", "MSFT"}:
            return 0, "wrong company for tech/rates portfolio"
        value = _apply_freshness(_base_relevance(kind), freshness)
        if kind == "business":
            value = min(value, 1)
        if kind in {"risk_factors", "earnings_release"} and freshness in {"fresh", "recent"}:
            value = max(value, 2)
        return value, f"{company} {kind}; tech holding evidence, rates not directly measured; freshness:{freshness}"

    if family == "banks_macro":
        if company != "JPM":
            return 0, "wrong company for bank/macro portfolio"
        value = _apply_freshness(_base_relevance(kind), freshness, allow_old_useful=False)
        if kind in {"risk_factors", "earnings_release"} and freshness in {"fresh", "recent"}:
            value = 3
        elif kind in {"mda", "business"} and freshness == "fresh":
            value = max(value, 2)
        return value, f"JPM {kind}; bank credit/macro evidence; freshness:{freshness}"

    if family == "health_macro":
        if company != "UNH":
            return 0, "wrong company for health/macro portfolio"
        value = _apply_freshness(_base_relevance(kind), freshness)
        if kind in {"mda", "risk_factors", "earnings_release"} and freshness == "fresh":
            value = 3
        elif kind == "business" and freshness == "fresh":
            value = 2
        return value, f"UNH {kind}; healthcare portfolio evidence; freshness:{freshness}"

    return 0, "unknown query family"


def label_rows(rows: list[dict[str, str]], *, overwrite: bool = False) -> list[dict[str, str]]:
    labeled: list[dict[str, str]] = []
    for row in rows:
        updated = dict(row)
        if overwrite or not str(updated.get("suggested_human_relevance", "") or "").strip():
            relevance, note = review_row(updated)
            old_label = str(updated.get("existing_relevance", "") or "").strip()
            updated["suggested_human_relevance"] = str(relevance)
            updated["human_notes"] = (
                f"{LABEL_SOURCE}: {note}; previous_assistant_label:{old_label}; "
                f"review_reason:{updated.get('reason', '')}"
            )
        labeled.append(updated)
    return labeled


def read_csv(path: Union[str, Path]) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Assistant-review evidence-unit held-out queue.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    rows = label_rows(read_csv(args.input), overwrite=args.overwrite)
    write_csv(args.output, rows, REVIEW_FIELDS)
    counts = Counter(row.get("suggested_human_relevance", "") for row in rows)
    print(
        {
            "rows": len(rows),
            "label_source": LABEL_SOURCE,
            "annotator": ANNOTATOR,
            "suggested_relevance_counts": dict(sorted(counts.items())),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
