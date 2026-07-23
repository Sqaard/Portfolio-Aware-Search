"""Point-in-time event ledger helpers for FinPortfolio IR.

The ledger is a READ/FEATURE layer artifact: it converts retrieved official
documents and SEC structured feeds into bounded event rows. It does not make
trading decisions and does not promote any feature into PPO state.
"""

from __future__ import annotations

import csv
import hashlib
import http.client
import json
import re
import shutil
import subprocess
import time
from collections import Counter, defaultdict
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from finportfolio_ir.text_utils import excerpt, stable_document_hash, tokenize


NY_TZ = ZoneInfo("America/New_York")
UTC = timezone.utc
SEC_USER_AGENT = "FinPortfolioIR/0.1 research contact: local@example.com"

EVENT_FEATURE_COLUMNS = [
    "earnings_surprise_direction",
    "earnings_surprise_magnitude",
    "eps_surprise_z",
    "revenue_surprise_z",
    "margin_surprise_z",
    "guidance_revision_direction",
    "guidance_revision_magnitude",
    "guidance_specificity",
    "management_confidence_delta",
    "promise_pressure",
    "promise_delivery_risk",
    "promise_miss_count_rolling_4q",
    "analyst_eps_revision_direction",
    "analyst_revision_breadth",
    "consensus_dispersion_delta",
    "rating_change_direction",
    "price_target_revision_magnitude",
    "buyback_intensity",
    "dividend_change_direction",
    "debt_refinancing_stress",
    "liquidity_stress",
    "regulatory_pressure",
    "supply_chain_shock_intensity",
    "evidence_specificity",
    "numeric_evidence_density",
    "uncertainty_intensity",
    "downside_risk_intensity",
    "actionability_decay_days",
    "source_quality_score",
    "timestamp_confidence",
]

SEC_COMPANYFACT_TAGS = {
    "Revenues": "revenue",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "revenue",
    "SalesRevenueNet": "revenue",
    "NetIncomeLoss": "net_income",
    "EarningsPerShareDiluted": "eps_diluted",
    "GrossProfit": "gross_profit",
    "OperatingIncomeLoss": "operating_income",
    "AssetsCurrent": "current_assets",
    "LiabilitiesCurrent": "current_liabilities",
    "CashAndCashEquivalentsAtCarryingValue": "cash",
    "LongTermDebtCurrent": "current_long_term_debt",
    "LongTermDebtNoncurrent": "long_term_debt",
    "StockholdersEquity": "equity",
    "CommonStocksIncludingAdditionalPaidInCapital": "common_stock_capital",
}

SOURCE_CARDS = [
    {
        "source_id": "sec_edgar",
        "source_family": "sec_edgar",
        "name": "SEC EDGAR submissions and filings",
        "point_in_time_availability": "SEC acceptance timestamp; API updates in near real time.",
        "timestamp_precision": "seconds when acceptanceDateTime is available",
        "historical_depth": "deep EDGAR history, local corpus 2010-2023 plus live metadata",
        "coverage": "US public companies",
        "survivorship_bias_risk": "low for fetched CIKs; universe membership still external",
        "licensing_risk": "low; public government data with fair-access policy",
        "cost": "free",
        "api_access_difficulty": "easy-medium",
        "expected_chrl_usefulness": "high for official events and PIT corroboration",
    },
    {
        "source_id": "sec_companyfacts",
        "source_family": "structured_fundamentals",
        "name": "SEC Companyfacts XBRL API",
        "point_in_time_availability": "filed date from fact; conservative end-of-day availability in this layer",
        "timestamp_precision": "date-level unless linked to accession acceptance timestamp",
        "historical_depth": "XBRL facts since 2009 for most large issuers",
        "coverage": "US public companies",
        "survivorship_bias_risk": "medium if current CIK universe only",
        "licensing_risk": "low; public government data",
        "cost": "free",
        "api_access_difficulty": "easy",
        "expected_chrl_usefulness": "medium as slow fundamental context and chart input",
    },
    {
        "source_id": "company_ir_existing_corpus",
        "source_family": "company_ir",
        "name": "Existing company IR / official press corpus",
        "point_in_time_availability": "published_at or first_seen_at from local crawler",
        "timestamp_precision": "varies by page; often date-level or page timestamp",
        "historical_depth": "depends on company archive",
        "coverage": "issuer-specific",
        "survivorship_bias_risk": "medium; archive survival varies",
        "licensing_risk": "medium; preserve source URLs and do not redistribute full text unnecessarily",
        "cost": "free",
        "api_access_difficulty": "medium; HTML varies",
        "expected_chrl_usefulness": "high for guidance/promise language after PIT checks",
    },
    {
        "source_id": "estimate_vendor_placeholder",
        "source_family": "analyst_estimates",
        "name": "Intrinio/Zacks, Nasdaq Data Link, FactSet, Finnhub, Benzinga estimates",
        "point_in_time_availability": "must be proven by vendor vintage/update timestamp before use",
        "timestamp_precision": "vendor-dependent",
        "historical_depth": "vendor-dependent",
        "coverage": "broad equities if licensed",
        "survivorship_bias_risk": "medium-high unless vendor provides delisted/history controls",
        "licensing_risk": "high",
        "cost": "paid",
        "api_access_difficulty": "medium",
        "expected_chrl_usefulness": "high if true historical revisions are available",
    },
]


