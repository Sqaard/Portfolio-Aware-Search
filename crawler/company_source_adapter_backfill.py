"""Backfill company-official documents from static, sitemap, vendor, and PDF sources.

This is a coverage-oriented companion to company_source_archive_discovery.py.
It targets tickers that have no accepted Company IR/Q4 documents yet and tries
source-specific discovery paths before pushing sources to the vendor backlog.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timezone
from pathlib import Path
from typing import Any, Iterable

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawler.company_source_archive_discovery import (
    CandidateLink,
    DiscoveryConfig,
    FetchResult,
    clean_text,
    content_word_count,
    date_only_to_available_iso,
    discover_q4_feed_candidates,
    discover_rss_candidates,
    discover_wordpress_candidates,
    event_tags_from_source,
    fetch_url,
    infer_published_at,
    make_session,
    parse_human_month_date,
    parse_url_date,
    risk_terms_from_text,
    source_type_to_document_source_type,
    stable_doc_id,
    utc_now_iso,
    validate_detail_candidate,
    visible_text_from_soup,
    write_csv,
)
from crawler.source_registry import canonicalize_url
from finportfolio_ir.io_utils import local_project_path, read_jsonl, write_jsonl
from finportfolio_ir.schema import FinancialDocument
from indexing.entity_linking import load_ticker_metadata


DEFAULT_USER_AGENT = "FinPortfolioIR/0.1 company-source-adapter-backfill; Mozilla/5.0"
YEAR_PATTERN = re.compile(r"20[0-2][0-9]")
PDF_EXT_PATTERN = re.compile(r"\.pdf(?:$|[?#])", re.I)


@dataclass(frozen=True)
class SitemapUrl:
    url: str
    lastmod: str = ""


def target_terms(source_type: str) -> tuple[str, ...]:
    text = source_type.lower()
    if "earnings" in text or "quarter" in text:
        return (
            "earnings",
            "quarter",
            "quarterly",
            "financial-results",
            "financial_results",
            "results",
            "q1",
            "q2",
            "q3",
            "q4",
        )
    if "press" in text or "news" in text:
        return ("press", "release", "news", "newsroom", "media", "announces")
    if "presentation" in text or "event" in text:
        return ("presentation", "presentations", "events", "webcast", "investor-day", "conference")
    if "report" in text or "annual" in text:
        return ("annual", "report", "reports", "proxy", "financial", "shareholder")
    return ("investor", "news", "financial", "report", "earnings", "press")


def candidate_available_at(url: str, text: str = "", fallback: str = "") -> str:
    url_date = parse_url_date(url)
    if url_date:
        return date_only_to_available_iso(url_date)
    human = parse_human_month_date(text[:500])
    if human:
        return date_only_to_available_iso(human)
    year_match = YEAR_PATTERN.search(url)
    if year_match:
        year = int(year_match.group(0))
        # Some static investor pages expose only /YYYY/ in PDF paths. Use
        # year-end as a conservative availability fallback until a better
        # source-specific date is available.
        return date_only_to_available_iso(date(year, 12, 31))
    return fallback


def in_year_range(iso_value: str, start_year: int, end_year: int) -> bool:
    return bool(iso_value and iso_value[:4].isdigit() and start_year <= int(iso_value[:4]) <= end_year)


def same_host_or_subdomain(url_a: str, url_b: str) -> bool:
    host_a = urllib.parse.urlparse(url_a).netloc.lower().removeprefix("www.")
    host_b = urllib.parse.urlparse(url_b).netloc.lower().removeprefix("www.")
    return bool(host_a and host_b and (host_a == host_b or host_a.endswith("." + host_b) or host_b.endswith("." + host_a)))


def source_page_link_candidates(
    row: dict[str, str],
    source_result: FetchResult,
    config: DiscoveryConfig,
) -> list[CandidateLink]:
    if not source_result.status_code or source_result.status_code >= 400:
        return []
    soup = BeautifulSoup(source_result.text or "", "html.parser")
    terms = target_terms(row.get("source_type", ""))
    candidates: list[tuple[int, CandidateLink]] = []
    seen: set[str] = set()
    for link in soup.find_all("a", href=True):
        href = str(link.get("href", ""))
        absolute = canonicalize_url(urllib.parse.urljoin(source_result.final_url or row["url"], href))
        if absolute in seen:
            continue
        is_pdf = bool(PDF_EXT_PATTERN.search(absolute.lower()))
        if not is_pdf and not same_host_or_subdomain(absolute, row["url"]):
            continue
        lowered = absolute.lower()
        parent_text = clean_text((link.parent.get_text(" ", strip=True) if link.parent else "") or link.get_text(" ", strip=True))
        joined = f"{lowered} {parent_text.lower()}"
        if not (PDF_EXT_PATTERN.search(lowered) or any(term in joined for term in terms)):
            continue
        if not (YEAR_PATTERN.search(joined) or parse_human_month_date(parent_text) or PDF_EXT_PATTERN.search(lowered)):
            continue
        available_at = candidate_available_at(absolute, parent_text)
        score = (
            (12 if PDF_EXT_PATTERN.search(lowered) else 0)
            + (8 if parse_human_month_date(parent_text) else 0)
            + sum(2 for term in terms if term in joined)
            - (6 if re.search(r"/20[0-2][0-9]/?$", lowered) and not PDF_EXT_PATTERN.search(lowered) else 0)
        )
        candidates.append(
            (
                score,
                CandidateLink(
                    url=absolute,
                    source_url=row["url"],
                    discovery_method="source_page_link",
                    anchor_text=parent_text[:400],
                    candidate_year=int(available_at[:4]) if available_at[:4].isdigit() else None,
                    api_published_at=available_at,
                    api_payload_url=row["url"],
                ),
            )
        )
        seen.add(absolute)
    candidates.sort(key=lambda item: (item[0], item[1].api_published_at, item[1].url), reverse=True)
    return [candidate for _, candidate in candidates[: config.max_detail_candidates_per_source]]


def embedded_pdf_candidates(
    row: dict[str, str],
    source_result: FetchResult,
    config: DiscoveryConfig,
    *,
    method: str = "embedded_pdf",
) -> list[CandidateLink]:
    """Find PDF URLs embedded in static HTML, JSON blobs, and widget state."""
    if not source_result.status_code or source_result.status_code >= 400 or not source_result.text:
        return []
    base_url = source_result.final_url or row["url"]
    terms = target_terms(row.get("source_type", ""))
    soup = BeautifulSoup(source_result.text or "", "html.parser")
    raw_candidates: list[tuple[str, str]] = []

    for tag in soup.find_all(True):
        for attr in ("href", "src", "data-href", "data-src", "data-url", "data-download-url"):
            value = tag.get(attr)
            if not value:
                continue
            value_text = str(value)
            if PDF_EXT_PATTERN.search(value_text):
                context = clean_text(tag.get_text(" ", strip=True) or tag.parent.get_text(" ", strip=True) if tag.parent else "")
                raw_candidates.append((value_text, context))

    token_pattern = re.compile(r"(?:https?:)?//[^\"'<>\s]+?\.pdf(?:\?[^\"'<>\s]*)?|/[^\"'<>\s]+?\.pdf(?:\?[^\"'<>\s]*)?", re.I)
    html = source_result.text or ""
    for match in token_pattern.finditer(html):
        prefix = html[max(0, match.start() - 10) : match.start()].lower()
        if prefix.endswith("public:") or prefix.endswith("private:"):
            continue
        start = max(0, match.start() - 160)
        end = min(len(html), match.end() + 220)
        raw_candidates.append((match.group(0), clean_text(html[start:end])))

    candidates: list[tuple[int, CandidateLink]] = []
    seen: set[str] = set()
    for raw_url, context in raw_candidates:
        if raw_url.startswith("private://") or raw_url.startswith("public://"):
            continue
        if raw_url.startswith("//"):
            raw_url = f"{urllib.parse.urlparse(base_url).scheme}:{raw_url}"
        absolute = canonicalize_url(urllib.parse.urljoin(base_url, raw_url))
        if absolute in seen or not PDF_EXT_PATTERN.search(absolute):
            continue
        seen.add(absolute)
        joined = f"{absolute.lower()} {context.lower()}"
        available_at = candidate_available_at(absolute, context)
        has_year = bool(YEAR_PATTERN.search(joined))
        if not has_year and not available_at:
            continue
        term_score = sum(2 for term in terms if term in joined)
        in_range_score = 8 if in_year_range(available_at, config.start_year, config.end_year) else 0
        report_score = 4 if any(term in joined for term in ("earnings", "annual", "report", "results", "presentation", "transcript")) else 0
        score = in_range_score + term_score + report_score
        if score <= 0:
            continue
        title = context[:220] or Path(urllib.parse.urlparse(absolute).path).name
        candidates.append(
            (
                score,
                CandidateLink(
                    url=absolute,
                    source_url=row["url"],
                    discovery_method=method,
                    anchor_text=title,
                    candidate_year=int(available_at[:4]) if available_at[:4].isdigit() else None,
                    api_published_at=available_at,
                    api_payload_url=base_url,
                ),
            )
        )
    candidates.sort(key=lambda item: (item[0], item[1].api_published_at, item[1].url), reverse=True)
    return [candidate for _, candidate in candidates[: config.max_detail_candidates_per_source]]


def detail_page_embedded_pdf_candidates(
    row: dict[str, str],
    detail_candidates: list[CandidateLink],
    fetcher,
    config: DiscoveryConfig,
    *,
    max_pages: int = 10,
) -> list[CandidateLink]:
    candidates: list[CandidateLink] = []
    pages_seen = 0
    for detail_candidate in detail_candidates:
        if PDF_EXT_PATTERN.search(detail_candidate.url):
            continue
        if detail_candidate.api_published_at and not in_year_range(detail_candidate.api_published_at, config.start_year, config.end_year):
            continue
        result = fetcher(detail_candidate.url)
        if not result.status_code or result.status_code >= 400 or not result.text:
            continue
        pdfs = embedded_pdf_candidates(row, result, config, method="detail_embedded_pdf")
        for pdf in pdfs:
            if detail_candidate.api_published_at:
                pdf = CandidateLink(
                    url=pdf.url,
                    source_url=pdf.source_url,
                    discovery_method=pdf.discovery_method,
                    anchor_text=pdf.anchor_text or detail_candidate.anchor_text,
                    candidate_year=detail_candidate.candidate_year,
                    api_title=pdf.api_title,
                    api_body=pdf.api_body,
                    api_published_at=detail_candidate.api_published_at,
                    api_payload_url=detail_candidate.url,
                )
            candidates.append(pdf)
        pages_seen += 1
        if pages_seen >= max_pages or len(candidates) >= config.max_detail_candidates_per_source:
            break
    return candidates[: config.max_detail_candidates_per_source]


def sitemap_roots(source_url: str) -> list[str]:
    parsed = urllib.parse.urlparse(source_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    roots = [f"{root}/sitemap.xml"]
    if "apple.com" in parsed.netloc and "/newsroom" in parsed.path:
        roots.insert(0, f"{root}/newsroom/sitemap.xml")
    return list(dict.fromkeys(roots))


def parse_sitemap_date(value: str) -> str:
    value = clean_text(value)
    if not value:
        return ""
    try:
        from finportfolio_ir.time_utils import to_utc_iso

        return to_utc_iso(value)
    except Exception:
        match = re.match(r"(20[0-2][0-9])-(\d{2})-(\d{2})", value)
        if match:
            return date_only_to_available_iso(date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
    return ""


def fetch_sitemap_urls(
    root_urls: Iterable[str],
    fetcher,
    *,
    max_sitemaps: int = 30,
    max_urls: int = 8000,
) -> list[SitemapUrl]:
    urls: list[SitemapUrl] = []
    queue = list(root_urls)
    seen: set[str] = set()
    sitemaps_seen = 0
    while queue and sitemaps_seen < max_sitemaps and len(urls) < max_urls:
        sitemap_url = queue.pop(0)
        if sitemap_url in seen:
            continue
        seen.add(sitemap_url)
        result = fetcher(sitemap_url)
        if not result.status_code or result.status_code >= 400:
            continue
        try:
            root = ET.fromstring(result.text.encode("utf-8"))
        except ET.ParseError:
            continue
        sitemaps_seen += 1
        namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        if root.tag.endswith("sitemapindex"):
            for loc in root.findall(".//sm:sitemap/sm:loc", namespace):
                if loc.text and len(queue) < max_sitemaps * 2:
                    queue.append(clean_text(loc.text))
            continue
        for url_node in root.findall(".//sm:url", namespace):
            loc_node = url_node.find("sm:loc", namespace)
            if loc_node is None or not loc_node.text:
                continue
            lastmod_node = url_node.find("sm:lastmod", namespace)
            urls.append(SitemapUrl(canonicalize_url(loc_node.text), parse_sitemap_date(lastmod_node.text if lastmod_node is not None else "")))
            if len(urls) >= max_urls:
                break
    return urls


def sitemap_candidates(
    row: dict[str, str],
    sitemap_urls: list[SitemapUrl],
    config: DiscoveryConfig,
) -> list[CandidateLink]:
    terms = target_terms(row.get("source_type", ""))
    source_path_terms = [part for part in re.split(r"[-_/]+", urllib.parse.urlparse(row["url"]).path.lower()) if len(part) >= 4]
    candidates: list[tuple[int, CandidateLink]] = []
    for item in sitemap_urls:
        if not same_host_or_subdomain(item.url, row["url"]):
            continue
        lowered = item.url.lower()
        available_at = candidate_available_at(item.url, "", item.lastmod)
        has_safe_date = in_year_range(available_at, config.start_year, config.end_year)
        has_year = bool(YEAR_PATTERN.search(lowered))
        term_score = sum(1 for term in terms if term in lowered)
        path_score = sum(1 for term in source_path_terms if term in lowered)
        if not has_safe_date and not has_year:
            continue
        if term_score == 0 and path_score == 0 and not PDF_EXT_PATTERN.search(lowered):
            continue
        score = term_score * 5 + path_score * 2 + (4 if PDF_EXT_PATTERN.search(lowered) else 0) + (3 if has_safe_date else 0)
        candidates.append(
            (
                score,
                CandidateLink(
                    url=item.url,
                    source_url=row["url"],
                    discovery_method="sitemap",
                    anchor_text="",
                    candidate_year=int(available_at[:4]) if available_at[:4].isdigit() else None,
                    api_published_at=available_at,
                    api_payload_url="sitemap",
                ),
            )
        )
    candidates.sort(key=lambda item: (item[0], item[1].api_published_at, item[1].url), reverse=True)
    return [candidate for _, candidate in candidates[: config.max_detail_candidates_per_source]]


def wordpress_roots(source_url: str, source_html: str = "") -> list[str]:
    parsed = urllib.parse.urlparse(source_url)
    roots = {f"{parsed.scheme}://{parsed.netloc}/wp-json"}
    for match in re.findall(r"https?://[^\"'<>\s]+/wp-json", source_html or ""):
        roots.add(match.rstrip("/"))
    return sorted(roots)


def wordpress_candidates(
    row: dict[str, str],
    source_result: FetchResult,
    fetcher,
    config: DiscoveryConfig,
) -> list[CandidateLink]:
    candidates: list[CandidateLink] = []
    for root in wordpress_roots(row["url"], source_result.text):
        for year in range(config.end_year, config.start_year - 1, -1):
            for page in range(1, config.wp_max_pages_per_year + 1):
                params = {
                    "per_page": config.wp_page_size,
                    "page": page,
                    "after": f"{year}-01-01T00:00:00",
                    "before": f"{year}-12-31T23:59:59",
                    "_fields": "date_gmt,link,title,content,excerpt",
                }
                api_url = f"{root.rstrip('/')}/wp/v2/posts?{urllib.parse.urlencode(params)}"
                result = fetcher(api_url)
                if not result.status_code or result.status_code >= 400:
                    break
                try:
                    payload = json.loads(result.text)
                except json.JSONDecodeError:
                    break
                if not isinstance(payload, list) or not payload:
                    break
                for item in payload:
                    link = str(item.get("link") or "")
                    if not link:
                        continue
                    title_html = str((item.get("title") or {}).get("rendered", ""))
                    body_html = str((item.get("content") or {}).get("rendered") or (item.get("excerpt") or {}).get("rendered") or "")
                    title = clean_text(BeautifulSoup(title_html, "html.parser").get_text(" "))
                    body = clean_text(BeautifulSoup(body_html, "html.parser").get_text(" "))
                    published = parse_sitemap_date(str(item.get("date_gmt") or ""))
                    joined = f"{link.lower()} {title.lower()} {body[:500].lower()}"
                    if not any(term in joined for term in target_terms(row.get("source_type", ""))):
                        continue
                    candidates.append(
                        CandidateLink(
                            url=canonicalize_url(link),
                            source_url=row["url"],
                            discovery_method="wordpress_rest_backfill",
                            anchor_text=title,
                            candidate_year=year,
                            api_title=title,
                            api_body=body_html,
                            api_published_at=published,
                            api_payload_url=api_url,
                        )
                    )
                    if len(candidates) >= config.max_detail_candidates_per_source:
                        return candidates
    return candidates


def extract_pdf_text_with_pypdf(pdf_bytes: bytes, max_pages: int = 12) -> str:
    def write_temp_pdf() -> Path:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            handle.write(pdf_bytes)
            return Path(handle.name)

    try:
        from pypdf import PdfReader  # type: ignore

        pdf_path = write_temp_pdf()
        try:
            reader = PdfReader(str(pdf_path))
            if getattr(reader, "is_encrypted", False):
                try:
                    reader.decrypt("")
                except Exception:
                    pass
            return "\n".join((page.extract_text() or "") for page in reader.pages[:max_pages])
        finally:
            pdf_path.unlink(missing_ok=True)
    except Exception:
        runtime = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "python" / "python.exe"
        if runtime.exists():
            pdf_path = write_temp_pdf()
            script = (
                "import sys; from pypdf import PdfReader; "
                "r=PdfReader(sys.argv[1]); "
                "\ntry:\n"
                "    r.decrypt('') if getattr(r, 'is_encrypted', False) else None\n"
                "except Exception:\n"
                "    pass\n"
                f"print('\\n'.join((p.extract_text() or '') for p in r.pages[:{max_pages}]))"
            )
            try:
                completed = subprocess.run(
                    [str(runtime), "-c", script, str(pdf_path)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=40,
                )
                if completed.returncode == 0 and completed.stdout.strip():
                    return completed.stdout
            except Exception:
                pass
            finally:
                pdf_path.unlink(missing_ok=True)
        node_runtime = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node" / "bin" / "node.exe"
        pdfjs_path = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node" / "node_modules" / "pdfjs-dist" / "legacy" / "build" / "pdf.mjs"
        if not node_runtime.exists() or not pdfjs_path.exists():
            return ""
        pdf_path = write_temp_pdf()
        node_script = f"""
