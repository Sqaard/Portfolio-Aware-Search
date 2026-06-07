"""Discover validated official company archive documents.

This crawler is deliberately evidence-first. It does not treat an official
top-level IR/news URL as a document. Instead it discovers dated detail URLs,
fetches each detail page, extracts a title/body/date, and emits normalized
FinancialDocument records with source provenance.

The crawler also emits a vendor queue for sources whose archives are likely
behind Q4, GCS/Web, QuoteMedia, WordPress, or browser-only UI layers.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Union

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawler.source_registry import canonicalize_url
from finportfolio_ir.io_utils import local_project_path, write_jsonl
from finportfolio_ir.schema import FinancialDocument
from indexing.entity_linking import load_ticker_metadata


DEFAULT_USER_AGENT = (
    "FinPortfolioIR/0.1 company-source-archive-discovery "
    "contact=local research; Mozilla/5.0"
)

DETAIL_KEYWORDS = (
    "press release",
    "news release",
    "earnings",
    "quarter",
    "financial results",
    "announces",
    "announced",
    "reports",
    "reported",
    "guidance",
    "dividend",
    "share repurchase",
    "investor",
    "results",
)

ARCHIVE_TITLE_TERMS = (
    "archive",
    "archives",
    "news releases",
    "press releases",
    "quarterly earnings",
    "financial results",
    "announcements",
    "newsroom",
)

SKIP_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".webp",
    ".zip",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".mp3",
    ".mp4",
)

MONTH_NAMES = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


@dataclass(frozen=True)
class FetchResult:
    url: str
    final_url: str
    status_code: int | None
    content_type: str
    text: str
    error: str = ""


@dataclass(frozen=True)
class CandidateLink:
    url: str
    source_url: str
    discovery_method: str
    anchor_text: str = ""
    candidate_year: int | None = None
    api_title: str = ""
    api_body: str = ""
    api_published_at: str = ""
    api_payload_url: str = ""


@dataclass(frozen=True)
class DiscoveryConfig:
    start_year: int = 2010
    end_year: int = 2023
    max_archive_pages_per_source: int = 80
    max_detail_candidates_per_source: int = 250
    max_documents_per_source: int = 50
    min_body_words: int = 180
    timeout_seconds: int = 15
    sleep_seconds: float = 0.05
    q4_page_size: int = 100
    rss_item_limit: int = 200
    wp_page_size: int = 50
    wp_max_pages_per_year: int = 2
    enable_q4_feed: bool = True
    enable_rss: bool = True
    enable_wordpress: bool = True
    enable_generic_html: bool = True
    include_source_grades: tuple[str, ...] = (
        "crawler_ready",
        "browser_or_api_needed",
        "blocked_or_forbidden",
        "fail",
        "",
    )


Fetcher = Callable[[str], FetchResult]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def year_range(config: DiscoveryConfig) -> range:
    return range(config.start_year, config.end_year + 1)


def parse_bool(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def host_key(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
    parts = host.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def same_site(url_a: str, url_b: str) -> bool:
    key_a = host_key(url_a)
    key_b = host_key(url_b)
    if not key_a or not key_b:
        return False
    host_a = urllib.parse.urlparse(url_a).netloc.lower().removeprefix("www.")
    host_b = urllib.parse.urlparse(url_b).netloc.lower().removeprefix("www.")
    return key_a == key_b or host_a.endswith(f".{key_b}") or host_b.endswith(f".{key_a}")


def fetch_url(url: str, *, session: requests.Session, timeout_seconds: int, max_bytes: int = 5_000_000) -> FetchResult:
    try:
        request_headers = {}
        lowered_url = url.lower()
        if "/feed/" in lowered_url or ".svc/" in lowered_url or "/wp-json/" in lowered_url:
            request_headers["Accept"] = "application/json,text/javascript,*/*;q=0.8"
        response = session.get(url, timeout=timeout_seconds, allow_redirects=True, headers=request_headers)
        content = response.content[:max_bytes]
        text = decode_response_text(content, response.headers.get("content-type", ""), response.encoding)
        return FetchResult(
            url=url,
            final_url=response.url,
            status_code=int(response.status_code),
            content_type=response.headers.get("content-type", ""),
            text=text,
        )
    except requests.RequestException as exc:
        return FetchResult(url=url, final_url="", status_code=None, content_type="", text="", error=str(exc)[:500])


def mojibake_score(text: str) -> int:
    return sum(text.count(token) for token in ("Ã", "â", "�", "вЂ", "Р", "С"))


def decode_response_text(content: bytes, content_type: str = "", declared_encoding: str | None = None) -> str:
    head = content[:4096].lower()
    if b"charset=utf-8" in head or "charset=utf-8" in (content_type or "").lower():
        preferred = "utf-8"
    else:
        preferred = declared_encoding or "utf-8"
    try:
        text = content.decode(preferred, errors="replace")
    except LookupError:
        text = content.decode("utf-8", errors="replace")
    if preferred.lower() != "utf-8":
        utf8_text = content.decode("utf-8", errors="replace")
        if mojibake_score(utf8_text) < mojibake_score(text):
            return utf8_text
    return text


def make_session(user_agent: str = DEFAULT_USER_AGENT) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.8",
        }
    )
    return session


def extract_title(soup: BeautifulSoup) -> str:
    title = ""
    if soup.title:
        title = clean_text(soup.title.get_text(" ", strip=True))
    if not title:
        for selector in (
            {"property": "og:title"},
            {"name": "twitter:title"},
            {"name": "title"},
        ):
            tag = soup.find("meta", selector)
            if tag and tag.get("content"):
                title = clean_text(str(tag["content"]))
                break
    return title[:300]


def visible_text_from_soup(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "form"]):
        tag.decompose()
    body_parts: list[str] = []
    for selector in ("article", "main", "[role=main]", ".article", ".press-release", ".news-release", ".entry-content"):
        selected = soup.select(selector)
        if selected:
            text = clean_text(" ".join(item.get_text(" ", strip=True) for item in selected))
            if len(text.split()) > 80:
                body_parts.append(text)
                break
    if not body_parts:
        body_parts.append(clean_text(soup.get_text(" ", strip=True)))
    return body_parts[0]


def content_word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9\-']*", text or ""))


def year_tokens(text: str, *, start_year: int = 2010, end_year: int = 2026) -> set[int]:
    return {
        int(value)
        for value in re.findall(r"(?<!\d)(20[0-2][0-9])(?!\d)", text or "")
        if start_year <= int(value) <= end_year
    }


def parse_datetime_value(value: str) -> datetime | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        pass
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def date_only_to_available_iso(value: date) -> str:
    # Date-only company pages are often not timestamped. Use end-of-day UTC to
    # preserve point-in-time safety for market-open decision policies.
    return datetime.combine(value, dt_time(23, 59, 59), tzinfo=timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def parse_human_month_date(text: str) -> date | None:
    pattern = (
        r"\b("
        + "|".join(MONTH_NAMES)
        + r")\.?\s+([0-3]?\d),?\s+(20[0-2][0-9])\b"
    )
    match = re.search(pattern, text or "", flags=re.IGNORECASE)
    if not match:
        return None
    month = MONTH_NAMES[match.group(1).lower().rstrip(".")]
    day = int(match.group(2))
    year = int(match.group(3))
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_url_date(url: str) -> date | None:
    path = urllib.parse.urlparse(url).path
    patterns = (
        r"/(20[0-2][0-9])/(0?[1-9]|1[0-2])/(0?[1-9]|[12]\d|3[01])(?:/|$|-)",
        r"/(20[0-2][0-9])-(0?[1-9]|1[0-2])-(0?[1-9]|[12]\d|3[01])(?:/|$|-)",
        r"[-_/](0?[1-9]|1[0-2])[-_/](0?[1-9]|[12]\d|3[01])[-_/](20[0-2][0-9])(?:/|$|-)",
    )
    for index, pattern in enumerate(patterns):
        match = re.search(pattern, path)
        if not match:
            continue
        try:
            if index < 2:
                return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            return date(int(match.group(3)), int(match.group(1)), int(match.group(2)))
        except ValueError:
            return None
    return None


def infer_published_at(soup: BeautifulSoup, url: str, visible_text: str) -> tuple[str, str]:
    meta_selectors = (
        {"property": "article:published_time"},
        {"property": "og:published_time"},
        {"name": "date"},
        {"name": "Date"},
        {"name": "dc.date"},
        {"name": "DC.date"},
        {"name": "pubdate"},
        {"itemprop": "datePublished"},
    )
    for selector in meta_selectors:
        tag = soup.find("meta", selector)
        if tag and tag.get("content"):
            parsed = parse_datetime_value(str(tag["content"]))
            if parsed:
                return parsed.isoformat(timespec="seconds").replace("+00:00", "Z"), "meta"

    time_tag = soup.find("time")
    if time_tag:
        for attr in ("datetime", "content"):
            if time_tag.get(attr):
                parsed = parse_datetime_value(str(time_tag[attr]))
                if parsed:
                    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z"), "time_tag"
        human = parse_human_month_date(time_tag.get_text(" ", strip=True))
        if human:
            return date_only_to_available_iso(human), "time_tag_date_only"

    url_date = parse_url_date(url)
    if url_date:
        return date_only_to_available_iso(url_date), "url_date"

    human_date = parse_human_month_date(visible_text[:3000])
    if human_date:
        return date_only_to_available_iso(human_date), "body_date"

    return "", "missing"


def source_type_to_document_source_type(source_type: str) -> str:
    text = (source_type or "").lower()
    if "presentation" in text or "event" in text:
        return "company_presentation"
    if "annual" in text or "report" in text or "proxy" in text:
        return "company_financial_report"
    if "sec" in text or "filing" in text:
        return "company_sec_filing_hub"
    if "earning" in text or "financial" in text or "quarter" in text:
        return "company_earnings_release"
    if "press" in text or "news" in text:
        return "company_press_release"
    if "investor" in text:
        return "company_ir_archive"
    return "company_official_archive"


def event_tags_from_source(title: str, source_type: str, body: str) -> list[str]:
    text = f"{title} {source_type} {body[:2000]}".lower()
    tags: set[str] = {"company_official"}
    if any(term in text for term in ("earnings", "quarter", "financial results", "results")):
        tags.add("earnings_release_candidate")
    if any(term in text for term in ("guidance", "outlook", "forecast")):
        tags.add("guidance")
    if any(term in text for term in ("dividend", "share repurchase", "buyback")):
        tags.add("capital_return")
    if any(term in text for term in ("acquisition", "merger", "divestiture", "spin-off", "spinoff")):
        tags.add("mna")
    if any(term in text for term in ("lawsuit", "litigation", "regulatory", "investigation")):
        tags.add("legal_regulatory")
    return sorted(tags)


def risk_terms_from_text(body: str) -> list[str]:
    text = body.lower()
    risk_terms = []
    for term in (
        "inflation",
        "interest rates",
        "supply chain",
        "litigation",
        "regulation",
        "recession",
        "credit",
        "demand",
        "margin",
        "foreign exchange",
        "oil",
        "commodity",
    ):
        if term in text:
            risk_terms.append(term)
    return risk_terms


def stable_doc_id(ticker: str, canonical_url_value: str) -> str:
    digest = hashlib.sha1(canonical_url_value.encode("utf-8")).hexdigest()[:16]
    return f"company_{ticker.lower()}_{digest}"


def looks_like_archive_page(title: str, body: str, link_count: int) -> bool:
    lowered_title = (title or "").lower()
    lowered_preview = f"{title} {body[:1200]}".lower()
    if link_count >= 20 and any(term in lowered_preview for term in ARCHIVE_TITLE_TERMS):
        return True
    if content_word_count(body) < 220 and any(term in lowered_title for term in ARCHIVE_TITLE_TERMS):
        return True
    return False


def is_probably_detail_url(url: str, anchor_text: str) -> bool:
    lowered = f"{url} {anchor_text}".lower()
    if urllib.parse.urlparse(url).path.lower().endswith(SKIP_EXTENSIONS):
        return False
    if "mailto:" in lowered or "javascript:" in lowered:
        return False
    if any(keyword in lowered for keyword in DETAIL_KEYWORDS):
        return True
    if parse_url_date(url):
        return True
    if re.search(r"/20[0-2][0-9]/[01]?\d/", urllib.parse.urlparse(url).path):
        return True
    return False


def extract_links(base_url: str, html: str, config: DiscoveryConfig) -> list[CandidateLink]:
    soup = BeautifulSoup(html or "", "html.parser")
    links: list[CandidateLink] = []
    for anchor in soup.find_all("a", href=True):
        absolute = urllib.parse.urljoin(base_url, str(anchor.get("href") or ""))
        if not absolute.startswith(("http://", "https://")):
            continue
        if not same_site(base_url, absolute):
            continue
        text = clean_text(anchor.get_text(" ", strip=True))[:240]
        link_years = sorted(year_tokens(f"{absolute} {text}", start_year=config.start_year, end_year=config.end_year))
        if link_years or is_probably_detail_url(absolute, text):
            links.append(
                CandidateLink(
                    url=canonicalize_url(absolute),
                    source_url=base_url,
                    discovery_method="html_link",
                    anchor_text=text,
                    candidate_year=link_years[0] if link_years else None,
                )
            )
    return links


def normalize_api_html_body(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    return visible_text_from_soup(soup)


def q4_base_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))


def fetch_json(fetcher: Fetcher, url: str) -> dict[str, Any] | None:
    result = fetcher(url)
    if not result.status_code or result.status_code >= 400:
        return None
    try:
        return json.loads(result.text)
    except json.JSONDecodeError:
        return None


def q4_params(params: dict[str, Any]) -> str:
    return urllib.parse.urlencode(params, doseq=True)


def discover_q4_feed_candidates(
    source_url: str,
    fetcher: Fetcher,
    config: DiscoveryConfig,
) -> list[CandidateLink]:
    base = q4_base_url(source_url)
    year_params = {
        "LanguageId": 1,
        "bodyType": 0,
        "pressReleaseDateFilter": 3,
        "categoryId": "",
        "tagList": "",
    }
    years_url = f"{base}/feed/PressRelease.svc/GetPressReleaseYearList?{q4_params(year_params)}"
    years_payload = fetch_json(fetcher, years_url)
    years = []
    if years_payload:
        years = [
            int(year)
            for year in years_payload.get("GetPressReleaseYearListResult", [])
            if config.start_year <= int(year) <= config.end_year
        ]
    candidates: list[CandidateLink] = []
    for year in sorted(set(years), reverse=True):
        list_params = {
            "LanguageId": 1,
            "bodyType": 2,
            "pressReleaseDateFilter": 3,
            "categoryId": "",
            "pageSize": config.q4_page_size,
            "pageNumber": 0,
            "tagList": "",
            "includeTags": "true",
            "year": year,
            "excludeSelection": 1,
        }
        list_url = f"{base}/feed/PressRelease.svc/GetPressReleaseList?{q4_params(list_params)}"
        payload = fetch_json(fetcher, list_url)
        if not payload:
            continue
        for item in payload.get("GetPressReleaseListResult", []) or []:
            detail = str(item.get("LinkToDetailPage") or item.get("LinkToUrl") or item.get("DocumentPath") or "")
            if not detail:
                continue
            detail_url = urllib.parse.urljoin(base, detail)
            published = parse_datetime_value(str(item.get("PressReleaseDate") or ""))
            published_iso = published.isoformat(timespec="seconds").replace("+00:00", "Z") if published else ""
            headline = clean_text(str(item.get("Headline") or ""))
            api_body = str(item.get("Body") or item.get("ShortBody") or item.get("ShortDescription") or "")
            candidates.append(
                CandidateLink(
                    url=canonicalize_url(detail_url),
                    source_url=source_url,
                    discovery_method="q4_press_release_feed",
                    anchor_text=headline,
                    candidate_year=year,
                    api_title=headline,
                    api_body=api_body,
                    api_published_at=published_iso,
                    api_payload_url=list_url,
                )
            )
            if len(candidates) >= config.max_detail_candidates_per_source:
                return candidates
    return candidates


def discover_rss_candidates(
    source_url: str,
    source_html: str,
    fetcher: Fetcher,
    config: DiscoveryConfig,
) -> list[CandidateLink]:
    soup = BeautifulSoup(source_html or "", "html.parser")
    feed_urls: list[str] = []
    for link in soup.find_all("link", href=True):
        rel = " ".join(str(part).lower() for part in link.get("rel", []))
        type_value = str(link.get("type", "")).lower()
        if "alternate" in rel and ("rss" in type_value or "xml" in type_value):
            feed_urls.append(urllib.parse.urljoin(source_url, str(link["href"])))
    for suffix in ("rss", "feed", "feed.xml"):
        feed_urls.append(urllib.parse.urljoin(source_url.rstrip("/") + "/", suffix))

    candidates: list[CandidateLink] = []
    seen_feeds: set[str] = set()
    for feed_url in list(dict.fromkeys(feed_urls)):
        canonical_feed = canonicalize_url(feed_url)
        if canonical_feed in seen_feeds:
            continue
        seen_feeds.add(canonical_feed)
        result = fetcher(canonical_feed)
        if not result.status_code or result.status_code >= 400 or "<" not in result.text[:100]:
            continue
        try:
            root = ET.fromstring(result.text.encode("utf-8"))
        except ET.ParseError:
            continue
        items = root.findall(".//item")
        for item in items[: config.rss_item_limit]:
            title = clean_text(item.findtext("title", default=""))
            link = clean_text(item.findtext("link", default=""))
            description = item.findtext("description", default="") or ""
            pubdate = clean_text(item.findtext("pubDate", default=""))
            parsed = parse_datetime_value(pubdate)
            if not link or not parsed:
                continue
            year = parsed.year
            if year < config.start_year or year > config.end_year:
                continue
            candidates.append(
                CandidateLink(
                    url=canonicalize_url(link),
                    source_url=source_url,
                    discovery_method="rss_feed",
                    anchor_text=title,
                    candidate_year=year,
                    api_title=title,
                    api_body=description,
                    api_published_at=parsed.isoformat(timespec="seconds").replace("+00:00", "Z"),
                    api_payload_url=canonical_feed,
                )
            )
            if len(candidates) >= config.max_detail_candidates_per_source:
                return candidates
    return candidates


def discover_wordpress_candidates(
    source_url: str,
    source_html: str,
    fetcher: Fetcher,
    config: DiscoveryConfig,
) -> list[CandidateLink]:
    wp_roots: set[str] = set()
    soup = BeautifulSoup(source_html or "", "html.parser")
    for link in soup.find_all("link", href=True):
        href = urllib.parse.urljoin(source_url, str(link["href"]))
        if "/wp-json" in href:
            wp_roots.add(href.split("/wp-json", 1)[0] + "/wp-json")
    for match in re.findall(r"https?://[^\"'<>\s]+/wp-json", source_html or ""):
        wp_roots.add(match)
    if "/wp-json" in source_url:
        wp_roots.add(source_url.split("/wp-json", 1)[0] + "/wp-json")

    candidates: list[CandidateLink] = []
    for wp_root in sorted(wp_roots):
        for year in year_range(config):
            for page in range(1, config.wp_max_pages_per_year + 1):
                params = {
                    "per_page": config.wp_page_size,
                    "page": page,
                    "after": f"{year}-01-01T00:00:00",
                    "before": f"{year}-12-31T23:59:59",
                    "_fields": "date_gmt,link,title,content,excerpt",
                }
                api_url = f"{wp_root.rstrip('/')}/wp/v2/posts?{urllib.parse.urlencode(params)}"
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
                    title = clean_text(BeautifulSoup(str((item.get("title") or {}).get("rendered", "")), "html.parser").get_text(" "))
                    body_html = str((item.get("content") or {}).get("rendered") or (item.get("excerpt") or {}).get("rendered") or "")
                    published = parse_datetime_value(str(item.get("date_gmt") or ""))
                    published_iso = published.isoformat(timespec="seconds").replace("+00:00", "Z") if published else ""
                    candidates.append(
                        CandidateLink(
                            url=canonicalize_url(link),
                            source_url=source_url,
                            discovery_method="wordpress_rest",
                            anchor_text=title,
                            candidate_year=year,
                            api_title=title,
                            api_body=body_html,
                            api_published_at=published_iso,
                            api_payload_url=api_url,
                        )
                    )
                    if len(candidates) >= config.max_detail_candidates_per_source:
                        return candidates
    return candidates


def year_archive_candidates(source_url: str, year: int) -> list[str]:
    parsed = urllib.parse.urlparse(source_url)
    base_path = parsed.path or "/"
    path_no_slash = base_path.rstrip("/")
    base_no_query = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, path_no_slash or "/", "", "", ""))
    candidates: list[str] = []

    if not path_no_slash.lower().endswith((".aspx", ".html", ".htm", ".php")):
        candidates.extend(
            [
                f"{base_no_query}/{year}",
                f"{base_no_query}/{year}/",
            ]
        )
    if path_no_slash.lower().endswith("/archive") or path_no_slash.lower().endswith("/archives"):
        candidates.extend([f"{base_no_query}/{year}/", f"{base_no_query}/{year}"])

    separator = "&" if parsed.query else "?"
    candidates.extend(
        [
            f"{source_url}{separator}year={year}",
            f"{source_url}{separator}Year={year}",
            f"{source_url}{separator}category=financial&year={year}",
            f"{source_url}{separator}category=Financial&year={year}",
        ]
    )
    return list(dict.fromkeys(canonicalize_url(candidate) for candidate in candidates))


def classify_vendor_profile(url: str, html: str = "", status_code: int | None = None) -> tuple[str, str]:
    lowered = f"{url} {html[:80_000]}".lower()
    if status_code in {403, 429} and ("cloudflare" in lowered or "just a moment" in lowered or "cf-mitigated" in lowered):
        return "cloudflare_challenge", "Prefer alternate official endpoint; otherwise use manual/browser pass without attempting bypass."
    if "q4api" in lowered or "q4apikey" in lowered or "q4cdn.com" in lowered or "q4app.com" in lowered or "/default.aspx" in lowered:
        return "q4_or_q4_like", "Discover Q4 module/API parameters or use browser network capture to identify JSON endpoints."
    if "gcs-web.com" in lowered:
        return "gcs_web", "Use GCS/Web year/category pagination; validate detail pages after URL discovery."
    if "quotemedia" in lowered:
        return "quotemedia", "Use QuoteMedia-backed investor page parser; inspect XHR calls and avoid treating shell HTML as documents."
    if "wp-json" in lowered or "/wp-content/" in lowered:
        return "wordpress", "Use WordPress REST API or sitemap before HTML scraping."
    if "recaptcha" in lowered or "hcaptchasitekey" in lowered:
        return "captcha_module", "Use official alternate feed/API if available; browser rendering may reveal public archive links."
    if status_code is None or status_code >= 400:
        return "fetch_problem", "Recheck with requests/certifi/curl fallback and replace if still blocked."
    return "static_html_or_custom", "Use generic archive discovery first; add source adapter only if coverage is poor."


def should_try_q4_feed(source_url: str, vendor_profile: str, source_html: str = "") -> bool:
    lowered = f"{source_url} {source_html[:20_000]}".lower()
    return (
        vendor_profile in {"q4_or_q4_like", "cloudflare_challenge", "captcha_module"}
        or "/default.aspx" in lowered
        or "q4api" in lowered
        or "q4cdn.com" in lowered
        or "q4app.com" in lowered
    )


def validate_detail_candidate(
    row: dict[str, str],
    candidate: CandidateLink,
    fetcher: Fetcher,
    config: DiscoveryConfig,
    metadata: dict[str, Any],
    ingested_at: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    api_body = normalize_api_html_body(candidate.api_body) if candidate.api_body else ""
    api_word_count = content_word_count(api_body)
    api_payload_candidate = bool(
        candidate.api_published_at
        and candidate.discovery_method in {"q4_press_release_feed", "wordpress_rest"}
    )
    api_payload_is_full_enough = bool(
        candidate.api_published_at
        and api_body
        and api_word_count >= config.min_body_words
        and candidate.discovery_method in {"q4_press_release_feed", "wordpress_rest"}
    )
    if api_payload_candidate:
        result = FetchResult(
            url=candidate.url,
            final_url=candidate.url,
            status_code=200,
            content_type="application/json_api_payload",
            text="",
            error="",
        )
    else:
        result = fetcher(candidate.url)
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
        "title": "",
        "published_at": "",
        "available_at": "",
        "body_word_count": 0,
        "canonical_url": canonicalize_url(result.final_url or candidate.url),
        "api_payload_url": candidate.api_payload_url,
    }
    use_api_payload = False
    if api_payload_candidate:
        use_api_payload = True
    elif not result.status_code or result.status_code >= 400:
        if api_body and candidate.api_published_at:
            use_api_payload = True
        else:
            manifest["reject_reason"] = result.error or f"http_status_{result.status_code}"
            return None, manifest
    elif "pdf" in result.content_type.lower() or candidate.url.lower().endswith(".pdf"):
        if api_body and candidate.api_published_at:
            use_api_payload = True
        else:
            manifest["reject_reason"] = "pdf_detail_not_supported_in_this_pass"
            return None, manifest
    elif "html" not in result.content_type.lower() and "<html" not in result.text.lower():
        if api_body and candidate.api_published_at:
            use_api_payload = True
        else:
            manifest["reject_reason"] = "non_html_detail"
            return None, manifest

    if use_api_payload:
        title = candidate.api_title or candidate.anchor_text
        body = api_body
        link_count = 0
        word_count = content_word_count(body)
        published_at = candidate.api_published_at
        date_source = f"{candidate.discovery_method}_published_at"
    else:
        soup = BeautifulSoup(result.text or "", "html.parser")
        title = extract_title(soup)
        body = visible_text_from_soup(soup)
        link_count = len(soup.find_all("a", href=True))
        word_count = content_word_count(body)
        published_at, date_source = infer_published_at(soup, result.final_url or candidate.url, body)
        if not published_at and candidate.api_published_at:
            published_at = candidate.api_published_at
            date_source = f"{candidate.discovery_method}_candidate_available_at"
        if (not published_at or word_count < config.min_body_words) and api_body and candidate.api_published_at:
            title = candidate.api_title or title or candidate.anchor_text
            body = api_body
            link_count = 0
            word_count = content_word_count(body)
            published_at = candidate.api_published_at
            date_source = f"{candidate.discovery_method}_published_at"

    manifest["title"] = title
    manifest["published_at"] = published_at
    manifest["available_at"] = published_at
    manifest["body_word_count"] = word_count
    manifest["date_source"] = date_source

    if not published_at:
        manifest["reject_reason"] = "missing_published_at"
        return None, manifest
    parsed_year = int(published_at[:4])
    if parsed_year < config.start_year or parsed_year > config.end_year:
        manifest["reject_reason"] = "published_at_outside_range"
        return None, manifest
    if word_count < config.min_body_words:
        manifest["reject_reason"] = "body_too_short"
        return None, manifest
    if looks_like_archive_page(title, body, link_count):
        manifest["reject_reason"] = "archive_or_index_page"
        return None, manifest

    ticker = row.get("ticker", "").upper()
    ticker_meta = metadata.get(ticker)
    company_name = (
        getattr(ticker_meta, "official_name", "")
        or getattr(ticker_meta, "company_name", "")
        or row.get("company", "")
    )
    source_type = source_type_to_document_source_type(row.get("source_type", ""))
    canonical = canonicalize_url(result.final_url or candidate.url)
    record = {
        "doc_id": stable_doc_id(ticker, canonical),
        "title": title,
        "body": body,
        "source": f"company_official_{ticker.lower()}",
        "source_type": source_type,
        "url": result.final_url or candidate.url,
        "source_registry_id": "company_ir",
        "canonical_url": canonical,
        "source_reliability_tier": "company",
        "robots_policy": row.get("robots_policy", "Use official company pages; respect robots and rate limits."),
        "last_url_check_at": ingested_at,
        "fetch_status": "ok",
        "content_license_note": "Official issuer material; preserve source URL and avoid redistributing full text beyond research cache.",
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
        "source_credibility": 0.8,
        "event_type": source_type,
        "language": "en",
        "discovery_source_url": row.get("url", ""),
        "discovery_method": candidate.discovery_method,
        "discovery_anchor_text": candidate.anchor_text,
        "api_payload_url": candidate.api_payload_url,
        "published_at_source": date_source,
        "body_word_count": word_count,
    }
    try:
        document = FinancialDocument.from_dict(record)
    except (KeyError, ValueError) as exc:
        manifest["reject_reason"] = f"schema_error:{exc}"
        return None, manifest

    manifest["accepted"] = "yes"
    manifest["reject_reason"] = ""
    return document.to_dict() | {
        "discovery_source_url": record["discovery_source_url"],
        "discovery_method": record["discovery_method"],
        "discovery_anchor_text": record["discovery_anchor_text"],
        "api_payload_url": record["api_payload_url"],
        "published_at_source": record["published_at_source"],
        "body_word_count": word_count,
    }, manifest


def discover_candidates_for_source(
    row: dict[str, str],
    fetcher: Fetcher,
    config: DiscoveryConfig,
) -> tuple[list[CandidateLink], dict[str, Any]]:
    source_url = canonicalize_url(row.get("url", ""))
    source_fetch_needed = config.enable_generic_html or config.enable_rss or config.enable_wordpress or not config.enable_q4_feed
    source_result = (
        fetcher(source_url)
        if source_fetch_needed
        else FetchResult(url=source_url, final_url=source_url, status_code=200, content_type="", text="")
    )
    vendor_profile, vendor_recommendation = classify_vendor_profile(source_url, source_result.text, source_result.status_code)
    source_manifest: dict[str, Any] = {
        "ticker": row.get("ticker", "").upper(),
        "company": row.get("company", ""),
        "source_type": row.get("source_type", ""),
        "source_url": source_url,
        "source_http_status": source_result.status_code or "",
        "source_final_url": source_result.final_url,
        "source_error": source_result.error,
        "source_fetch_skipped": "no" if source_fetch_needed else "yes",
        "vendor_profile": vendor_profile,
        "vendor_recommendation": vendor_recommendation,
        "archive_pages_seen": 0,
        "candidate_links_seen": 0,
        "q4_feed_candidates_seen": 0,
        "rss_candidates_seen": 0,
        "wordpress_candidates_seen": 0,
    }

    if not source_result.status_code or source_result.status_code >= 500:
        return [], source_manifest

    direct_candidates: list[CandidateLink] = []
    q4_only_mode = config.enable_q4_feed and not (config.enable_generic_html or config.enable_rss or config.enable_wordpress)
    if config.enable_q4_feed and (q4_only_mode or should_try_q4_feed(source_url, vendor_profile, source_result.text)):
        q4_candidates = discover_q4_feed_candidates(source_url, fetcher, config)
        source_manifest["q4_feed_candidates_seen"] = len(q4_candidates)
        direct_candidates.extend(q4_candidates)
    if config.enable_rss:
        rss_candidates = discover_rss_candidates(source_result.final_url or source_url, source_result.text, fetcher, config)
        source_manifest["rss_candidates_seen"] = len(rss_candidates)
        direct_candidates.extend(rss_candidates)
    if config.enable_wordpress and (vendor_profile == "wordpress" or "/wp-json" in (source_result.text or "").lower()):
        wp_candidates = discover_wordpress_candidates(source_result.final_url or source_url, source_result.text, fetcher, config)
        source_manifest["wordpress_candidates_seen"] = len(wp_candidates)
        direct_candidates.extend(wp_candidates)

    archive_queue: deque[CandidateLink] = deque()
    if config.enable_generic_html:
        for year in year_range(config):
            for candidate in year_archive_candidates(source_url, year):
                archive_queue.append(
                    CandidateLink(
                        url=candidate,
                        source_url=source_url,
                        discovery_method="year_pattern",
                        candidate_year=year,
                    )
                )
        archive_queue.extend(extract_links(source_result.final_url or source_url, source_result.text, config))

    seen_archive_urls: set[str] = set()
    detail_candidates: list[CandidateLink] = []
    seen_detail_urls: set[str] = set()
    for candidate in direct_candidates:
        detail_url = canonicalize_url(candidate.url)
        if detail_url in seen_detail_urls:
            continue
        seen_detail_urls.add(detail_url)
        detail_candidates.append(candidate)
    archive_pages_seen = 0

    while (
        archive_queue
        and archive_pages_seen < config.max_archive_pages_per_source
        and len(detail_candidates) < config.max_detail_candidates_per_source
    ):
        archive = archive_queue.popleft()
        archive_url = canonicalize_url(archive.url)
        if archive_url in seen_archive_urls:
            continue
        seen_archive_urls.add(archive_url)
        archive_result = fetcher(archive_url)
        if config.sleep_seconds:
            time.sleep(config.sleep_seconds)
        if not archive_result.status_code or archive_result.status_code >= 400:
            continue
        archive_pages_seen += 1
        links = extract_links(archive_result.final_url or archive_url, archive_result.text, config)
        if is_probably_detail_url(archive_result.final_url or archive_url, archive.anchor_text):
            detail_url = canonicalize_url(archive_result.final_url or archive_url)
            if detail_url not in seen_detail_urls:
                seen_detail_urls.add(detail_url)
                detail_candidates.append(
                    CandidateLink(
                        url=detail_url,
                        source_url=source_url,
                        discovery_method=archive.discovery_method,
                        anchor_text=archive.anchor_text,
                        candidate_year=archive.candidate_year,
                    )
                )
        for link in links:
            detail_url = canonicalize_url(link.url)
            if detail_url in seen_detail_urls:
                continue
            if is_probably_detail_url(detail_url, link.anchor_text) or link.candidate_year:
                seen_detail_urls.add(detail_url)
                detail_candidates.append(link)
            if len(detail_candidates) >= config.max_detail_candidates_per_source:
                break
        if len(detail_candidates) >= config.max_detail_candidates_per_source:
            break

    source_manifest["archive_pages_seen"] = archive_pages_seen
    source_manifest["candidate_links_seen"] = len(detail_candidates)
    return detail_candidates, source_manifest


def discover_documents_from_sources(
    sources: Iterable[dict[str, str]],
    *,
    metadata_path: Union[str, Path],
    config: DiscoveryConfig,
    fetcher: Fetcher | None = None,
    source_limit: int = 0,
    source_offset: int = 0,
    tickers: set[str] | None = None,
    source_types: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    metadata = load_ticker_metadata(metadata_path)
    ingested_at = utc_now_iso()
    session = make_session()
    actual_fetcher = fetcher or (
        lambda url: fetch_url(url, session=session, timeout_seconds=config.timeout_seconds)
    )
    documents: list[dict[str, Any]] = []
    detail_manifest: list[dict[str, Any]] = []
    source_manifest: list[dict[str, Any]] = []
    vendor_queue: list[dict[str, Any]] = []
    seen_doc_ids: set[str] = set()

    selected_sources = []
    for source in sources:
        ticker = str(source.get("ticker", "")).upper()
        source_type = str(source.get("source_type", ""))
        if tickers and ticker not in tickers:
            continue
        if source_types and source_type not in source_types:
            continue
        selected_sources.append(source)
    if source_offset > 0:
        selected_sources = selected_sources[source_offset:]
    if source_limit > 0:
        selected_sources = selected_sources[:source_limit]

    for source_index, row in enumerate(selected_sources, start=1):
        grade = str(row.get("crawler_grade", "") or "")
        if config.include_source_grades and grade not in config.include_source_grades:
            continue
        candidates, source_info = discover_candidates_for_source(row, actual_fetcher, config)
        source_info["source_index"] = source_index
        source_manifest.append(source_info)

        accepted_for_source = 0
        rejected_for_source = 0
        for candidate in candidates:
            if accepted_for_source >= config.max_documents_per_source:
                break
            document, manifest = validate_detail_candidate(
                row,
                candidate,
                actual_fetcher,
                config,
                metadata,
                ingested_at,
            )
            detail_manifest.append(manifest)
            if document is None:
                rejected_for_source += 1
                continue
            if document["doc_id"] in seen_doc_ids:
                manifest["accepted"] = "no"
                manifest["reject_reason"] = "duplicate_doc_id"
                rejected_for_source += 1
                continue
            documents.append(document)
            seen_doc_ids.add(document["doc_id"])
            accepted_for_source += 1

        source_info["accepted_documents"] = accepted_for_source
        source_info["rejected_candidates_checked"] = rejected_for_source
        if accepted_for_source == 0 or source_info["vendor_profile"] not in {"static_html_or_custom", "wordpress"}:
            priority = vendor_priority(source_info["vendor_profile"], accepted_for_source)
            if source_info.get("source_fetch_skipped") == "yes" and int(source_info.get("q4_feed_candidates_seen", 0) or 0) == 0:
                priority = "not_q4_source_run_static_or_browser_later"
            vendor_queue.append(
                {
                    "ticker": row.get("ticker", "").upper(),
                    "company": row.get("company", ""),
                    "source_type": row.get("source_type", ""),
                    "url": row.get("url", ""),
                    "crawler_grade": grade,
                    "vendor_profile": source_info["vendor_profile"],
                    "vendor_recommendation": source_info["vendor_recommendation"],
                    "accepted_documents": accepted_for_source,
                    "candidate_links_seen": source_info["candidate_links_seen"],
                    "source_http_status": source_info["source_http_status"],
                    "priority": priority,
                }
            )
    return documents, detail_manifest, source_manifest, vendor_queue


def vendor_priority(vendor_profile: str, accepted_documents: int) -> str:
    if accepted_documents > 0:
        return "low_already_has_generic_yield"
    if vendor_profile in {"q4_or_q4_like", "gcs_web", "quotemedia"}:
        return "high_vendor_api_adapter"
    if vendor_profile in {"cloudflare_challenge", "captcha_module"}:
        return "medium_browser_or_alternate_endpoint"
    if vendor_profile == "wordpress":
        return "medium_wp_rest_or_sitemap"
    if vendor_profile == "fetch_problem":
        return "medium_recheck_or_replace"
    return "medium_source_specific_adapter"


def read_sources_csv(path: Union[str, Path]) -> list[dict[str, str]]:
    source_path = local_project_path(path)
    with source_path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [{key: (value or "").strip() for key, value in row.items()} for row in csv.DictReader(handle)]


def write_csv(path: Union[str, Path], rows: list[dict[str, Any]]) -> None:
    output_path = local_project_path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        output_path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_summary(
    documents: list[dict[str, Any]],
    detail_manifest: list[dict[str, Any]],
    source_manifest: list[dict[str, Any]],
    vendor_queue: list[dict[str, Any]],
    config: DiscoveryConfig,
) -> dict[str, Any]:
    return {
        "created_at": utc_now_iso(),
        "config": {
            "start_year": config.start_year,
            "end_year": config.end_year,
            "max_archive_pages_per_source": config.max_archive_pages_per_source,
            "max_detail_candidates_per_source": config.max_detail_candidates_per_source,
            "max_documents_per_source": config.max_documents_per_source,
            "min_body_words": config.min_body_words,
            "q4_page_size": config.q4_page_size,
            "rss_item_limit": config.rss_item_limit,
            "wp_page_size": config.wp_page_size,
            "wp_max_pages_per_year": config.wp_max_pages_per_year,
            "enable_q4_feed": config.enable_q4_feed,
            "enable_rss": config.enable_rss,
            "enable_wordpress": config.enable_wordpress,
            "enable_generic_html": config.enable_generic_html,
        },
        "source_rows_checked": len(source_manifest),
        "documents_written": len(documents),
        "detail_candidates_checked": len(detail_manifest),
        "accepted_by_ticker": dict(Counter(doc.get("matched_tickers", [""])[0] for doc in documents)),
        "accepted_by_source_type": dict(Counter(doc.get("source_type", "") for doc in documents)),
        "reject_reasons": dict(Counter(row.get("reject_reason", "") for row in detail_manifest if row.get("accepted") != "yes")),
        "vendor_queue_rows": len(vendor_queue),
        "vendor_profiles": dict(Counter(row.get("vendor_profile", "") for row in vendor_queue)),
        "vendor_priorities": dict(Counter(row.get("priority", "") for row in vendor_queue)),
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Discover dated official company archive documents.")
    parser.add_argument("--sources", required=True, help="CSV with ticker/company/source_type/url rows.")
    parser.add_argument("--metadata", default="data/processed_documents/dow30_ticker_metadata.csv")
    parser.add_argument("--output-documents", required=True, help="Output normalized FinancialDocument JSONL.")
    parser.add_argument("--detail-manifest-output", required=True, help="CSV of accepted/rejected detail candidates.")
    parser.add_argument("--source-manifest-output", required=True, help="CSV summary per source row.")
    parser.add_argument("--vendor-queue-output", required=True, help="CSV queue for vendor/browser/API adapters.")
    parser.add_argument("--summary-output", required=True, help="JSON summary.")
    parser.add_argument("--start-year", type=int, default=2010)
    parser.add_argument("--end-year", type=int, default=2023)
    parser.add_argument("--max-archive-pages-per-source", type=int, default=80)
    parser.add_argument("--max-detail-candidates-per-source", type=int, default=250)
    parser.add_argument("--max-documents-per-source", type=int, default=50)
    parser.add_argument("--min-body-words", type=int, default=180)
    parser.add_argument("--timeout-seconds", type=int, default=15)
    parser.add_argument("--sleep-seconds", type=float, default=0.05)
    parser.add_argument("--q4-page-size", type=int, default=100)
    parser.add_argument("--rss-item-limit", type=int, default=200)
    parser.add_argument("--wp-page-size", type=int, default=50)
    parser.add_argument("--wp-max-pages-per-year", type=int, default=2)
    parser.add_argument("--source-limit", type=int, default=0)
    parser.add_argument("--source-offset", type=int, default=0)
    parser.add_argument("--ticker", action="append", default=[], help="Ticker filter. Repeatable.")
    parser.add_argument("--source-type", action="append", default=[], help="source_type filter. Repeatable.")
    parser.add_argument("--disable-q4-feed", action="store_true")
    parser.add_argument("--disable-rss", action="store_true")
    parser.add_argument("--disable-wordpress", action="store_true")
    parser.add_argument("--disable-generic-html", action="store_true")
    parser.add_argument(
        "--include-source-grade",
        action="append",
        default=[],
        help="crawler_grade values to include. Repeatable. Defaults to all relevant grades.",
    )
    args = parser.parse_args(argv)

    config = DiscoveryConfig(
        start_year=args.start_year,
        end_year=args.end_year,
        max_archive_pages_per_source=args.max_archive_pages_per_source,
        max_detail_candidates_per_source=args.max_detail_candidates_per_source,
        max_documents_per_source=args.max_documents_per_source,
        min_body_words=args.min_body_words,
        timeout_seconds=args.timeout_seconds,
        sleep_seconds=args.sleep_seconds,
        q4_page_size=args.q4_page_size,
        rss_item_limit=args.rss_item_limit,
        wp_page_size=args.wp_page_size,
        wp_max_pages_per_year=args.wp_max_pages_per_year,
        enable_q4_feed=not args.disable_q4_feed,
        enable_rss=not args.disable_rss,
        enable_wordpress=not args.disable_wordpress,
        enable_generic_html=not args.disable_generic_html,
        include_source_grades=tuple(args.include_source_grade) if args.include_source_grade else DiscoveryConfig().include_source_grades,
    )

    sources = read_sources_csv(args.sources)
    documents, detail_manifest, source_manifest, vendor_queue = discover_documents_from_sources(
        sources,
        metadata_path=args.metadata,
        config=config,
        source_limit=args.source_limit,
        source_offset=args.source_offset,
        tickers={ticker.upper() for ticker in args.ticker} if args.ticker else None,
        source_types=set(args.source_type) if args.source_type else None,
    )
    write_jsonl(args.output_documents, documents)
    write_csv(args.detail_manifest_output, detail_manifest)
    write_csv(args.source_manifest_output, source_manifest)
    write_csv(args.vendor_queue_output, vendor_queue)
    summary = build_summary(documents, detail_manifest, source_manifest, vendor_queue, config)
    summary_path = local_project_path(args.summary_output)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