GUIDANCE_TERMS = {
    "guidance",
    "outlook",
    "forecast",
    "expects",
    "expected",
    "anticipates",
    "full-year",
    "full year",
    "next quarter",
    "fiscal year",
    "raised",
    "lowered",
    "withdraw",
}
UNCERTAINTY_TERMS = {
    "uncertain",
    "uncertainty",
    "could",
    "may",
    "might",
    "subject to",
    "volatility",
    "headwind",
    "challenging",
    "pressure",
}
DOWNSIDE_TERMS = {
    "risk",
    "decline",
    "decrease",
    "lower",
    "weak",
    "impairment",
    "loss",
    "shortfall",
    "deterioration",
}


def parse_utc(value: Any, *, date_time: dtime | None = None) -> datetime | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if re.fullmatch(r"\d{14}", raw):
        return datetime.strptime(raw, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    if re.fullmatch(r"\d{8}", raw):
        day = datetime.strptime(raw, "%Y%m%d").date()
        return datetime.combine(day, date_time or dtime(23, 59, 59), tzinfo=UTC)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        day = date.fromisoformat(raw)
        return datetime.combine(day, date_time or dtime(23, 59, 59), tzinfo=UTC)
    cleaned = raw.replace("Z", "+00:00")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}", cleaned):
        cleaned = cleaned + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def iso_utc(dt: datetime | None) -> str:
    if not dt:
        return ""
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def market_session_tag(dt_utc: datetime | None) -> str:
    if dt_utc is None:
        return "unknown"
    local = dt_utc.astimezone(NY_TZ)
    if local.weekday() >= 5:
        return "weekend"
    current = local.time()
    if current < dtime(9, 30):
        return "before_open"
    if current <= dtime(16, 0):
        return "during_market"
    return "after_close"


def next_market_open(dt_utc: datetime | None) -> datetime | None:
    if dt_utc is None:
        return None
    local = dt_utc.astimezone(NY_TZ)
    candidate_date = local.date()
    if local.weekday() >= 5 or local.time() >= dtime(9, 30):
        candidate_date = candidate_date + timedelta(days=1)
    while candidate_date.weekday() >= 5:
        candidate_date = candidate_date + timedelta(days=1)
    local_open = datetime.combine(candidate_date, dtime(9, 30), tzinfo=NY_TZ)
    return local_open.astimezone(UTC)


def event_hash(parts: Iterable[Any]) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def numeric_density(text: str) -> float:
    tokens = tokenize(text)
    if not tokens:
        return 0.0
    numeric = sum(1 for token in tokens if re.fullmatch(r"\d+(?:\.\d+)?", token.strip("$%")))
    return clamp((numeric / max(1, len(tokens))) * 12.0)


def term_intensity(text: str, terms: set[str], *, scale: float = 4.0) -> float:
    lowered = (text or "").lower()
    hits = sum(1 for term in terms if term in lowered)
    return clamp(hits / scale)


def evidence_specificity(text: str) -> float:
    lowered = (text or "").lower()
    has_date = bool(re.search(r"\b(20\d{2}|19\d{2}|q[1-4]|quarter|fiscal|year)\b", lowered))
    has_currency = bool(re.search(r"[$]\s?\d|\b\d+(?:\.\d+)?\s?(million|billion|percent|%)", lowered))
    return clamp(0.45 * numeric_density(text) + 0.25 * has_date + 0.30 * has_currency)


