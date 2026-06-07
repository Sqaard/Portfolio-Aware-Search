"""Create deterministic bootstrap qrels for the web search quality pool.

These labels are not a replacement for human judgments. They are a fast,
auditable development set that lets ranking changes be measured before a
manual review pass exists.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional, Union


QREL_FIELDS = ["query_id", "doc_id", "relevance", "label_source", "annotator", "notes"]

SUMMARY_FIELDS = [
    "query_id",
    "rows",
    "rel_3",
    "rel_2",
    "rel_1",
    "rel_0",
    "mean_relevance",
]

SEC_HINTS = (
    "10-k",
    "10k",
    "10-q",
    "10q",
    "8-k",
    "8k",
    "filing",
    "sec",
    "item ",
    "mda",
    "md&a",
    "financial statements",
)

RISK_HINTS = (
    "risk",
    "risk factors",
    "litigation",
    "lawsuit",
    "legal",
    "legal proceedings",
    "regulatory",
    "market risk",
    "credit risk",
    "supply chain",
)

EVENT_HINTS = (
    "earnings",
    "guidance",
    "8-k",
    "current report",
    "press release",
    "investor material",
    "presentation",
    "product",
    "launch",
    "dividend",
    "buyback",
)

FUNDAMENTAL_HINTS = (
    "revenue",
    "sales",
    "margin",
    "cloud",
    "financial statements",
    "mda",
    "md&a",
    "operating income",
    "net income",
    "eps",
    "debt",
    "cash flow",
    "gross profit",
)

MACRO_HINTS = (
    "macro",
    "fed",
    "fomc",
    "treasury",
    "yield",
    "rate",
    "rates",
    "inflation",
    "cpi",
    "pce",
    "spread",
    "vix",
    "volatility",
    "oil",
    "unemployment",
    "payrolls",
    "consumer",
    "demand",
    "housing",
    "recession",
)


def _split_pipe(value: str) -> set[str]:
    return {part.strip().upper() for part in str(value or "").split("|") if part.strip()}


def _text(row: dict[str, str]) -> str:
    parts = [
        row.get("query", ""),
        row.get("title", ""),
        row.get("group_title", ""),
        row.get("folder_title", ""),
        row.get("source_type", ""),
        row.get("event_tags", ""),
        row.get("risk_terms", ""),
        row.get("excerpt", ""),
    ]
    return " ".join(str(part or "") for part in parts).lower()


def _has_any(text: str, hints: tuple[str, ...]) -> bool:
    return any(hint in text for hint in hints)


def _source_family(row: dict[str, str]) -> str:
    folder_key = str(row.get("folder_key", "") or "").strip().lower()
    source_type = str(row.get("source_type", "") or "").strip().lower()
    if folder_key:
        return folder_key
    if source_type.startswith("sec_filing"):
        return "sec_filings"
    if source_type.startswith("official_macro"):
        return "macro"
    if source_type.startswith("company_"):
        return "company_ir"
    if "news" in source_type or "headline" in source_type:
        return "news"
    return "other_sources"


def _is_company_ir_like_sec_exhibit(row: dict[str, str], text: str) -> bool:
    return (
        str(row.get("source_type", "") or "").lower() == "sec_filing_exhibit"
        and _has_any(text, ("earnings", "guidance", "press release", "investor material", "presentation"))
    )


def _source_matches(row: dict[str, str], text: str) -> tuple[bool, list[str]]:
    source_scope = str(row.get("source_scope", "") or "").strip().lower()
    family = _source_family(row)
    reasons: list[str] = []
    if source_scope == "sec_filings":
        match = family == "sec_filings"
    elif source_scope == "company_ir":
        match = family == "company_ir" or _is_company_ir_like_sec_exhibit(row, text)
    elif source_scope == "macro":
        match = family == "macro"
    else:
        match = family in {"sec_filings", "company_ir", "macro"}
    if match:
        reasons.append(f"source:{family}")
    return match, reasons


def _entity_matches(row: dict[str, str]) -> tuple[bool, list[str]]:
    expected = str(row.get("expected_ticker", "") or "").strip().upper()
    reasons: list[str] = []
    if not expected:
        return True, ["entity:any"]
    matched = _split_pipe(row.get("matched_tickers", "")) | _split_pipe(row.get("matched_holdings", ""))
    if expected == "MARKET":
        if "MARKET" in matched or _source_family(row) == "macro":
            return True, ["entity:market"]
        return False, []
    title_text = f"{row.get('title', '')} {row.get('group_title', '')}".upper()
    if expected in matched or re.search(rf"(?<![A-Z0-9]){re.escape(expected)}(?![A-Z0-9])", title_text):
        reasons.append(f"entity:{expected}")
        return True, reasons
    return False, []


def _intent_matches(row: dict[str, str], text: str) -> tuple[bool, list[str]]:
    intent = str(row.get("intent", "") or "").strip().lower()
    family = _source_family(row)
    reasons: list[str] = []

    if intent == "company_filings":
        match = family == "sec_filings" and _has_any(text, SEC_HINTS)
    elif intent == "company_risk":
        match = family == "sec_filings" and _has_any(text, RISK_HINTS)
    elif intent == "company_events":
        match = family in {"sec_filings", "company_ir"} and _has_any(text, EVENT_HINTS)
    elif intent == "company_fundamentals":
        match = family in {"sec_filings", "company_ir"} and _has_any(text, FUNDAMENTAL_HINTS)
    elif intent == "company_macro":
        match = family in {"sec_filings", "company_ir", "macro"} and _has_any(text, MACRO_HINTS + FUNDAMENTAL_HINTS)
    elif intent.startswith("macro_"):
        match = family == "macro" and _has_any(text, MACRO_HINTS)
    elif intent in {"portfolio_macro", "portfolio_sector"}:
        match = family == "macro" or _has_any(text, MACRO_HINTS)
    else:
        match = _has_any(text, SEC_HINTS + EVENT_HINTS + MACRO_HINTS + FUNDAMENTAL_HINTS)

    if match:
        reasons.append(f"intent:{intent or 'general'}")
    return match, reasons


def grade_pool_row(row: dict[str, str]) -> dict[str, str]:
    text = _text(row)
    source_match, source_reasons = _source_matches(row, text)
    entity_match, entity_reasons = _entity_matches(row)
    intent_match, intent_reasons = _intent_matches(row, text)
    reasons = source_reasons + entity_reasons + intent_reasons

    positive_signals = int(source_match) + int(entity_match) + int(intent_match)
    if source_match and entity_match and intent_match:
        relevance = 3
    elif positive_signals >= 2:
        relevance = 2
    elif positive_signals == 1:
        relevance = 1
    else:
        relevance = 0

    expected = str(row.get("expected_ticker", "") or "").strip().upper()
    if expected and expected != "MARKET" and not entity_match:
        relevance = min(relevance, 1)
        if relevance:
            reasons.append("penalty:wrong_entity")
    if str(row.get("source_scope", "") or "").strip().lower() and not source_match:
        relevance = min(relevance, 1)
        if relevance:
            reasons.append("penalty:wrong_source")

    return {
        "query_id": row.get("query_id", ""),
        "doc_id": row.get("doc_id", ""),
        "relevance": str(relevance),
        "label_source": "bootstrap_search_intent_v1",
        "annotator": "rules",
        "notes": ";".join(reasons),
    }


def read_csv(path: Union[str, Path]) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Union[str, Path], rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def summarize_qrels(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        grouped[row["query_id"]].append(int(row["relevance"]))

    summary: list[dict[str, str]] = []
    for query_id, labels in sorted(grouped.items()):
        counts = Counter(labels)
        total = len(labels)
        mean = sum(labels) / total if total else 0.0
        summary.append(
            {
                "query_id": query_id,
                "rows": str(total),
                "rel_3": str(counts.get(3, 0)),
                "rel_2": str(counts.get(2, 0)),
                "rel_1": str(counts.get(1, 0)),
                "rel_0": str(counts.get(0, 0)),
                "mean_relevance": f"{mean:.4f}",
            }
        )
    return summary


def build_qrels(pool_rows: list[dict[str, str]], max_rows: int = 0) -> list[dict[str, str]]:
    rows = pool_rows[:max_rows] if max_rows and max_rows > 0 else pool_rows
    seen: set[tuple[str, str]] = set()
    qrels: list[dict[str, str]] = []
    for row in rows:
        key = (str(row.get("query_id", "")), str(row.get("doc_id", "")))
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        qrels.append(grade_pool_row(row))
    return qrels


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build bootstrap qrels for the web search quality pool.")
    parser.add_argument("--input", required=True, help="Input search quality pool CSV.")
    parser.add_argument("--output", required=True, help="Output qrels CSV.")
    parser.add_argument("--summary-output", default="", help="Optional per-query label summary CSV.")
    parser.add_argument(
        "--max-rows",
        type=int,
        default=0,
        help="Optional first-N rows limit. Default labels every row in the pool.",
    )
    args = parser.parse_args(argv)

    qrels = build_qrels(read_csv(args.input), max_rows=args.max_rows)
    write_csv(args.output, qrels, QREL_FIELDS)
    if args.summary_output:
        write_csv(args.summary_output, summarize_qrels(qrels), SUMMARY_FIELDS)
    print(f"wrote_qrels={len(qrels)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