const fs = require('fs');
(async () => {{
  globalThis.DOMMatrix = class {{ constructor(){{}} multiply(){{return this;}} translate(){{return this;}} scale(){{return this;}} rotate(){{return this;}} }};
  globalThis.ImageData = class {{}};
  globalThis.Path2D = class {{}};
  const pdfjsLib = await import('file:///{str(pdfjs_path).replace(chr(92), '/')}');
  const data = new Uint8Array(fs.readFileSync(process.argv[1]));
  const doc = await pdfjsLib.getDocument({{ data, useWorkerFetch: false, isEvalSupported: false, useSystemFonts: true }}).promise;
  const pages = [];
  for (let i = 1; i <= Math.min(doc.numPages, {max_pages}); i++) {{
    const page = await doc.getPage(i);
    const content = await page.getTextContent();
    pages.push(content.items.map(item => item.str || '').join(' '));
  }}
  console.log(pages.join('\\n'));
}})().catch(err => {{ console.error(err && err.stack ? err.stack : String(err)); process.exit(1); }});
"""
        try:
            completed = subprocess.run(
                [str(node_runtime), "-e", node_script, str(pdf_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            return completed.stdout if completed.returncode == 0 else ""
        except Exception:
            return ""
        finally:
            pdf_path.unlink(missing_ok=True)


def validate_pdf_candidate(
    row: dict[str, str],
    candidate: CandidateLink,
    fetcher,
    config: DiscoveryConfig,
    metadata: dict[str, Any],
    ingested_at: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    result = fetcher(candidate.url, binary=True)
    manifest = {
        "ticker": row.get("ticker", "").upper(),
        "company": row.get("company", ""),
        "source_type": row.get("source_type", ""),
        "source_url": row.get("url", ""),
        "candidate_url": candidate.url,
        "final_url": result.final_url,
        "discovery_method": candidate.discovery_method,
        "anchor_text": candidate.anchor_text,
        "candidate_year": candidate.candidate_year or "",
        "http_status": result.status_code or "",
        "content_type": result.content_type,
        "accepted": "no",
        "reject_reason": "",
        "title": candidate.anchor_text[:160],
        "published_at": "",
        "available_at": "",
        "body_word_count": 0,
        "canonical_url": canonicalize_url(result.final_url or candidate.url),
        "api_payload_url": candidate.api_payload_url,
    }
    if not result.status_code or result.status_code >= 400:
        manifest["reject_reason"] = result.error or f"http_status_{result.status_code}"
        return None, manifest
    pdf_bytes = getattr(result, "binary_content", b"")  # type: ignore[attr-defined]
    if not pdf_bytes:
        manifest["reject_reason"] = "missing_pdf_bytes"
        return None, manifest
    body = clean_text(extract_pdf_text_with_pypdf(pdf_bytes))
    word_count = content_word_count(body)
    if word_count < config.min_body_words:
        manifest["reject_reason"] = "pdf_body_too_short"
        manifest["body_word_count"] = word_count
        return None, manifest
    published_at = candidate_available_at(candidate.url, f"{candidate.anchor_text} {body[:2000]}", candidate.api_published_at)
    manifest["published_at"] = published_at
    manifest["available_at"] = published_at
    manifest["body_word_count"] = word_count
    if not in_year_range(published_at, config.start_year, config.end_year):
        manifest["reject_reason"] = "missing_or_outside_published_at"
        return None, manifest
    title = candidate.anchor_text or body.split(".")[0][:160] or Path(urllib.parse.urlparse(candidate.url).path).name
    ticker = row.get("ticker", "").upper()
    ticker_meta = metadata.get(ticker)
    company_name = getattr(ticker_meta, "official_name", "") or getattr(ticker_meta, "company_name", "") or row.get("company", "")
    canonical = canonicalize_url(result.final_url or candidate.url)
    source_type = source_type_to_document_source_type(row.get("source_type", ""))
    record = {
        "doc_id": stable_doc_id(ticker, canonical),
        "title": clean_text(title),
        "body": body[:250_000],
        "source": f"company_official_{ticker.lower()}",
        "source_type": source_type,
        "url": result.final_url or candidate.url,
        "source_registry_id": "company_ir_adapter_backfill",
        "canonical_url": canonical,
        "source_reliability_tier": "company",
        "robots_policy": row.get("robots_policy", "Use official company pages; respect robots and rate limits."),
        "last_url_check_at": ingested_at,
        "fetch_status": "ok",
        "content_license_note": "Official issuer PDF material; preserve source URL and avoid redistributing full text beyond research cache.",
        "published_at": published_at,
        "first_seen_at": published_at,
        "available_at": published_at,
        "ingested_at": ingested_at,
        "tickers_detected": [ticker],
        "matched_tickers": [ticker],
        "matched_holdings": [ticker],
        "company_names_detected": [company_name] if company_name else [],
        "sectors_detected": [getattr(ticker_meta, "sector", "")] if ticker_meta and getattr(ticker_meta, "sector", "") else [],
        "sector_tags": [getattr(ticker_meta, "sector", "")] if ticker_meta and getattr(ticker_meta, "sector", "") else [],
        "event_tags": event_tags_from_source(title, row.get("source_type", ""), body),
        "risk_terms": risk_terms_from_text(body),
        "source_credibility": 0.78,
        "event_type": source_type,
        "language": "en",
        "discovery_source_url": row.get("url", ""),
        "discovery_method": candidate.discovery_method,
        "discovery_anchor_text": candidate.anchor_text,
        "api_payload_url": candidate.api_payload_url,
        "published_at_source": "pdf_candidate_or_text",
        "body_word_count": word_count,
    }
    try:
        document = FinancialDocument.from_dict(record)
    except (KeyError, ValueError) as exc:
        manifest["reject_reason"] = f"schema_error:{exc}"
        return None, manifest
    manifest["accepted"] = "yes"
    manifest["reject_reason"] = ""
    manifest["title"] = record["title"]
    return document.to_dict() | {
        "discovery_source_url": record["discovery_source_url"],
        "discovery_method": record["discovery_method"],
        "discovery_anchor_text": record["discovery_anchor_text"],
        "api_payload_url": record["api_payload_url"],
        "published_at_source": record["published_at_source"],
        "body_word_count": word_count,
    }, manifest


def make_binary_fetcher(session: requests.Session, timeout_seconds: int):
    def fetcher(url: str, binary: bool = False):
        if binary:
            try:
                response = session.get(url, timeout=timeout_seconds, allow_redirects=True)
                result = FetchResult(
                    url=url,
                    final_url=response.url,
                    status_code=int(response.status_code),
                    content_type=response.headers.get("content-type", ""),
                    text="",
                )
                object.__setattr__(result, "binary_content", response.content[:20_000_000])
                return result
            except requests.RequestException as exc:
                return FetchResult(url=url, final_url="", status_code=None, content_type="", text="", error=str(exc)[:500])
        return fetch_url(url, session=session, timeout_seconds=timeout_seconds)

    return fetcher


def candidate_key(candidate: CandidateLink) -> str:
    return canonicalize_url(candidate.url)


def fallback_source_url(row: dict[str, str], source_result: FetchResult) -> str:
    ticker = row.get("ticker", "").upper()
    source_url = canonicalize_url(row.get("url", ""))
    if ticker == "AMGN" and urllib.parse.urlparse(source_url).netloc.lower() in {"investors.amgen.com", "amgen.gcs-web.com"}:
        if not source_result.status_code or source_result.status_code >= 500 or source_result.error:
            return "https://www.amgen.com/newsroom/press-releases"
    return ""


def discover_candidates_for_row(
    row: dict[str, str],
    fetcher,
    sitemap_cache: dict[str, list[SitemapUrl]],
    config: DiscoveryConfig,
    max_sitemaps_per_host: int,
) -> tuple[list[CandidateLink], dict[str, Any]]:
    source_url = canonicalize_url(row["url"])
    source_result = fetcher(source_url)
    fallback_url = fallback_source_url(row, source_result)
    if fallback_url:
        fallback_result = fetcher(fallback_url)
        if fallback_result.status_code and fallback_result.status_code < 500:
            source_url = canonicalize_url(fallback_url)
            source_result = fallback_result
    effective_row = dict(row)
    effective_row["url"] = source_url
    source_html = source_result.text if source_result.status_code and source_result.status_code < 500 else ""
    candidates: list[CandidateLink] = []
    q4_count = wp_count = rss_count = source_link_count = embedded_pdf_count = detail_pdf_count = sitemap_count = 0

    if row.get("review_vendor_profile") == "q4_or_q4_like" or "/default.aspx" in source_url.lower():
        q4_candidates = discover_q4_feed_candidates(source_url, fetcher, config)
        q4_count = len(q4_candidates)
        candidates.extend(q4_candidates)

    if row.get("review_vendor_profile") == "wordpress" or "wp-json" in (source_html or "").lower():
        wp_candidates = wordpress_candidates(effective_row, source_result, fetcher, config)
        wp_count = len(wp_candidates)
        candidates.extend(wp_candidates)

    if source_html:
        rss_candidates = discover_rss_candidates(source_result.final_url or source_url, source_html, fetcher, config)
        rss_count = len(rss_candidates)
        candidates.extend(rss_candidates)

    link_candidates = source_page_link_candidates(effective_row, source_result, config)
    source_link_count = len(link_candidates)
    candidates.extend(link_candidates)

    pdf_candidates = embedded_pdf_candidates(effective_row, source_result, config)
    embedded_pdf_count = len(pdf_candidates)
    candidates.extend(pdf_candidates)

    detail_pdf_candidates = detail_page_embedded_pdf_candidates(effective_row, link_candidates, fetcher, config)
    detail_pdf_count = len(detail_pdf_candidates)
    candidates.extend(detail_pdf_candidates)

    host = urllib.parse.urlparse(source_url).netloc.lower()
    if host not in sitemap_cache:
        sitemap_cache[host] = fetch_sitemap_urls(
            sitemap_roots(source_url),
            fetcher,
            max_sitemaps=max_sitemaps_per_host,
            max_urls=8000,
        )
    sm_candidates = sitemap_candidates(effective_row, sitemap_cache[host], config)
    sitemap_count = len(sm_candidates)
    candidates.extend(sm_candidates)

    seen: set[str] = set()
    deduped: list[CandidateLink] = []
    for candidate in candidates:
        key = candidate_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
        if len(deduped) >= config.max_detail_candidates_per_source:
            break

    return deduped, {
        "ticker": row.get("ticker", "").upper(),
        "company": row.get("company", ""),
        "source_type": row.get("source_type", ""),
        "source_url": source_url,
        "original_source_url": canonicalize_url(row["url"]),
        "source_http_status": source_result.status_code or "",
        "source_final_url": source_result.final_url,
        "source_error": source_result.error,
        "vendor_profile": row.get("review_vendor_profile", ""),
        "q4_candidates_seen": q4_count,
        "wordpress_candidates_seen": wp_count,
        "rss_candidates_seen": rss_count,
        "source_page_link_candidates_seen": source_link_count,
        "embedded_pdf_candidates_seen": embedded_pdf_count,
        "detail_embedded_pdf_candidates_seen": detail_pdf_count,
        "sitemap_candidates_seen": sitemap_count,
        "candidate_links_seen": len(deduped),
    }


def covered_tickers_from_jsonl(paths: Iterable[str | Path]) -> set[str]:
    covered: set[str] = set()
    for path in paths:
        candidate = local_project_path(path)
        if not candidate.exists():
            continue
        for record in read_jsonl(candidate):
            for ticker in record.get("matched_tickers", []) or []:
                if ticker != "MARKET":
                    covered.add(str(ticker).upper())
    return covered


def run(args: argparse.Namespace) -> dict[str, Any]:
    sources_path = local_project_path(args.sources)
    with sources_path.open("r", encoding="utf-8-sig", newline="") as handle:
        sources = [{key: (value or "").strip() for key, value in row.items()} for row in csv.DictReader(handle)]
    covered = covered_tickers_from_jsonl(args.existing_documents)
    target_tickers = {ticker.upper() for ticker in args.ticker}
    if args.only_uncovered:
        target_tickers |= {row["ticker"].upper() for row in sources if row.get("ticker", "").upper() not in covered}
    if target_tickers:
        sources = [row for row in sources if row.get("ticker", "").upper() in target_tickers]

    config = DiscoveryConfig(
        start_year=args.start_year,
        end_year=args.end_year,
        max_archive_pages_per_source=0,
        max_detail_candidates_per_source=args.max_candidates_per_source,
        max_documents_per_source=args.max_documents_per_source,
        min_body_words=args.min_body_words,
        timeout_seconds=args.timeout_seconds,
        sleep_seconds=0.0,
        q4_page_size=args.q4_page_size,
        wp_page_size=args.wp_page_size,
        wp_max_pages_per_year=args.wp_max_pages_per_year,
        enable_q4_feed=True,
        enable_rss=False,
        enable_wordpress=True,
        enable_generic_html=False,
    )
    metadata = load_ticker_metadata(args.metadata)
    ingested_at = utc_now_iso()
    session = make_session(DEFAULT_USER_AGENT)
    fetcher = make_binary_fetcher(session, config.timeout_seconds)
    sitemap_cache: dict[str, list[SitemapUrl]] = {}
    documents: list[dict[str, Any]] = []
    detail_manifest: list[dict[str, Any]] = []
    source_manifest: list[dict[str, Any]] = []
    vendor_queue: list[dict[str, Any]] = []

    for row in sources:
        candidates, source_info = discover_candidates_for_row(row, fetcher, sitemap_cache, config, args.max_sitemaps_per_host)
        accepted_for_source = 0
        rejected_for_source = 0
        for candidate in candidates:
            if accepted_for_source >= config.max_documents_per_source:
                break
            if PDF_EXT_PATTERN.search(candidate.url):
                document, manifest = validate_pdf_candidate(row, candidate, fetcher, config, metadata, ingested_at)
            else:
                no_detail_fetch = candidate.api_body and candidate.api_published_at
                document, manifest = validate_detail_candidate(
                    row,
                    candidate,
                    (lambda url: FetchResult(url=url, final_url=url, status_code=404, content_type="", text="", error="detail_fetch_disabled_for_api_payload"))
                    if no_detail_fetch
                    else fetcher,
                    config,
                    metadata,
                    ingested_at,
                )
            detail_manifest.append(manifest)
            if document is None:
                rejected_for_source += 1
                continue
            documents.append(document)
            accepted_for_source += 1
        source_info["accepted_documents"] = accepted_for_source
        source_info["rejected_candidates_checked"] = rejected_for_source
        source_manifest.append(source_info)
        if accepted_for_source == 0:
            vendor_queue.append(
                {
                    "ticker": row.get("ticker", "").upper(),
                    "company": row.get("company", ""),
                    "source_type": row.get("source_type", ""),
                    "url": row.get("url", ""),
                    "vendor_profile": row.get("review_vendor_profile", ""),
                    "accepted_documents": accepted_for_source,
                    "candidate_links_seen": source_info["candidate_links_seen"],
                    "priority": "needs_browser_vendor_pdf_or_source_specific_adapter",
                    "recommendation": "Inspect source with browser/network tools or add a dedicated endpoint/parser.",
                }
            )

    seen_doc_ids: set[str] = set()
    per_ticker_counts: Counter[str] = Counter()
    deduped: list[dict[str, Any]] = []
    for document in sorted(documents, key=lambda doc: ((doc.get("matched_tickers") or [""])[0], doc.get("published_at", ""), doc.get("canonical_url", ""))):
        ticker = (document.get("matched_tickers") or [""])[0]
        if document["doc_id"] in seen_doc_ids:
            continue
        if args.max_documents_per_ticker and per_ticker_counts[ticker] >= args.max_documents_per_ticker:
            continue
        seen_doc_ids.add(document["doc_id"])
        per_ticker_counts[ticker] += 1
        deduped.append(document)

    write_jsonl(args.output_documents, deduped)
    write_csv(args.detail_manifest_output, detail_manifest)
    write_csv(args.source_manifest_output, source_manifest)
    write_csv(args.vendor_queue_output, vendor_queue)
    summary = {
        "created_at": utc_now_iso(),
        "sources_checked": len(source_manifest),
        "input_sources_after_filter": len(sources),
        "existing_company_doc_tickers": sorted(covered),
        "target_tickers": sorted(target_tickers),
        "documents_written": len(deduped),
        "documents_by_ticker": dict(Counter((doc.get("matched_tickers") or [""])[0] for doc in deduped)),
        "documents_by_source_type": dict(Counter(doc.get("source_type", "") for doc in deduped)),
        "documents_by_discovery_method": dict(Counter(doc.get("discovery_method", "") for doc in deduped)),
        "detail_candidates_checked": len(detail_manifest),
        "reject_reasons": dict(Counter(row.get("reject_reason", "") for row in detail_manifest if row.get("accepted") != "yes")),
        "vendor_queue_rows": len(vendor_queue),
        "outputs": {
            "documents": str(args.output_documents),
            "detail_manifest": str(args.detail_manifest_output),
            "source_manifest": str(args.source_manifest_output),
            "vendor_queue": str(args.vendor_queue_output),
            "summary": str(args.summary_output),
        },
    }
    local_project_path(args.summary_output).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill company official coverage through sitemap/static/vendor/PDF adapters.")
    parser.add_argument("--sources", required=True)
    parser.add_argument("--metadata", default="data/processed_documents/dow30_ticker_metadata.csv")
    parser.add_argument("--existing-documents", action="append", default=[])
    parser.add_argument("--output-documents", required=True)
    parser.add_argument("--detail-manifest-output", required=True)
    parser.add_argument("--source-manifest-output", required=True)
    parser.add_argument("--vendor-queue-output", required=True)
    parser.add_argument("--summary-output", required=True)
    parser.add_argument("--start-year", type=int, default=2010)
    parser.add_argument("--end-year", type=int, default=2023)
    parser.add_argument("--ticker", action="append", default=[])
    parser.add_argument("--only-uncovered", action="store_true")
    parser.add_argument("--max-candidates-per-source", type=int, default=80)
    parser.add_argument("--max-documents-per-source", type=int, default=12)
    parser.add_argument("--max-documents-per-ticker", type=int, default=40)
    parser.add_argument("--max-sitemaps-per-host", type=int, default=30)
    parser.add_argument("--min-body-words", type=int, default=100)
    parser.add_argument("--timeout-seconds", type=int, default=8)
    parser.add_argument("--q4-page-size", type=int, default=100)
    parser.add_argument("--wp-page-size", type=int, default=50)
    parser.add_argument("--wp-max-pages-per-year", type=int, default=2)
    return parser


def main() -> None:
    summary = run(build_parser().parse_args())
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