def base_feature_payload(text: str, source_quality: float, timestamp_confidence: float) -> dict[str, float | None]:
    lowered = (text or "").lower()
    guidance_hit = term_intensity(lowered, GUIDANCE_TERMS)
    downside_hit = term_intensity(lowered, DOWNSIDE_TERMS)
    uncertainty_hit = term_intensity(lowered, UNCERTAINTY_TERMS)
    liquidity_hit = term_intensity(lowered, {"liquidity", "covenant", "maturity", "refinancing", "going concern"}, scale=3.0)
    regulatory_hit = term_intensity(lowered, {"regulatory", "investigation", "antitrust", "litigation", "lawsuit", "doj", "ftc", "sec investigation"}, scale=3.0)
    supply_hit = term_intensity(lowered, {"supply chain", "supplier", "production", "shortage", "disruption", "delivery"}, scale=3.0)
    features: dict[str, float | None] = {name: None for name in EVENT_FEATURE_COLUMNS}
    features.update(
        {
            "guidance_specificity": clamp(guidance_hit * evidence_specificity(text)),
            "management_confidence_delta": clamp(term_intensity(lowered, {"confident", "strong", "resilient", "record"}, scale=4.0) - downside_hit, -1.0, 1.0),
            "promise_pressure": clamp(guidance_hit + 0.25 * evidence_specificity(text)),
            "promise_delivery_risk": clamp(downside_hit + uncertainty_hit + liquidity_hit),
            "liquidity_stress": liquidity_hit,
            "regulatory_pressure": regulatory_hit,
            "supply_chain_shock_intensity": supply_hit,
            "evidence_specificity": evidence_specificity(text),
            "numeric_evidence_density": numeric_density(text),
            "uncertainty_intensity": uncertainty_hit,
            "downside_risk_intensity": downside_hit,
            "actionability_decay_days": 0.0,
            "source_quality_score": clamp(source_quality),
            "timestamp_confidence": clamp(timestamp_confidence),
        }
    )
    return features


def source_quality(record: dict[str, Any]) -> float:
    if record.get("source_quality_score") is not None:
        return float(record["source_quality_score"])
    if record.get("source_credibility") is not None:
        try:
            return float(record["source_credibility"])
        except (TypeError, ValueError):
            pass
    source_type = str(record.get("source_type", "")).lower()
    if source_type.startswith("sec"):
        return 0.95
    if source_type.startswith("company"):
        return 0.80
    if source_type.startswith("official_macro"):
        return 0.90
    return 0.50


def timestamp_confidence(event_time: datetime | None, available_at: datetime | None, record: dict[str, Any]) -> float:
    if event_time and available_at and "T" in str(record.get("available_at", "")):
        return 0.90
    if record.get("sec_exhibit_last_modified"):
        return 0.85
    if available_at:
        return 0.65
    return 0.0


def ticker_from_record(record: dict[str, Any]) -> str:
    candidates = [
        record.get("sec_ticker"),
        (record.get("matched_tickers") or [""])[0] if isinstance(record.get("matched_tickers"), list) else "",
        (record.get("tickers_detected") or [""])[0] if isinstance(record.get("tickers_detected"), list) else "",
    ]
    for candidate in candidates:
        value = str(candidate or "").upper()
        if value and value != "MARKET":
            return value
    return ""


