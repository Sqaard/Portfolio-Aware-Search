"""Build decision-grade evidence units from normalized financial documents.

The output remains compatible with ``FinancialDocument`` so the current index
and ranker can consume it immediately, while downstream tools get explicit
evidence-unit metadata.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finportfolio_ir.io_utils import read_jsonl, write_jsonl  # noqa: E402
from finportfolio_ir.text_utils import stable_content_hash, stable_document_hash  # noqa: E402


SEC_SECTION_FIELDS = [
    "section_id",
    "sec_section_id",
    "sec_section_code",
    "sec_section_title",
    "sec_section_ordinal",
    "sec_section_start_char",
    "sec_section_end_char",
    "sec_section_chars",
    "section_truncated",
    "sec_form",
    "sec_accession_number",
    "sec_exhibit_id",
    "sec_exhibit_name",
    "sec_exhibit_url",
    "sec_exhibit_size",
    "sec_exhibit_last_modified",
]

MACRO_FIELDS = [
    "macro_series_id",
    "macro_series_title",
    "macro_family",
    "macro_frequency",
    "macro_observation_date",
    "macro_value",
    "macro_units",
    "macro_release_lag_days",
]

COMPANY_IR_SOURCE_TYPES = {
    "company_ir",
    "company_press_release",
    "company_earnings_release",
    "company_financial_report",
    "company_report",
    "company_presentation",
}

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


def _copy_base(record: dict[str, Any]) -> dict[str, Any]:
    return dict(record)


def _append_tag(values: Iterable[Any], tag: str) -> list[str]:
    output = [str(value) for value in values if str(value)]
    if tag not in output:
        output.append(tag)
    return output


def _unit_hash_id(parent_doc_id: str, unit_type: str, index: int, body: str) -> str:
    digest = stable_content_hash({"title": parent_doc_id, "body": body})[:12]
    return f"{parent_doc_id}__eu_{unit_type}_{index:03d}_{digest}"


def _finalize_unit(
    unit: dict[str, Any],
    *,
    parent: dict[str, Any],
    unit_id: str,
    unit_type: str,
    unit_index: int,
    claim_type: str,
    title: str,
    body: str,
) -> dict[str, Any]:
    unit["doc_id"] = unit_id
    unit["evidence_unit_id"] = unit_id
    unit["parent_doc_id"] = str(parent.get("parent_doc_id") or parent.get("doc_id") or "")
    unit["evidence_unit_type"] = unit_type
    unit["evidence_unit_index"] = unit_index
    unit["evidence_unit_claim_type"] = claim_type
    unit["title"] = title
    unit["body"] = " ".join(str(body or "").split())
    unit["event_tags"] = _append_tag(parent.get("event_tags", []) or [], unit_type)
    unit["duplicate_cluster_id"] = f"{parent.get('duplicate_cluster_id') or parent.get('doc_id')}:{unit_type}:{unit_index}"
    unit["document_hash"] = stable_document_hash(unit)
    return unit


def _sec_claim_type(record: dict[str, Any]) -> str:
    section_id = str(record.get("sec_section_id") or record.get("section_id") or "").lower()
    source_type = str(record.get("source_type", "")).lower()
    if "risk_factors" in section_id:
        return "risk_factors"
    if "mda" in section_id:
        return "mda"
    if "financial_statements" in section_id:
        return "financial_statements"
    if "market_risk" in section_id:
        return "market_risk"
    if "legal" in section_id:
        return "legal_proceedings"
    if "exhibit" in source_type or section_id.startswith("exhibit_"):
        return "filing_exhibit"
    return "filing_section"


def _sec_unit(record: dict[str, Any]) -> dict[str, Any]:
    source_type = str(record.get("source_type", "")).lower()
    unit_type = "sec_exhibit" if source_type == "sec_filing_exhibit" else "sec_section"
    unit = _copy_base(record)
    parent_doc_id = str(record.get("parent_doc_id") or record.get("doc_id") or "")
    unit_id = str(record.get("doc_id") or _unit_hash_id(parent_doc_id, unit_type, 1, str(record.get("body", ""))))
    for field in SEC_SECTION_FIELDS:
        if field in record:
            unit[field] = record[field]
    return _finalize_unit(
        unit,
        parent=record,
        unit_id=unit_id,
        unit_type=unit_type,
        unit_index=int(record.get("sec_section_ordinal") or 1),
        claim_type=_sec_claim_type(record),
        title=str(record.get("title", "")),
        body=str(record.get("body", "")),
    )


def _macro_unit(record: dict[str, Any]) -> dict[str, Any]:
    unit = _copy_base(record)
    for field in MACRO_FIELDS:
        if field in record:
            unit[field] = record[field]
    unit_id = str(record.get("doc_id") or _unit_hash_id(str(record.get("macro_series_id", "macro")), "macro_observation", 1, str(record.get("body", ""))))
    return _finalize_unit(
        unit,
        parent=record,
        unit_id=unit_id,
        unit_type="macro_observation",
        unit_index=1,
        claim_type=str(record.get("macro_family") or "macro"),
        title=str(record.get("title", "")),
        body=str(record.get("body", "")),
    )


def _paragraphs(text: str) -> list[str]:
    raw = str(text or "").replace("\r\n", "\n")
    paragraphs = [" ".join(part.split()) for part in re.split(r"\n\s*\n+", raw) if part.strip()]
    if len(paragraphs) > 1:
        return paragraphs
    sentences = [part.strip() for part in SENTENCE_SPLIT_RE.split(" ".join(raw.split())) if part.strip()]
    return sentences or ([" ".join(raw.split())] if raw.strip() else [])


def _chunk_text(text: str, *, max_chars: int, min_chars: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for paragraph in _paragraphs(text):
        if not paragraph:
            continue
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            for start in range(0, len(paragraph), max_chars):
                part = paragraph[start : start + max_chars].strip()
                if len(part) >= min_chars:
                    chunks.append(part)
            continue
        candidate = f"{current} {paragraph}".strip() if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if len(current) >= min_chars:
                chunks.append(current)
            current = paragraph
    if len(current) >= min_chars:
        chunks.append(current)
    return chunks


def _company_claim_type(block: str, record: dict[str, Any]) -> str:
    text = f"{record.get('title', '')} {block}".lower()
    if any(term in text for term in ["revenue", "sales", "net income", "eps", "earnings", "margin"]):
        return "company_financial_fact"
    if any(term in text for term in ["guidance", "outlook", "forecast", "expects", "expected"]):
        return "company_guidance"
    if any(term in text for term in ["launch", "announces", "introduced", "product", "service"]):
        return "company_operating_fact"
    if any(term in text for term in ["risk", "lawsuit", "investigation", "regulatory", "recall"]):
        return "company_risk_fact"
    return "company_ir_fact"


def _company_units(record: dict[str, Any], *, max_chars: int, min_chars: int) -> list[dict[str, Any]]:
    chunks = _chunk_text(str(record.get("body", "")), max_chars=max_chars, min_chars=min_chars)
    if not chunks and str(record.get("body", "")).strip():
        chunks = [" ".join(str(record.get("body", "")).split())[:max_chars]]
    units: list[dict[str, Any]] = []
    parent_doc_id = str(record.get("doc_id", ""))
    for index, chunk in enumerate(chunks, start=1):
        unit = _copy_base(record)
        unit_id = _unit_hash_id(parent_doc_id, "company_ir_fact_block", index, chunk)
        title = f"{record.get('title', parent_doc_id)} - fact block {index}"
        units.append(
            _finalize_unit(
                unit,
                parent=record,
                unit_id=unit_id,
                unit_type="company_ir_fact_block",
                unit_index=index,
                claim_type=_company_claim_type(chunk, record),
                title=title,
                body=chunk,
            )
        )
    return units


def evidence_units_for_record(
    record: dict[str, Any],
    *,
    company_max_chars: int = 1400,
    company_min_chars: int = 120,
    include_unknown_documents: bool = False,
) -> list[dict[str, Any]]:
    source_type = str(record.get("source_type", "") or "").lower()
    if source_type in {"sec_filing_section", "sec_filing_exhibit"}:
        return [_sec_unit(record)]
    if source_type.startswith("official_macro"):
        return [_macro_unit(record)]
    if source_type in COMPANY_IR_SOURCE_TYPES or source_type.startswith("company_"):
        return _company_units(record, max_chars=company_max_chars, min_chars=company_min_chars)
    if not include_unknown_documents:
        return []
    unit = _copy_base(record)
    parent_doc_id = str(record.get("doc_id", ""))
    unit_id = _unit_hash_id(parent_doc_id, "document_block", 1, str(record.get("body", "")))
    return [
        _finalize_unit(
            unit,
            parent=record,
            unit_id=unit_id,
            unit_type="document_block",
            unit_index=1,
            claim_type="document",
            title=str(record.get("title", "")),
            body=str(record.get("body", "")),
        )
    ]


def build_evidence_units(
    records: Iterable[dict[str, Any]],
    *,
    company_max_chars: int = 1400,
    company_min_chars: int = 120,
    include_unknown_documents: bool = False,
) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for record in records:
        for unit in evidence_units_for_record(
            record,
            company_max_chars=company_max_chars,
            company_min_chars=company_min_chars,
            include_unknown_documents=include_unknown_documents,
        ):
            unit_id = str(unit.get("evidence_unit_id") or unit.get("doc_id"))
            if unit_id in seen_ids:
                continue
            seen_ids.add(unit_id)
            units.append(unit)
    units.sort(
        key=lambda row: (
            str(row.get("available_at", "")),
            str(row.get("source_registry_id", "")),
            str(row.get("parent_doc_id", "")),
            int(row.get("evidence_unit_index", 0) or 0),
            str(row.get("evidence_unit_id", "")),
        )
    )
    return units


def summarize_evidence_units(units: list[dict[str, Any]], inputs: list[str]) -> dict[str, Any]:
    type_counts = Counter(str(unit.get("evidence_unit_type", "")) for unit in units)
    claim_counts = Counter(str(unit.get("evidence_unit_claim_type", "")) for unit in units)
    source_counts = Counter(str(unit.get("source_type", "")) for unit in units)
    return {
        "input_files": inputs,
        "evidence_unit_count": len(units),
        "parent_document_count": len({str(unit.get("parent_doc_id", "")) for unit in units if unit.get("parent_doc_id")}),
        "evidence_unit_type_counts": dict(sorted(type_counts.items())),
        "claim_type_counts": dict(sorted(claim_counts.items())),
        "source_type_counts": dict(sorted(source_counts.items())),
        "missing_available_at": sum(1 for unit in units if not unit.get("available_at")),
        "missing_document_hash": sum(1 for unit in units if not unit.get("document_hash")),
    }


def _read_inputs(inputs: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for raw in inputs:
        for item in str(raw).split(","):
            path = item.strip()
            if path:
                records.extend(read_jsonl(path))
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build evidence-unit JSONL from normalized financial documents.")
    parser.add_argument("--input", required=True, nargs="+", help="One or more normalized document JSONL files; comma-separated values are also accepted.")
    parser.add_argument("--output", required=True, help="Evidence-unit JSONL output.")
    parser.add_argument("--summary-output", default="", help="Optional summary JSON.")
    parser.add_argument("--company-max-chars", type=int, default=1400)
    parser.add_argument("--company-min-chars", type=int, default=120)
    parser.add_argument("--include-unknown-documents", action="store_true")
    args = parser.parse_args(argv)

    records = _read_inputs(args.input)
    units = build_evidence_units(
        records,
        company_max_chars=args.company_max_chars,
        company_min_chars=args.company_min_chars,
        include_unknown_documents=args.include_unknown_documents,
    )
    write_jsonl(args.output, units)
    summary = summarize_evidence_units(units, args.input)
    if args.summary_output:
        Path(args.summary_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.summary_output).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
