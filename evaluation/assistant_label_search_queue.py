"""Fill search review queue labels with an explicit assistant-review rubric.

This is not a replacement for independent human annotation. It is a reproducible
assistant-reviewed label pass that can unblock ranking experiments while keeping
the label source visible in notes and exported qrels.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from pathlib import Path
from typing import Optional, Union


DOW_BANK_TICKERS = {"JPM", "GS", "AXP", "TRV"}


def _text(row: dict[str, str], *fields: str) -> str:
    return " ".join(str(row.get(field, "") or "") for field in fields).lower()


def _doc_text(row: dict[str, str]) -> str:
    return _text(row, "title", "event_tags", "excerpt", "source_type", "folder_key")


def _tokens(row: dict[str, str]) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", _doc_text(row)))


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _matched_tickers(row: dict[str, str]) -> set[str]:
    raw = str(row.get("matched_tickers", "") or "")
    return {token.upper() for token in re.findall(r"\b[A-Z]{1,5}\b", raw)}


def _is_expected_entity(row: dict[str, str]) -> bool:
    expected = str(row.get("expected_ticker", "") or "").upper()
    if expected == "MARKET":
        return "MARKET" in _matched_tickers(row) or _is_macro(row)
    return expected in _matched_tickers(row)


def _is_macro(row: dict[str, str]) -> bool:
    text = _text(row, "source_type", "folder_key", "event_tags")
    return "official_macro" in text or "macro" in text


def _is_sec(row: dict[str, str]) -> bool:
    text = _text(row, "source_type", "folder_key")
    return "sec_filing" in text or "sec_filings" in text


def _is_company_ir(row: dict[str, str]) -> bool:
    text = _text(row, "source_type", "folder_key", "event_tags")
    return "company_" in text or "company ir" in text or "company official" in text


def _source_matches_scope(row: dict[str, str]) -> bool:
    scope = str(row.get("source_scope", "") or "").lower()
    if scope == "macro":
        return _is_macro(row)
    if scope == "sec_filings":
        return _is_sec(row)
    if scope == "company_ir":
        return _is_company_ir(row)
    return True


def _filing_form_score(row: dict[str, str]) -> tuple[int, str]:
    title = _text(row, "title", "event_tags")
    query = _text(row, "query")
    if "10-q" in query:
        if "10-q" in title:
            return 3, "exact 10-Q filing"
        if any(form in title for form in ("10-k", "8-k")):
            return 2, "same issuer SEC filing but not requested 10-Q"
        return 1, "same issuer but not a direct 10-Q filing"
    if _is_sec(row):
        return 3, "same issuer SEC filing"
    if _is_company_ir(row):
        return 1, "same issuer but not SEC filing"
    return 0, "not a filing source"


def _company_filings_label(row: dict[str, str]) -> tuple[int, str]:
    if not _is_expected_entity(row):
        return 0, "wrong company for filing query"
    return _filing_form_score(row)


def _company_events_label(row: dict[str, str]) -> tuple[int, str]:
    if not _is_expected_entity(row):
        return 0, "wrong company for event query"
    text = _doc_text(row)
    if "8-k" in _text(row, "query"):
        if "8-k" in text:
            return 3, "requested company 8-K/current report"
        if _is_sec(row):
            return 2, "same company SEC filing but not exact 8-K"
        return 1, "same company event source but not SEC 8-K"
    if _contains_any(text, ("earnings release", "earnings guidance", "results of operations", "financial condition", "exhibit 99", "guidance")):
        return 3, "same company earnings/guidance evidence"
    if _contains_any(text, ("management discussion", "mda", "financial statements", "income statement", "operating income")):
        return 2, "same company financial context for guidance query"
    if _is_company_ir(row) or _is_sec(row):
        return 1, "same company event source but weak earnings/guidance match"
    return 0, "wrong source for event query"


def _company_risk_label(row: dict[str, str]) -> tuple[int, str]:
    if not _is_expected_entity(row):
        if row.get("query_id") == "web_banks_credit_cycle" and _matched_tickers(row).intersection(DOW_BANK_TICKERS):
            return 2, "bank-sector credit evidence but not target bank"
        return 0, "wrong company for risk query"
    text = _doc_text(row)
    if _contains_any(text, ("risk factors", "item 1a", "litigation", "legal proceedings", "lawsuit", "regulatory", "credit risk", "company risk", "supply chain")):
        return 3, "direct risk evidence for target company"
    if _contains_any(text, ("market risk", "management discussion", "mda", "financial condition", "credit", "debt")):
        return 2, "risk-adjacent target-company filing"
    if _is_sec(row) or _is_company_ir(row):
        return 1, "target-company document with weak risk match"
    return 0, "wrong source for risk query"


def _company_fundamentals_label(row: dict[str, str]) -> tuple[int, str]:
    if not _is_expected_entity(row):
        return 0, "wrong company for fundamentals query"
    text = _doc_text(row)
    if _contains_any(text, ("margin", "operating income", "income statement", "segment revenue", "earnings release", "results of operations", "financial statements", "mda", "management discussion", "cloud")):
        return 3, "target-company financial or margin evidence"
    if _contains_any(text, ("10-k", "10-q", "8-k", "financial condition", "market risk")):
        return 2, "target-company SEC filing with indirect fundamentals evidence"
    if _is_sec(row) or _is_company_ir(row):
        return 1, "target-company document with weak fundamentals match"
    return 0, "wrong source for fundamentals query"


def _company_macro_label(row: dict[str, str]) -> tuple[int, str]:
    text = _doc_text(row)
    expected = _is_expected_entity(row)
    topical = _contains_any(text, ("energy", "oil", "demand", "consumer", "spending", "payment", "revenue", "earnings", "macro"))
    if expected and topical:
        return 3, "target-company macro-sensitive evidence"
    if expected and (_is_sec(row) or _is_company_ir(row)):
        return 2, "target-company source with indirect macro exposure"
    if _is_macro(row) and topical:
        return 2, "macro source relevant to company exposure"
    return 0, "wrong entity or weak macro match"


def _portfolio_sector_label(row: dict[str, str]) -> tuple[int, str]:
    text = _doc_text(row)
    qid = row.get("query_id", "")
    tickers = _matched_tickers(row)
    if qid == "web_banks_credit_cycle":
        if "JPM" in tickers and _contains_any(text, ("credit", "risk", "financial", "10-k", "10-q", "bank")):
            return 3, "target bank credit-cycle evidence"
        if tickers.intersection(DOW_BANK_TICKERS) and _contains_any(text, ("credit", "risk", "financial", "10-k", "10-q", "bank")):
            return 2, "bank-sector credit-cycle evidence"
        return 0, "not bank credit-cycle evidence"
    if qid == "web_it_real_yields":
        if _is_macro(row) and _contains_any(text, ("yield", "rate", "treasury", "spread")):
            return 3, "rate/yield evidence relevant to growth stocks"
        if _is_macro(row):
            return 2, "macro evidence indirectly relevant to growth stocks"
        return 0, "not real-yields evidence"
    return _portfolio_macro_label(row)


def _macro_label(row: dict[str, str]) -> tuple[int, str]:
    if not _is_macro(row):
        return 0, "not an official macro source"
    qid = row.get("query_id", "")
    text = _doc_text(row)
    direct_terms = {
        "web_10y_2y_spread": ("10-year minus 2-year", "10 year 2 year", "t10y2y", "yield curve", "2-year"),
        "web_yield_curve": ("yield curve", "10-year minus 2-year", "10-year minus 3-month", "spread", "treasury"),
        "web_vix": ("vix", "volatility"),
        "web_high_yield_spread": ("high yield", "credit spread", "bofa", "spread"),
        "web_wti_oil": ("wti", "crude", "oil"),
        "web_unemployment": ("unemployment", "employment", "labor"),
        "web_inflation": ("inflation", "cpi", "consumer price"),
    }
    if qid in direct_terms and _contains_any(text, direct_terms[qid]):
        return 3, "direct official macro series match"
    if qid == "web_inflation" and _contains_any(text, ("oil", "wti", "energy")):
        return 2, "inflation-adjacent price-pressure series"
    if qid == "web_unemployment" and _contains_any(text, ("demand", "consumer", "growth")):
        return 2, "growth-demand macro proxy"
    return 1, "official macro source but weak query-specific match"


def _portfolio_macro_label(row: dict[str, str]) -> tuple[int, str]:
    text = _doc_text(row)
    qid = row.get("query_id", "")
    if not (_is_macro(row) or _is_sec(row) or _is_company_ir(row)):
        return 0, "unsupported source for portfolio macro query"
    if qid == "web_portfolio_rates":
        if _is_macro(row) and _contains_any(text, ("rate", "yield", "treasury", "spread")):
            return 3, "direct rates evidence for portfolio risk"
        return 1, "weak portfolio-rates match"
    if qid == "web_consumer_demand":
        if _contains_any(text, ("consumer", "demand", "spending", "retail", "unemployment", "employment")):
            return 3 if _is_macro(row) else 2, "consumer-demand evidence"
        if _is_macro(row):
            return 1, "macro source with weak demand match"
        return 0, "not consumer-demand evidence"
    if qid == "web_earnings_risk":
        if _contains_any(text, ("vix", "risk appetite", "credit spread", "yield", "earnings", "financial condition")):
            return 3 if _is_macro(row) else 2, "earnings/risk-appetite evidence"
        return 1 if _is_macro(row) else 0, "weak earnings-risk match"
    return _macro_label(row) if _is_macro(row) else (1, "portfolio macro query with non-macro source")


def label_row(row: dict[str, str]) -> tuple[int, str]:
    intent = str(row.get("intent", "") or "").lower()
    qid = row.get("query_id", "")
    if intent == "company_filings":
        return _company_filings_label(row)
    if intent == "company_events":
        return _company_events_label(row)
    if intent == "company_risk":
        return _company_risk_label(row)
    if intent == "company_fundamentals":
        return _company_fundamentals_label(row)
    if intent == "company_macro":
        return _company_macro_label(row)
    if intent == "portfolio_sector":
        return _portfolio_sector_label(row)
    if intent == "portfolio_macro":
        return _portfolio_macro_label(row)
    if intent.startswith("macro_") or qid.startswith("web_10y") or qid.startswith("web_wti"):
        return _macro_label(row)
    return (1 if _source_matches_scope(row) else 0), "generic source-scope match"


def label_rows(rows: list[dict[str, str]], *, overwrite: bool = False) -> list[dict[str, str]]:
    labeled: list[dict[str, str]] = []
    for row in rows:
        updated = dict(row)
        if overwrite or not str(updated.get("human_relevance", "") or "").strip():
            relevance, reason = label_row(updated)
            updated["human_relevance"] = str(relevance)
            existing_note = str(updated.get("reviewer_notes", "") or "").strip()
            note = f"assistant_review_v1: {reason}"
            updated["reviewer_notes"] = note if overwrite else (f"{existing_note} | {note}" if existing_note else note)
        labeled.append(updated)
    return labeled


def read_csv(path: Union[str, Path]) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Union[str, Path], rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Fill human_relevance in a search review queue with assistant-reviewed labels.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    rows = read_csv(args.input)
    labeled = label_rows(rows, overwrite=args.overwrite)
    fieldnames = list(rows[0].keys()) if rows else []
    write_csv(args.output, labeled, fieldnames)
    counts = Counter(row.get("human_relevance", "") for row in labeled)
    print(f"labeled_rows={len(labeled)} counts={dict(sorted(counts.items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