def classify_document_subtypes(record: dict[str, Any]) -> list[tuple[str, str]]:
    title = str(record.get("title", ""))
    body = str(record.get("body", ""))
    text = f"{title} {body}".lower()
    source_type = str(record.get("source_type", "")).lower()
    sec_code = str(record.get("sec_section_code", "")).lower()
    sec_form = str(record.get("sec_form", "")).upper()
    out: list[tuple[str, str]] = []

    if source_type.startswith("sec") and sec_form:
        out.append(("sec_filing", f"{sec_form.lower()}_{sec_code or 'document'}"))
    if sec_form == "8-K" and (sec_code == "2.02" or "earnings_release_candidate" in record.get("event_tags", []) or "earnings release" in text):
        out.append(("earnings", "earnings_release_candidate"))
    if source_type.startswith("company") and any(term in text for term in ["earnings", "quarter results", "financial results", "reports results"]):
        out.append(("earnings", "company_earnings_release_candidate"))
    if any(term in text for term in GUIDANCE_TERMS):
        out.append(("guidance", "guidance_language"))
    if any(term in text for term in ["share repurchase", "stock repurchase", "buyback", "repurchase program"]):
        out.append(("corporate_action", "buyback"))
    if "dividend" in text:
        out.append(("corporate_action", "dividend_change"))
    if any(term in text for term in ["notes due", "debt offering", "refinancing", "credit facility", "senior notes", "covenant"]):
        out.append(("corporate_action", "debt_refinancing"))
    if any(term in text for term in ["acquisition", "merger", "acquire", "tender offer", "divestiture", "spin-off", "spinoff"]):
        out.append(("corporate_action", "m_and_a"))
    if any(term in text for term in ["restructuring", "layoff", "workforce reduction", "cost reduction plan"]):
        out.append(("corporate_action", "restructuring_or_layoff"))
    if any(term in text for term in ["chief executive", "ceo", "chief financial", "cfo", "resigns", "appointed"]):
        out.append(("corporate_action", "management_change"))
    if any(term in text for term in ["liquidity", "going concern", "covenant", "debt maturity", "refinancing risk"]):
        out.append(("risk_stress", "liquidity_or_covenant"))
    if any(term in text for term in ["downgrade", "credit rating", "rating agency"]):
        out.append(("risk_stress", "credit_rating"))
    if any(term in text for term in ["regulatory investigation", "antitrust", "litigation", "lawsuit", "doj", "ftc", "sec investigation"]):
        out.append(("risk_stress", "regulatory_or_litigation"))
    if any(term in text for term in ["supply chain", "supplier", "production disruption", "delivery delay", "shortage"]):
        out.append(("risk_stress", "supply_chain_shock"))
    if any(term in text for term in ["sanction", "geopolitical", "commodity", "oil price", "natural gas", "opec"]):
        out.append(("risk_stress", "geopolitical_or_commodity"))

    seen: set[tuple[str, str]] = set()
    deduped: list[tuple[str, str]] = []
    for item in out:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def make_event(
    *,
    source_id: str,
    source_family: str,
    ticker: str,
    company_id: str,
    cik: str,
    event_type: str,
    event_subtype: str,
    event_time: datetime | None,
    available_at: datetime | None,
    document_hash: str,
    raw_source_url: str,
    title: str,
    body: str,
    source_document_id: str,
    extracted_features: dict[str, Any],
    source_reliability_score: float,
    timestamp_confidence_score: float,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    decision_time = next_market_open(available_at)
    retrieval_cutoff = decision_time
    pit_valid = bool(available_at and retrieval_cutoff and decision_time and available_at <= retrieval_cutoff <= decision_time)
    session = market_session_tag(event_time or available_at)
    event_id = event_hash(
        [
            source_id,
            ticker,
            event_type,
            event_subtype,
            iso_utc(event_time),
            iso_utc(available_at),
            document_hash,
            source_document_id,
        ]
    )
    return {
        "event_id": event_id,
        "source_id": source_id,
        "source_family": source_family,
        "ticker": ticker,
        "company_id": company_id,
        "cik": cik,
        "event_type": event_type,
        "event_subtype": event_subtype,
        "event_time_utc": iso_utc(event_time),
        "available_at_utc": iso_utc(available_at),
        "retrieval_cutoff_utc": iso_utc(retrieval_cutoff),
        "decision_time_utc": iso_utc(decision_time),
        "market_session_tag": session,
        "before_open": session == "before_open",
        "during_market": session == "during_market",
        "after_close": session == "after_close",
        "document_hash": document_hash,
        "raw_source_url": raw_source_url,
        "source_document_id": source_document_id,
        "title": title,
        "body_excerpt": excerpt(body, 500),
        "extracted_features": extracted_features,
        "source_reliability_score": clamp(source_reliability_score),
        "timestamp_confidence": clamp(timestamp_confidence_score),
        "point_in_time_valid_flag": pit_valid,
        "pit_failure_reason": "" if pit_valid else "missing_or_invalid_available_retrieval_decision_order",
        "metadata": metadata or {},
    }


def events_from_document(record: dict[str, Any]) -> list[dict[str, Any]]:
    ticker = ticker_from_record(record)
    if not ticker:
        return []
    subtypes = classify_document_subtypes(record)
    if not subtypes:
        return []
    event_time = parse_utc(record.get("published_at")) or parse_utc(record.get("sec_exhibit_last_modified")) or parse_utc(record.get("available_at"))
    available_at = parse_utc(record.get("available_at")) or parse_utc(record.get("first_seen_at")) or event_time
    quality = source_quality(record)
    ts_conf = timestamp_confidence(event_time, available_at, record)
    text = f"{record.get('title', '')} {record.get('body', '')}"
    features = base_feature_payload(text, quality, ts_conf)
    source_type = str(record.get("source_type", "")).lower()
    if source_type.startswith("sec"):
        source_id = "sec_edgar"
        source_family = "sec_edgar"
    elif source_type.startswith("company"):
        source_id = str(record.get("source_registry_id") or "company_ir_existing_corpus")
        source_family = "company_ir"
    else:
        source_id = str(record.get("source_registry_id") or record.get("source") or "unknown")
        source_family = source_type or "unknown"
    sec = record.get("sec") if isinstance(record.get("sec"), dict) else {}
    cik = str(sec.get("cik") or record.get("cik") or "")
    doc_hash = str(record.get("document_hash") or stable_document_hash(record))
    events = []
    for event_type, event_subtype in subtypes:
        event_features = dict(features)
        if event_type == "earnings":
            event_features["earnings_surprise_direction"] = 0.0
            event_features["earnings_surprise_magnitude"] = None
        if event_subtype == "buyback":
            event_features["buyback_intensity"] = clamp(0.2 + 0.8 * numeric_density(text))
        if event_subtype == "dividend_change":
            event_features["dividend_change_direction"] = 0.0
        if event_subtype == "debt_refinancing":
            event_features["debt_refinancing_stress"] = clamp(0.2 + event_features.get("liquidity_stress", 0.0) or 0.0)
        events.append(
            make_event(
                source_id=source_id,
                source_family=source_family,
                ticker=ticker,
                company_id=cik,
                cik=cik,
                event_type=event_type,
                event_subtype=event_subtype,
                event_time=event_time,
                available_at=available_at,
                document_hash=doc_hash,
                raw_source_url=str(record.get("url") or record.get("canonical_url") or ""),
                title=str(record.get("title") or ""),
                body=str(record.get("body") or ""),
                source_document_id=str(record.get("doc_id") or ""),
                extracted_features=event_features,
                source_reliability_score=quality,
                timestamp_confidence_score=ts_conf,
                metadata={
                    "source_type": record.get("source_type", ""),
                    "sec_form": record.get("sec_form", ""),
                    "sec_accession_number": record.get("sec_accession_number", ""),
                    "sec_section_code": record.get("sec_section_code", ""),
                    "sec_section_title": record.get("sec_section_title", ""),
                },
            )
        )
    return events


def read_ticker_metadata(path: str | Path) -> dict[str, dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return {str(row.get("ticker", "")).upper(): row for row in rows if row.get("ticker")}


def request_json_via_curl(url: str, *, user_agent: str = SEC_USER_AGENT, timeout: int = 20, retries: int = 2) -> dict[str, Any]:
    curl = shutil.which("curl") or shutil.which("curl.exe")
    if not curl:
        raise RuntimeError("curl executable is not available for fallback fetch")
    max_time = max(timeout * max(1, retries), timeout + 30)
    command = [
        curl,
        "-L",
        "--silent",
        "--show-error",
        "--retry",
        str(max(0, retries)),
        "--retry-delay",
        "2",
        "--connect-timeout",
        str(min(timeout, 10)),
        "--max-time",
        str(max_time),
        "-H",
        f"User-Agent: {user_agent}",
        "-H",
        "Accept-Encoding: identity",
        url,
    ]
    completed = subprocess.run(command, check=True, capture_output=True, timeout=max_time + 10)
    return json.loads(completed.stdout.decode("utf-8"))


def request_json(url: str, *, user_agent: str = SEC_USER_AGENT, timeout: int = 20, retries: int = 2) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": user_agent, "Accept-Encoding": "identity"})
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, http.client.IncompleteRead, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(5.0, 0.75 * attempt))
                continue
            break
    try:
        return request_json_via_curl(url, user_agent=user_agent, timeout=timeout, retries=retries)
    except Exception:
        if last_error:
            raise last_error
        raise


def cached_request_json(
    cache_path: Path,
    url: str,
    *,
    force: bool = False,
    user_agent: str = SEC_USER_AGENT,
    sleep_seconds: float = 0.12,
    timeout: int = 20,
    retries: int = 2,
) -> dict[str, Any]:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists() and not force:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    payload = request_json(url, user_agent=user_agent, timeout=timeout, retries=retries)
    cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if sleep_seconds > 0:
        time.sleep(sleep_seconds)
    return payload


def events_from_sec_submissions(
    metadata: dict[str, dict[str, str]],
    *,
    cache_dir: Path,
    start_date: date,
    end_date: date,
    tickers: Iterable[str] | None = None,
    force: bool = False,
    user_agent: str = SEC_USER_AGENT,
    request_timeout: int = 20,
    request_retries: int = 2,
    progress: Callable[[str, str, int], None] | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    ticker_list = [ticker.upper() for ticker in (tickers or metadata.keys()) if ticker.upper() in metadata]
    for position, ticker in enumerate(ticker_list, start=1):
        before_count = len(events)
        if progress:
            progress(ticker, "start", position)
        row = metadata[ticker]
        cik = str(row.get("cik", "")).zfill(10)
        if not cik.strip("0"):
            if progress:
                progress(ticker, "missing_cik", position)
            continue
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        try:
            payload = cached_request_json(
                cache_dir / f"CIK{cik}.json",
                url,
                force=force,
                user_agent=user_agent,
                timeout=request_timeout,
                retries=request_retries,
            )
        except (HTTPError, URLError, TimeoutError, http.client.IncompleteRead, json.JSONDecodeError):
            if progress:
                progress(ticker, "fetch_failed", position)
            continue
        recent = payload.get("filings", {}).get("recent", {})
        accessions = recent.get("accessionNumber", [])
        forms = recent.get("form", [])
        filing_dates = recent.get("filingDate", [])
        report_dates = recent.get("reportDate", [])
        acceptances = recent.get("acceptanceDateTime", [])
        primary_docs = recent.get("primaryDocument", [])
        items = recent.get("items", [])
        for index, accession in enumerate(accessions):
            form = str(forms[index] if index < len(forms) else "")
            filing_date = str(filing_dates[index] if index < len(filing_dates) else "")
            try:
                filed_day = date.fromisoformat(filing_date)
            except ValueError:
                continue
            if filed_day < start_date or filed_day > end_date:
                continue
            accepted = parse_utc(acceptances[index] if index < len(acceptances) else "") or parse_utc(filing_date)
            accession_nodash = str(accession).replace("-", "")
            primary = str(primary_docs[index] if index < len(primary_docs) else "")
            cik_nozero = str(int(cik))
            url = f"https://www.sec.gov/Archives/edgar/data/{cik_nozero}/{accession_nodash}/{primary}" if primary else f"https://www.sec.gov/Archives/edgar/data/{cik_nozero}/{accession_nodash}/"
            item_value = str(items[index] if index < len(items) else "")
            if form == "8-K" and "2.02" in item_value:
                event_type, subtype = "earnings", "sec_8k_item_2_02_results"
            elif form == "8-K" and "5.02" in item_value:
                event_type, subtype = "corporate_action", "management_change"
            elif form == "8-K":
                event_type, subtype = "sec_filing", "8-k_current_report"
            elif form in {"10-Q", "10-K"}:
                event_type, subtype = "sec_filing", form.lower()
            else:
                event_type, subtype = "sec_filing", form.lower() or "filing"
            body = f"SEC {form} filing metadata for {ticker}. Items: {item_value}. Report date: {report_dates[index] if index < len(report_dates) else ''}."
            doc_hash = event_hash([ticker, cik, accession, form, filing_date, accepted, primary])
            quality = 0.95
            ts_conf = 0.95 if accepted else 0.60
            features = base_feature_payload(body, quality, ts_conf)
            events.append(
                make_event(
                    source_id="sec_edgar_submissions_live",
                    source_family="sec_edgar",
                    ticker=ticker,
                    company_id=cik,
                    cik=cik,
                    event_type=event_type,
                    event_subtype=subtype,
                    event_time=accepted,
                    available_at=accepted,
                    document_hash=doc_hash,
                    raw_source_url=url,
                    title=f"{ticker} {form} filing metadata filed {filing_date}",
                    body=body,
                    source_document_id=f"sec_live_{ticker.lower()}_{accession_nodash}",
                    extracted_features=features,
                    source_reliability_score=quality,
                    timestamp_confidence_score=ts_conf,
                    metadata={"accession": accession, "form": form, "filing_date": filing_date, "items": item_value, "primary_document": primary},
                )
            )
        if progress:
            progress(ticker, f"done:+{len(events) - before_count}", position)
    return events


def events_from_companyfacts(
    metadata: dict[str, dict[str, str]],
    *,
    cache_dir: Path,
    start_date: date,
    end_date: date,
    tickers: Iterable[str] | None = None,
    force: bool = False,
    user_agent: str = SEC_USER_AGENT,
    max_facts_per_tag: int = 500,
    request_timeout: int = 20,
    request_retries: int = 2,
    progress: Callable[[str, str, int], None] | None = None,
    raise_on_fetch_error: bool = False,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    ticker_list = [ticker.upper() for ticker in (tickers or metadata.keys()) if ticker.upper() in metadata]
    for position, ticker in enumerate(ticker_list, start=1):
        before_count = len(events)
        if progress:
            progress(ticker, "start", position)
        row = metadata[ticker]
        cik = str(row.get("cik", "")).zfill(10)
        if not cik.strip("0"):
            if progress:
                progress(ticker, "missing_cik", position)
            continue
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        try:
            payload = cached_request_json(
                cache_dir / f"CIK{cik}.json",
                url,
                force=force,
                user_agent=user_agent,
                timeout=request_timeout,
                retries=request_retries,
            )
        except (HTTPError, URLError, TimeoutError, http.client.IncompleteRead, json.JSONDecodeError):
            if progress:
                progress(ticker, "fetch_failed", position)
            if raise_on_fetch_error:
                raise
            continue
        us_gaap = payload.get("facts", {}).get("us-gaap", {})
        for tag, subtype in SEC_COMPANYFACT_TAGS.items():
            concept = us_gaap.get(tag)
            if not isinstance(concept, dict):
                continue
            units = concept.get("units", {})
            rows: list[dict[str, Any]] = []
            for unit_rows in units.values():
                if isinstance(unit_rows, list):
                    rows.extend(item for item in unit_rows if isinstance(item, dict))
            rows.sort(key=lambda item: str(item.get("filed", "")))
            for fact in rows[-max_facts_per_tag:]:
                filed = str(fact.get("filed", ""))
                try:
                    filed_day = date.fromisoformat(filed)
                except ValueError:
                    continue
                if filed_day < start_date or filed_day > end_date:
                    continue
                available = parse_utc(filed, date_time=dtime(23, 59, 59))
                value = fact.get("val")
                form = str(fact.get("form", ""))
                accession = str(fact.get("accn", ""))
                fy = fact.get("fy")
                fp = fact.get("fp")
                end = fact.get("end")
                body = f"SEC companyfacts {tag} for {ticker}: value {value}; fiscal {fy} {fp}; period end {end}; form {form}; filed {filed}."
                doc_hash = event_hash([ticker, cik, tag, value, accession, filed, end, form])
                quality = 0.95
                ts_conf = 0.65
                features = base_feature_payload(body, quality, ts_conf)
                if subtype == "revenue":
                    features["earnings_surprise_direction"] = 0.0
                if subtype in {"current_long_term_debt", "long_term_debt"}:
                    features["debt_refinancing_stress"] = 0.2
                events.append(
                    make_event(
                        source_id="sec_companyfacts",
                        source_family="structured_fundamentals",
                        ticker=ticker,
                        company_id=cik,
                        cik=cik,
                        event_type="fundamental_fact",
                        event_subtype=subtype,
                        event_time=parse_utc(end, date_time=dtime(23, 59, 59)) or available,
                        available_at=available,
                        document_hash=doc_hash,
                        raw_source_url=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
                        title=f"{ticker} SEC companyfacts {tag} filed {filed}",
                        body=body,
                        source_document_id=f"sec_companyfacts_{ticker.lower()}_{tag}_{accession}_{filed}",
                        extracted_features=features,
                        source_reliability_score=quality,
                        timestamp_confidence_score=ts_conf,
                        metadata={"tag": tag, "value": value, "unit": fact.get("unit", ""), "form": form, "accession": accession, "fy": fy, "fp": fp, "end": end, "filed": filed},
                    )
                )
        if progress:
            progress(ticker, f"done:+{len(events) - before_count}", position)
    return events


def dedupe_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for event in events:
        by_id[str(event["event_id"])] = event
    return sorted(by_id.values(), key=lambda row: (row.get("available_at_utc", ""), row.get("ticker", ""), row.get("event_type", ""), row.get("event_id", "")))


def validate_pit(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    violations: list[dict[str, str]] = []
    count = 0
    for event in events:
        count += 1
        available = parse_utc(event.get("available_at_utc"))
        cutoff = parse_utc(event.get("retrieval_cutoff_utc"))
        decision = parse_utc(event.get("decision_time_utc"))
        if not available or not cutoff or not decision or not (available <= cutoff <= decision):
            violations.append({"event_id": str(event.get("event_id", "")), "reason": "available_cutoff_decision_order"})
        if event.get("after_close") and available and decision:
            local_available = available.astimezone(NY_TZ).date()
            local_decision = decision.astimezone(NY_TZ).date()
            if local_decision <= local_available and available.astimezone(NY_TZ).time() >= dtime(16, 0):
                violations.append({"event_id": str(event.get("event_id", "")), "reason": "after_close_same_day_decision"})
    return {
        "event_count": count,
        "pit_violation_count": len(violations),
        "point_in_time_valid": len(violations) == 0,
        "sample_violations": violations[:20],
    }


def summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_type = Counter(str(event.get("event_type", "")) for event in events)
    by_subtype = Counter(str(event.get("event_subtype", "")) for event in events)
    by_source = Counter(str(event.get("source_id", "")) for event in events)
    by_ticker = Counter(str(event.get("ticker", "")) for event in events)
    by_year = Counter(str(parse_utc(event.get("available_at_utc")).year) for event in events if parse_utc(event.get("available_at_utc")))
    return {
        "event_count": len(events),
        "ticker_count": len(by_ticker),
        "event_type_counts": dict(sorted(by_type.items())),
        "event_subtype_counts": dict(sorted(by_subtype.items())),
        "source_counts": dict(sorted(by_source.items())),
        "top_tickers": dict(by_ticker.most_common(20)),
        "year_counts": dict(sorted(by_year.items())),
    }


def coverage_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: Counter[tuple[str, int, str]] = Counter()
    for event in events:
        available = parse_utc(event.get("available_at_utc"))
        if not available:
            continue
        counts[(str(event.get("ticker", "")), available.year, str(event.get("event_type", "")))] += 1
    return [
        {"ticker": ticker, "year": year, "event_type": event_type, "event_count": count}
        for (ticker, year, event_type), count in sorted(counts.items())
    ]


def daily_feature_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        decision = parse_utc(event.get("decision_time_utc"))
        if not decision:
            continue
        decision_date = decision.astimezone(NY_TZ).date().isoformat()
        grouped[(str(event.get("ticker", "")), decision_date)].append(event)
    rows: list[dict[str, Any]] = []
    for (ticker, decision_date), items in sorted(grouped.items()):
        row: dict[str, Any] = {"ticker": ticker, "decision_date": decision_date, "event_count_total": len(items)}
        type_counts = Counter(str(item.get("event_type", "")) for item in items)
        for event_type in ["earnings", "guidance", "corporate_action", "risk_stress", "fundamental_fact", "sec_filing"]:
            row[f"{event_type}_event_count"] = type_counts.get(event_type, 0)
        for feature in EVENT_FEATURE_COLUMNS:
            values = []
            for item in items:
                value = (item.get("extracted_features") or {}).get(feature)
                if isinstance(value, (int, float)):
                    values.append(float(value))
            row[f"{feature}_mean"] = sum(values) / len(values) if values else 0.0
            row[f"{feature}_max"] = max(values) if values else 0.0
        rows.append(row)
    return rows


def write_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
