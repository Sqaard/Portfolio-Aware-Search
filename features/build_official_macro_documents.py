"""Build official US macro documents for portfolio-level retrieval.

The first version uses public FRED CSV downloads as an official macro backbone.
For point-in-time safety without ALFRED vintages, each observation receives a
conservative availability timestamp: observation date plus a source-specific
release lag. This is intentionally conservative for PPO ablations.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawler.normalize_documents import normalize_records  # noqa: E402
from finportfolio_ir.io_utils import write_jsonl  # noqa: E402


TRAIN_END = date(2021, 10, 1)


@dataclass(frozen=True)
class MacroSeriesSpec:
    series_id: str
    title: str
    source_registry_id: str
    source_name: str
    family: str
    frequency: str
    release_lag_days: int
    event_tags: tuple[str, ...]
    risk_terms: tuple[str, ...]
    units: str = ""


DEFAULT_SERIES: tuple[MacroSeriesSpec, ...] = (
    MacroSeriesSpec("DGS10", "10-Year Treasury Yield", "treasury_us", "U.S. Treasury via FRED", "rates", "daily", 1, ("rates_policy", "yield_curve"), ("interest rates", "real yields", "duration risk"), "percent"),
    MacroSeriesSpec("DGS2", "2-Year Treasury Yield", "treasury_us", "U.S. Treasury via FRED", "rates", "daily", 1, ("rates_policy", "front_end_rates"), ("interest rates", "Fed policy", "funding costs"), "percent"),
    MacroSeriesSpec("T10Y2Y", "10-Year Minus 2-Year Treasury Spread", "fred", "FRED", "credit", "daily", 1, ("yield_curve", "credit_stress"), ("yield curve", "recession risk", "credit stress"), "percentage points"),
    MacroSeriesSpec("VIXCLS", "CBOE VIX Index", "fred", "FRED / CBOE", "market_volatility", "daily", 1, ("market_volatility", "risk_appetite"), ("volatility", "risk appetite", "equity risk"), "index"),
    MacroSeriesSpec("BAMLH0A0HYM2", "ICE BofA High Yield Option-Adjusted Spread", "fred", "FRED / ICE BofA", "credit", "daily", 1, ("credit_spreads", "credit_stress"), ("credit spreads", "funding stress", "default risk"), "percent"),
    MacroSeriesSpec("DCOILWTICO", "WTI Crude Oil Price", "eia", "EIA via FRED", "energy", "daily", 1, ("energy", "input_costs"), ("oil", "energy prices", "inflation pressure"), "USD per barrel"),
    MacroSeriesSpec("FEDFUNDS", "Effective Federal Funds Rate", "federal_reserve", "Federal Reserve via FRED", "rates", "monthly", 7, ("rates_policy", "monetary_policy"), ("Fed policy", "interest rates", "liquidity"), "percent"),
    MacroSeriesSpec("CPIAUCSL", "Consumer Price Index", "bls", "BLS via FRED", "inflation", "monthly", 18, ("inflation_pressure", "consumer_prices"), ("inflation", "real income", "margin pressure"), "index"),
    MacroSeriesSpec("UNRATE", "Unemployment Rate", "bls", "BLS via FRED", "labor", "monthly", 7, ("macro_growth", "labor_market"), ("jobs", "unemployment", "consumer demand"), "percent"),
    MacroSeriesSpec("PAYEMS", "Total Nonfarm Payrolls", "bls", "BLS via FRED", "labor", "monthly", 7, ("macro_growth", "labor_market"), ("payrolls", "jobs", "consumer demand"), "thousands"),
    MacroSeriesSpec("INDPRO", "Industrial Production Index", "federal_reserve", "Federal Reserve via FRED", "growth", "monthly", 21, ("macro_growth", "industrial_cycle"), ("growth", "production", "cyclical demand"), "index"),
    MacroSeriesSpec("HOUST", "Housing Starts", "census", "Census Bureau via FRED", "housing", "monthly", 21, ("housing", "macro_growth"), ("housing", "mortgage rates", "construction demand"), "thousands"),
)


def _availability_at(observation_date: date, release_lag_days: int) -> str:
    available = datetime.combine(
        observation_date + timedelta(days=release_lag_days),
        time(14, 0),
        tzinfo=timezone.utc,
    )
    return available.isoformat().replace("+00:00", "Z")


def _split_for_available_at(available_at: str) -> str:
    return "train" if date.fromisoformat(available_at[:10]) < TRAIN_END else "test"


def _series_url(series_id: str, start_date: str, end_date: str) -> str:
    query = urlencode({"id": series_id, "cosd": start_date, "coed": end_date})
    return f"https://fred.stlouisfed.org/graph/fredgraph.csv?{query}"


def _download_fred_rows(series_id: str, start_date: str, end_date: str, user_agent: str) -> list[dict[str, str]]:
    request = urllib.request.Request(
        _series_url(series_id, start_date, end_date),
        headers={"User-Agent": user_agent, "Accept": "text/csv,*/*"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        text = response.read().decode("utf-8")
    rows = list(csv.DictReader(text.splitlines()))
    return [{"DATE": row["observation_date"] if "observation_date" in row else row["DATE"], "value": row[series_id]} for row in rows]


def _value_to_float(value: str) -> float | None:
    text = str(value).strip()
    if not text or text == ".":
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if math.isnan(number):
        return None
    return number


def build_macro_record(spec: MacroSeriesSpec, observation_date: date, value: float) -> dict[str, Any]:
    available_at = _availability_at(observation_date, spec.release_lag_days)
    value_text = f"{value:.4f}".rstrip("0").rstrip(".")
    title = f"Official US macro release: {spec.title} on {observation_date.isoformat()}"
    body = (
        f"Official US macro observation. Series {spec.series_id}: {spec.title}. "
        f"Observation date: {observation_date.isoformat()}. "
        f"Conservative available_at: {available_at}. "
        f"Value: {value_text} {spec.units}. "
        f"Macro family: {spec.family}. "
        f"Retrieval use: portfolio-level macro/risk evidence for US equities. "
        f"Relevant concepts: {', '.join(spec.risk_terms)}."
    )
    return {
        "doc_id": f"official_macro_{spec.series_id.lower()}_{observation_date.isoformat()}",
        "title": title,
        "body": body,
        "source": spec.source_name,
        "source_type": "official_macro_release",
        "source_registry_id": spec.source_registry_id,
        "source_reliability_tier": "official",
        "robots_policy": "Use public CSV/API downloads and cache observations for reproducibility.",
        "content_license_note": "Public macro time series; preserve source series, observation date, and retrieval assumptions.",
        "source_credibility": 0.90,
        "url": _series_url(spec.series_id, observation_date.isoformat(), observation_date.isoformat()),
        "canonical_url": f"https://fred.stlouisfed.org/series/{spec.series_id}",
        "published_at": available_at,
        "first_seen_at": available_at,
        "available_at": available_at,
        "ingested_at": available_at,
        "last_url_check_at": available_at,
        "fetch_status": "ok",
        "version_id": f"{spec.series_id}:{observation_date.isoformat()}",
        "duplicate_cluster_id": f"official_macro:{spec.series_id}:{observation_date.isoformat()}",
        "tickers_detected": ["MARKET"],
        "matched_tickers": ["MARKET"],
        "matched_holdings": [],
        "company_names_detected": [],
        "sectors_detected": ["Macro"],
        "sector_tags": ["Macro", spec.family],
        "event_tags": ["official_macro", spec.family, *spec.event_tags],
        "risk_terms": list(spec.risk_terms),
        "event_type": f"official_macro_{spec.family}",
        "language": "en",
        "macro_series_id": spec.series_id,
        "macro_series_title": spec.title,
        "macro_family": spec.family,
        "macro_frequency": spec.frequency,
        "macro_observation_date": observation_date.isoformat(),
        "macro_value": value,
        "macro_units": spec.units,
        "macro_release_lag_days": spec.release_lag_days,
        "split": _split_for_available_at(available_at),
    }


def build_official_macro_documents(
    *,
    output_raw: Path,
    output_processed: Path,
    metadata: Path,
    source_registry: Path,
    summary_output: Path,
    start_date: str,
    end_date: str,
    user_agent: str,
    series_specs: tuple[MacroSeriesSpec, ...] = DEFAULT_SERIES,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for spec in series_specs:
        try:
            for row in _download_fred_rows(spec.series_id, start_date, end_date, user_agent):
                value = _value_to_float(row["value"])
                if value is None:
                    continue
                observation_date = date.fromisoformat(row["DATE"])
                records.append(build_macro_record(spec, observation_date, value))
        except Exception as exc:  # noqa: BLE001 - CLI summary should preserve partial success.
            errors.append({"series_id": spec.series_id, "error": str(exc)[:300]})

    records.sort(key=lambda row: (str(row.get("available_at", "")), str(row.get("macro_series_id", ""))))
    write_jsonl(output_raw, records)

    normalized = normalize_records(records, metadata, source_registry)
    raw_by_doc = {str(row.get("doc_id")): row for row in records}
    for row in normalized:
        raw = raw_by_doc.get(str(row.get("doc_id")), {})
        for key in [
            "macro_series_id",
            "macro_series_title",
            "macro_family",
            "macro_frequency",
            "macro_observation_date",
            "macro_value",
            "macro_units",
            "macro_release_lag_days",
            "split",
        ]:
            row[key] = raw.get(key, "")
        row["matched_tickers"] = ["MARKET"]
        row["matched_holdings"] = []
        row["tickers_detected"] = ["MARKET"]
    write_jsonl(output_processed, normalized)

    summary = {
        "input_series": [spec.series_id for spec in series_specs],
        "raw_rows": len(records),
        "processed_rows": len(normalized),
        "start_date": start_date,
        "end_date": end_date,
        "series_counts": dict(Counter(str(row.get("macro_series_id", "")) for row in normalized)),
        "family_counts": dict(Counter(str(row.get("macro_family", "")) for row in normalized)),
        "split_counts": dict(Counter(str(row.get("split", "")) for row in normalized)),
        "errors": errors,
        "error_count": len(errors),
        "raw_output": str(output_raw),
        "processed_output": str(output_processed),
    }
    summary_output.parent.mkdir(parents=True, exist_ok=True)
    summary_output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build official US macro retrieval documents.")
    parser.add_argument("--output-raw", default="data/raw_documents/official_macro_2010_2023.jsonl")
    parser.add_argument("--output-processed", default="data/processed_documents/official_macro_2010_2023_documents.jsonl")
    parser.add_argument("--metadata", default="data/processed_documents/dow30_ticker_metadata.csv")
    parser.add_argument("--source-registry", default="data/source_registry/source_registry.csv")
    parser.add_argument("--summary-output", default="data/processed_documents/official_macro_2010_2023_summary.json")
    parser.add_argument("--start-date", default="2010-01-01")
    parser.add_argument("--end-date", default="2023-03-01")
    parser.add_argument("--user-agent", default="FinPortfolioIR/0.1 academic research contact=ivanp@example.com")
    args = parser.parse_args(argv)

    summary = build_official_macro_documents(
        output_raw=Path(args.output_raw),
        output_processed=Path(args.output_processed),
        metadata=Path(args.metadata),
        source_registry=Path(args.source_registry),
        summary_output=Path(args.summary_output),
        start_date=args.start_date,
        end_date=args.end_date,
        user_agent=args.user_agent,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["processed_rows"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
