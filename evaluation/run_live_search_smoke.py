"""Run live-search smoke checks for guarded evidence-unit retrieval.

This is the post-promotion regression check. It exercises representative
queries against either the in-process web service or a running HTTP server and
verifies latency, gate status, promotion provenance, search grain, and top
result type.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Optional, Union
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web_app import FinPortfolioWebService  # noqa: E402


RESULT_FIELDS = [
    "case_id",
    "query",
    "passed",
    "failures",
    "latency_ms",
    "result_count",
    "raw_count",
    "gate_enabled",
    "gate_active",
    "gate_active_result_count",
    "gate_mode",
    "promotion_status",
    "promotion_reason",
    "feature_flag_enabled",
    "top_search_grain",
    "top_evidence_unit_type",
    "top_evidence_unit_claim_type",
    "top_source_type",
    "top_doc_id",
    "top_title",
]

SUMMARY_FIELDS = [
    "status",
    "case_count",
    "passed_count",
    "failed_count",
    "max_latency_ms",
    "mean_latency_ms",
    "promotion_status_counts",
]


DEFAULT_CASES: list[dict[str, str]] = [
    {
        "case_id": "sec_risk_factors",
        "query": "Apple risk factors in the latest 10-K",
        "expect_gate_enabled": "true",
        "expect_gate_active": "true",
        "expect_top_grain": "evidence_unit",
        "expect_any_unit_signatures": "sec_section:risk_factors|risk_factors",
        "expect_promotion_status": "accepted",
        "max_latency_ms": "15000",
    },
    {
        "case_id": "broad_company_overview",
        "query": "Apple company overview and product history",
        "expect_gate_enabled": "false",
        "expect_gate_active": "false",
        "expect_top_grain": "document",
        "expect_any_unit_signatures": "",
        "expect_promotion_status": "accepted",
        "max_latency_ms": "12000",
    },
    {
        "case_id": "macro_rates_credit",
        "query": "Fed rates and credit spreads",
        "expect_gate_enabled": "true",
        "expect_gate_active": "true",
        "expect_top_grain": "evidence_unit",
        "expect_any_unit_signatures": "macro_observation|rates|credit",
        "expect_promotion_status": "accepted",
        "max_latency_ms": "12000",
    },
    {
        "case_id": "sec_earnings_guidance",
        "query": "Apple 8-K earnings guidance",
        "expect_gate_enabled": "true",
        "expect_gate_active": "true",
        "expect_top_grain": "evidence_unit",
        "expect_any_unit_signatures": "sec_exhibit:filing_exhibit|sec_section:mda|filing_exhibit|mda",
        "expect_promotion_status": "accepted",
        "max_latency_ms": "15000",
    },
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


def write_json(path: Union[str, Path], payload: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _truthy(value: object) -> Optional[bool]:
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return None


def _clean(value: object) -> str:
    return str(value or "").strip()


def _split_expected(value: object) -> set[str]:
    return {item.strip() for item in str(value or "").split("|") if item.strip()}


def _float_value(value: object, default: float = 0.0) -> float:
    try:
        return float(str(value or "").strip())
    except ValueError:
        return default


def _row_signatures(row: dict[str, Any]) -> set[str]:
    unit_type = _clean(row.get("evidence_unit_type"))
    claim_type = _clean(row.get("evidence_unit_claim_type"))
    source_type = _clean(row.get("source_type"))
    signatures = {value for value in [unit_type, claim_type, source_type] if value}
    if unit_type and claim_type:
        signatures.add(f"{unit_type}:{claim_type}")
    return signatures


def _leaf_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    leaves: list[dict[str, Any]] = []
    for row in rows:
        folder_children = row.get("folder_children")
        if isinstance(folder_children, list) and folder_children:
            leaves.extend(_leaf_rows([child for child in folder_children if isinstance(child, dict)]))
            continue
        group_children = row.get("group_children")
        if isinstance(group_children, list) and group_children:
            leaves.extend(_leaf_rows([child for child in group_children if isinstance(child, dict)]))
            continue
        leaves.append(row)
    return leaves


def _top_rows(payload: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    rows = payload.get("results", [])
    if not isinstance(rows, list):
        return []
    return _leaf_rows([row for row in rows if isinstance(row, dict)])[:limit]


def evaluate_smoke_case(case: dict[str, str], payload: dict[str, Any], latency_ms: float) -> dict[str, Any]:
    gate = payload.get("evidence_unit_gate", {}) if isinstance(payload.get("evidence_unit_gate"), dict) else {}
    rows = _top_rows(payload)
    top = rows[0] if rows else {}
    failures: list[str] = []

    max_latency = _float_value(case.get("max_latency_ms"), 0.0)
    if max_latency and latency_ms > max_latency:
        failures.append(f"latency>{int(max_latency)}ms")

    expected_enabled = _truthy(case.get("expect_gate_enabled"))
    if expected_enabled is not None and bool(gate.get("enabled")) != expected_enabled:
        failures.append(f"gate_enabled_expected_{expected_enabled}")

    expected_active = _truthy(case.get("expect_gate_active"))
    if expected_active is not None and bool(gate.get("active")) != expected_active:
        failures.append(f"gate_active_expected_{expected_active}")

    expected_promotion = _clean(case.get("expect_promotion_status"))
    if expected_promotion and _clean(gate.get("promotion_status")) != expected_promotion:
        failures.append(f"promotion_status_expected_{expected_promotion}")

    expected_grain = _clean(case.get("expect_top_grain"))
    if expected_grain and _clean(top.get("search_grain")) != expected_grain:
        failures.append(f"top_grain_expected_{expected_grain}")

    expected_signatures = _split_expected(case.get("expect_any_unit_signatures"))
    if expected_signatures:
        observed = set()
        for row in rows:
            observed.update(_row_signatures(row))
        if not observed.intersection(expected_signatures):
            failures.append("expected_unit_signature_missing")

    if not rows:
        failures.append("no_results")

    return {
        "case_id": case.get("case_id", ""),
        "query": case.get("query", ""),
        "passed": not failures,
        "failures": "|".join(failures),
        "latency_ms": round(latency_ms, 1),
        "result_count": payload.get("count", 0),
        "raw_count": payload.get("raw_count", 0),
        "gate_enabled": bool(gate.get("enabled")),
        "gate_active": bool(gate.get("active")),
        "gate_active_result_count": gate.get("active_result_count", 0),
        "gate_mode": gate.get("mode", ""),
        "promotion_status": gate.get("promotion_status", ""),
        "promotion_reason": gate.get("promotion_reason", ""),
        "feature_flag_enabled": bool(gate.get("feature_flag_enabled")),
        "top_search_grain": top.get("search_grain", ""),
        "top_evidence_unit_type": top.get("evidence_unit_type", ""),
        "top_evidence_unit_claim_type": top.get("evidence_unit_claim_type", ""),
        "top_source_type": top.get("source_type", ""),
        "top_doc_id": top.get("doc_id", ""),
        "top_title": top.get("title", ""),
    }


def summarize_smoke_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [float(row["latency_ms"]) for row in rows]
    passed_count = sum(1 for row in rows if row["passed"])
    status_counts = Counter(str(row.get("promotion_status", "") or "unknown") for row in rows)
    return {
        "status": "passed" if passed_count == len(rows) else "failed",
        "case_count": len(rows),
        "passed_count": passed_count,
        "failed_count": len(rows) - passed_count,
        "max_latency_ms": max(latencies) if latencies else 0.0,
        "mean_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
        "promotion_status_counts": "|".join(f"{key}:{value}" for key, value in sorted(status_counts.items())),
    }


def run_smoke_cases(
    cases: list[dict[str, str]],
    fetch_payload: Callable[[str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        started = time.perf_counter()
        payload = fetch_payload(case["query"])
        latency_ms = (time.perf_counter() - started) * 1000
        rows.append(evaluate_smoke_case(case, payload, latency_ms))
    return rows, summarize_smoke_rows(rows)


def _http_fetcher(base_url: str, timeout_seconds: float) -> Callable[[str], dict[str, Any]]:
    root = base_url.rstrip("/")

    def fetch(query: str) -> dict[str, Any]:
        url = f"{root}/api/search?{urlencode({'q': query, 'limit': '10'})}"
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    return fetch


def _service_fetcher(
    *,
    documents_path: str = "",
    search_index_path: str = "",
    settings_path: str = "",
) -> Callable[[str], dict[str, Any]]:
    service_kwargs: dict[str, Path] = {}
    if settings_path:
        service_kwargs["settings_path"] = Path(settings_path)
    if documents_path:
        service_kwargs["documents_path"] = Path(documents_path)
    if search_index_path:
        service_kwargs["search_index_path"] = Path(search_index_path)
    service = FinPortfolioWebService(**service_kwargs)

    def fetch(query: str) -> dict[str, Any]:
        return service.search_payload(query, limit=10)

    return fetch


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run live search smoke regression checks.")
    parser.add_argument("--base-url", default="", help="Optional running server base URL, e.g. http://127.0.0.1:8780.")
    parser.add_argument("--documents-path", default="")
    parser.add_argument("--search-index-path", default="")
    parser.add_argument("--settings-path", default="")
    parser.add_argument("--cases", default="", help="Optional CSV with smoke cases.")
    parser.add_argument("--output-dir", default="data/exports/live_search_smoke_v1")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)

    cases = read_csv(args.cases) if args.cases else DEFAULT_CASES
    fetcher = (
        _http_fetcher(args.base_url, args.timeout_seconds)
        if args.base_url
        else _service_fetcher(
            documents_path=args.documents_path,
            search_index_path=args.search_index_path,
            settings_path=args.settings_path,
        )
    )
    rows, summary = run_smoke_cases(cases, fetcher)
    output_dir = Path(args.output_dir)
    write_csv(output_dir / "live_search_smoke_results.csv", rows, RESULT_FIELDS)
    write_csv(output_dir / "live_search_smoke_summary.csv", [summary], SUMMARY_FIELDS)
    write_json(output_dir / "live_search_smoke_summary.json", summary | {"results": rows})
    print(json.dumps(summary, sort_keys=True))
    if args.strict and summary["status"] != "passed":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
