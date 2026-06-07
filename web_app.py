"""Local browser-testable FinPortfolio IR dashboard.

This server intentionally uses only the Python standard library. It is a thin
UI/API layer over the retrieval, macro, portfolio, favorite, and My Vibe
primitives; it does not crawl live sites and does not call FinGPT.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import http.cookies
import ipaddress
import json
import math
import mimetypes
import os
import re
import socket
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawler.source_registry import canonicalize_url  # noqa: E402
from finportfolio_ir.chart_lab import ChartLabStore, chart_lab_options  # noqa: E402
from finportfolio_ir.favorites import (  # noqa: E402
    annotate_results_with_favorites,
    favorite_key,
    normalize_favorite_websites,
    sort_results_for_refresh,
    toggle_favorite_in_place,
)
from finportfolio_ir.dow30 import DOW30_COMPANIES, DOW30_SECTOR_BY_TICKER, DOW30_TICKER_SET, dow30_options  # noqa: E402
from finportfolio_ir.io_utils import local_project_path, read_jsonl  # noqa: E402
from finportfolio_ir.my_vibe import build_portfolio_impact_prompt, post_for_ui, post_portfolio_relevance  # noqa: E402
from finportfolio_ir.portfolio_summary import summarize_portfolio  # noqa: E402
from finportfolio_ir.query_intent import classify_query_intent  # noqa: E402
from finportfolio_ir.macro_rule_engine import evaluate_official_macro  # noqa: E402
from finportfolio_ir.text_utils import excerpt, tokenize  # noqa: E402
from finportfolio_ir.us_macro_rules import build_macro_portfolio_translation, build_us_macro_dashboard  # noqa: E402


DEFAULT_MACRO_SNAPSHOT = {
    "fed_funds_rate": 4.75,
    "ten_year_treasury_yield": 4.20,
    "ten_year_breakeven": 2.35,
    "investment_grade_credit_spread": 1.22,
    "payrolls_3m_avg": 155000,
    "unemployment_3m_change": 0.12,
    "retail_sales_yoy": 2.1,
    "ism_new_orders": 50.4,
    "sp500_earnings_revision_3m": 0.01,
    "dxy_yoy": 2.4,
    "vix": 18.5,
    "wti_yoy": 8.0,
}

DEFAULT_SETTINGS = {
    "portfolio": [
        {"ticker": "AAPL", "purchase_price": 178.0, "quantity": 10},
        {"ticker": "MSFT", "purchase_price": 420.0, "quantity": 4},
        {"ticker": "JPM", "purchase_price": 190.0, "quantity": 6},
        {"ticker": "CVX", "purchase_price": 155.0, "quantity": 3},
    ],
    "favorite_websites": ["https://www.sec.gov/", "https://fred.stlouisfed.org/", "https://www.apple.com/"],
}

SAMPLE_DOCUMENTS_PATH = ROOT / "data" / "processed_documents" / "documents.jsonl"
DEMO_DOCUMENTS_PATH = ROOT / "data" / "processed_documents" / "repo_demo_documents.jsonl"
FULL_DOCUMENTS_PATH = ROOT / "data" / "processed_documents" / "sec_macro_company_ir_ppo_2010_2023_documents.jsonl"
TEXT_FEATURES_PATH = (
    ROOT
    / "data"
    / "exports"
    / "daily_retrieval_ppo_full_company_ir"
    / "codex_rule_text_features_macro_rules"
    / "doc_text_features_codex_rule.csv"
)
FEATURE_RELATIONS_PATH = (
    ROOT
    / "data"
    / "exports"
    / "daily_retrieval_ppo_full_company_ir"
    / "ppo_ablation_package"
    / "pre_ppo_diagnostics"
    / "feature_target_relations_stock_level.csv"
)
SEARCH_INDEX_PATH = ROOT / "data" / "search_index" / "finportfolio_search.sqlite"
CHART_LAB_PANEL_PATH = (
    ROOT
    / "data"
    / "exports"
    / "daily_retrieval_ppo_full_company_ir"
    / "rl_panel_codex_rule_text_features.csv"
)
SEARCH_INDEX_VERSION = "search_index_v1"
SEARCH_INDEX_CANDIDATE_LIMIT = 1_500
DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 25
MY_VIBE_INDEX_CANDIDATE_LIMIT = 700
HISTORICAL_SEARCH_CUTOFF = "2023-03-01T23:59:59Z"
SEARCH_STOPWORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "best",
    "buy",
    "can",
    "for",
    "from",
    "how",
    "i",
    "in",
    "into",
    "invest",
    "investment",
    "is",
    "me",
    "of",
    "on",
    "or",
    "should",
    "stock",
    "stocks",
    "the",
    "to",
    "what",
    "which",
    "with",
    "corporation",
    "company",
    "inc",
    "group",
}
ENTITY_QUERY_STOPWORDS = SEARCH_STOPWORDS | {
    "10k",
    "10q",
    "8k",
    "archive",
    "archives",
    "event",
    "events",
    "filing",
    "filings",
    "ir",
    "macro",
    "press",
    "release",
    "releases",
    "report",
    "reports",
    "risk",
    "risks",
    "sec",
    "signal",
    "signals",
}
ENTITY_SUFFIX_WORDS = SEARCH_STOPWORDS | {
    "co",
    "corp",
    "incorporated",
    "ltd",
    "plc",
}
SIGNAL_FEATURE_COLUMNS = [
    "sentiment_proxy",
    "risk_intensity",
    "uncertainty_intensity",
    "opportunity_intensity",
    "forward_looking_intensity",
    "portfolio_action_relevance",
    "final_score",
    "event_severity_score",
    "risk_term_score",
    "macro_regime_relevance_score",
]
SIGNAL_FLAG_COLUMNS = [
    "signal_earnings_guidance",
    "signal_company_risk",
    "signal_macro_rates",
    "signal_inflation",
    "signal_credit",
    "signal_labor_growth",
    "signal_market_volatility",
    "signal_energy",
    "signal_housing",
    "signal_legal_regulatory",
    "signal_supply_chain",
    "signal_consumer_demand",
    "signal_margin_pressure",
    "signal_capital_return",
    "signal_mna",
]
FINANCIAL_CREDIT_TICKERS = {"AXP", "GS", "JPM", "TRV", "V"}


SEARCH_CUTOFF = os.environ.get("FINPORTFOLIO_SEARCH_CUTOFF", "").strip() or HISTORICAL_SEARCH_CUTOFF
SEARCH_END_LABEL = SEARCH_CUTOFF[:10]


def default_documents_path() -> Path:
    if FULL_DOCUMENTS_PATH.exists():
        return FULL_DOCUMENTS_PATH
    if DEMO_DOCUMENTS_PATH.exists():
        return DEMO_DOCUMENTS_PATH
    return SAMPLE_DOCUMENTS_PATH


def default_search_index_path() -> Path:
    return SEARCH_INDEX_PATH

ICON_FILES = {
    "portfolio": "icon_portfolio_settings.png",
    "favorite": "favorite_site_icon.png",
    "empty_heart": "empty_heart_favorite_sites.png",
    "delete": "red_crest_delete_button.jpg",
}

REACHABLE_URL_STATUSES = set(range(200, 400)) | {401, 403, 405}
ENV_PATH = ROOT / ".env"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
MISTRAL_CHAT_COMPLETIONS_URL = "https://api.mistral.ai/v1/chat/completions"
DEEPSEEK_CHAT_COMPLETIONS_URL = "https://api.deepseek.com/chat/completions"
PARATERA_CHAT_COMPLETIONS_URL = "https://llmapi.paratera.com/v1/chat/completions"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_PARATERA_DEEPSEEK_MODEL = "DeepSeek-V4-Flash"
DEFAULT_LLM_MODEL = DEFAULT_DEEPSEEK_MODEL
MAX_PROMPT_POST_CHARS = 18_000
MAX_PORTFOLIO_HOLDINGS = 40
LLM_TIMEOUT_SECONDS = 90
LLM_MAX_ATTEMPTS = 3
LLM_RETRY_BASE_SECONDS = 1.5
LLM_RETRY_MAX_SECONDS = 10.0
LLM_RETRYABLE_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}
FOLDER_ANALYSIS_LLM_ENABLED = os.environ.get("FINPORTFOLIO_FOLDER_ANALYSIS_LLM", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
FOLDER_ANALYSIS_LLM_DOC_LIMIT = max(1, min(8, int(os.environ.get("FINPORTFOLIO_FOLDER_ANALYSIS_LLM_DOC_LIMIT", "6") or 6)))
FOLDER_ANALYSIS_LLM_TEXT_CHARS = max(500, min(1800, int(os.environ.get("FINPORTFOLIO_FOLDER_ANALYSIS_LLM_TEXT_CHARS", "1200") or 1200)))
FOLDER_ANALYSIS_MAX_DOCS_1Y = max(20, min(160, int(os.environ.get("FINPORTFOLIO_FOLDER_ANALYSIS_MAX_DOCS_1Y", "80") or 80)))
FOLDER_ANALYSIS_MAX_DOCS_5Y = max(40, min(240, int(os.environ.get("FINPORTFOLIO_FOLDER_ANALYSIS_MAX_DOCS_5Y", "160") or 160)))
FOLDER_ANALYSIS_MAX_DOCS_ALL = max(60, min(320, int(os.environ.get("FINPORTFOLIO_FOLDER_ANALYSIS_MAX_DOCS_ALL", "240") or 240)))
FOLDER_ANALYSIS_WARMUP_ENABLED = os.environ.get("FINPORTFOLIO_FOLDER_ANALYSIS_WARMUP", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
try:
    FOLDER_ANALYSIS_WARMUP_DELAY_SECONDS = max(
        0.0,
        min(120.0, float(os.environ.get("FINPORTFOLIO_FOLDER_ANALYSIS_WARMUP_DELAY_SECONDS", "20") or 20.0)),
    )
except ValueError:
    FOLDER_ANALYSIS_WARMUP_DELAY_SECONDS = 20.0


def _load_local_env(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lstrip("\ufeff")
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_local_env()


def _entity_words(value: str, *, stopwords: set[str] | None = None) -> list[str]:
    words = [
        re.sub(r"[^a-z0-9]", "", part)
        for part in re.sub(r"[^A-Za-z0-9]+", " ", value.lower().replace("&", " and ")).split()
    ]
    return [word for word in words if word and word not in (stopwords or set())]


def _company_name_terms(company: dict[str, str]) -> list[str]:
    return [
        word
        for word in _entity_words(company.get("name", ""), stopwords=ENTITY_SUFFIX_WORDS)
        if len(word) >= 2
    ]


def _company_aliases(company: dict[str, str]) -> list[str]:
    ticker = str(company.get("ticker", "")).lower()
    terms = _company_name_terms(company)
    aliases = {ticker}
    if terms:
        aliases.add(" ".join(terms))
        aliases.add("".join(terms))
        aliases.add(terms[0])
        if len(terms) >= 2:
            aliases.add(" ".join(terms[:2]))
            aliases.add("".join(terms[:2]))
    return [alias for alias in aliases if alias]


class UpstreamServiceError(RuntimeError):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


def load_env_file(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lstrip("\ufeff")
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file()


def _json_dumps(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("content-length", "0") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(min(length, 1_000_000))
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON body: {exc}") from exc
    return parsed if isinstance(parsed, dict) else {}


def _site_label(value: str) -> str:
    key = favorite_key(value)
    if not key:
        return "Unknown Site"
    return key.split(":")[0]


def _source_type_label(value: Any) -> str:
    text = str(value or "").lower()
    if "official_macro" in text:
        return "Macro release"
    if "sec_filing_exhibit" in text:
        return "SEC exhibit"
    if "sec_filing" in text:
        return "SEC filing"
    if "earnings" in text:
        return "Earnings release"
    if "press" in text:
        return "Press release"
    if "financial_report" in text:
        return "Financial report"
    if text.startswith("company_"):
        return "Company IR"
    if "news" in text or "headline" in text:
        return "News"
    return "Document"


def _clean_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(parsed) or math.isinf(parsed):
        return default
    return parsed


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _impact_direction_value(value: Any) -> float:
    text = str(value or "").strip().lower()
    if text == "positive":
        return 1.0
    if text == "negative":
        return -1.0
    if text == "mixed":
        return 0.25
    return 0.0


def _safe_ticker(value: Any) -> str:
    return re.sub(r"[^A-Z0-9.-]", "", str(value or "").upper())[:12]


def _clean_text(value: Any, *, limit: int = 700) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def server_llm_secret() -> str:
    if os.environ.get("FINPORTFOLIO_DISABLE_SERVER_LLM") == "1":
        return ""
    return (
        os.environ.get("LLM_API_KEY")
        or os.environ.get("DEBATE_API_KEY")
        or os.environ.get("MISTRAL_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    ).strip()


def server_llm_endpoint() -> str:
    explicit = (
        os.environ.get("LLM_BASE_URL")
        or os.environ.get("DEBATE_BASE_URL")
        or os.environ.get("MISTRAL_BASE_URL")
        or os.environ.get("DEEPSEEK_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or ""
    ).strip()
    if explicit:
        return _chat_completions_endpoint(explicit)
    if os.environ.get("DEBATE_API_KEY"):
        return PARATERA_CHAT_COMPLETIONS_URL
    if os.environ.get("DEEPSEEK_API_KEY") and not os.environ.get("MISTRAL_API_KEY"):
        return DEEPSEEK_CHAT_COMPLETIONS_URL
    if os.environ.get("OPENAI_API_KEY") and not os.environ.get("MISTRAL_API_KEY"):
        return OPENAI_RESPONSES_URL
    if os.environ.get("MISTRAL_API_KEY"):
        return MISTRAL_CHAT_COMPLETIONS_URL
    return DEEPSEEK_CHAT_COMPLETIONS_URL


def server_llm_model() -> str:
    model = (
        os.environ.get("LLM_MODEL")
        or os.environ.get("DEBATE_STUDENT_MODEL")
        or os.environ.get("MISTRAL_MODEL")
        or os.environ.get("DEEPSEEK_MODEL")
        or os.environ.get("OPENAI_MODEL")
        or DEFAULT_LLM_MODEL
    ).strip()
    if model.lower() == "deepseek-v4-pro":
        return "DeepSeek-V4-Flash"
    return model


def _chat_completions_endpoint(value: str) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return MISTRAL_CHAT_COMPLETIONS_URL
    if text.endswith("/chat/completions") or text.endswith("/responses"):
        return text
    return f"{text}/chat/completions"


def llm_provider_options() -> list[dict[str, Any]]:
    return [
        {
            "id": "mistral",
            "label": "Mistral",
            "model": "mistral-small-latest",
            "base_url": MISTRAL_CHAT_COMPLETIONS_URL,
        },
        {
            "id": "deepseek",
            "label": "DeepSeek Official",
            "model": DEFAULT_DEEPSEEK_MODEL,
            "base_url": DEEPSEEK_CHAT_COMPLETIONS_URL,
            "task_models": {
                "graph": DEFAULT_DEEPSEEK_MODEL,
                "post": DEFAULT_DEEPSEEK_MODEL,
                "portfolio": DEFAULT_DEEPSEEK_MODEL,
            },
        },
        {
            "id": "paratera_deepseek",
            "label": "Paratera DeepSeek",
            "model": DEFAULT_PARATERA_DEEPSEEK_MODEL,
            "base_url": PARATERA_CHAT_COMPLETIONS_URL,
            "task_models": {
                "graph": DEFAULT_PARATERA_DEEPSEEK_MODEL,
                "post": DEFAULT_PARATERA_DEEPSEEK_MODEL,
                "portfolio": DEFAULT_PARATERA_DEEPSEEK_MODEL,
            },
        },
        {
            "id": "openai",
            "label": "OpenAI",
            "model": "gpt-5.2",
            "base_url": OPENAI_RESPONSES_URL,
        },
    ]


LLM_PROVIDER_BY_ID = {provider["id"]: provider for provider in llm_provider_options()}


def _llm_config_for_task(config: dict[str, Any], task: str) -> dict[str, Any]:
    scoped = dict(config)
    task_models = config.get("task_models") if isinstance(config.get("task_models"), dict) else {}
    provider = LLM_PROVIDER_BY_ID.get(_clean_text(config.get("provider"), limit=40))
    if not task_models and provider and isinstance(provider.get("task_models"), dict):
        task_models = provider["task_models"]
    model = task_models.get(task) or config.get(f"{task}_model")
    if model:
        scoped["model"] = model
    return scoped


def public_llm_config() -> dict[str, Any]:
    has_server_llm = bool(server_llm_secret())
    default_endpoint = server_llm_endpoint() if has_server_llm else DEEPSEEK_CHAT_COMPLETIONS_URL
    default_model = server_llm_model() if has_server_llm else DEFAULT_LLM_MODEL
    return {
        "llm_server_configured": has_server_llm,
        "llm_default_model": normalize_llm_model_for_endpoint(default_model, default_endpoint),
        "llm_default_base_url": default_endpoint,
        "llm_providers": llm_provider_options(),
    }


def llm_request_format(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    path = parsed.path.rstrip("/").lower()
    host = (parsed.hostname or "").lower()
    if path.endswith("/chat/completions") or host.endswith("mistral.ai"):
        return "chat_completions"
    return "openai_responses"


def _endpoint_host(value: str) -> str:
    try:
        return urlparse(value).hostname or ""
    except Exception:
        return ""


def normalize_llm_model_for_endpoint(model: str, endpoint: str) -> str:
    cleaned = _clean_text(model, limit=120) or DEFAULT_LLM_MODEL
    host = _endpoint_host(endpoint).lower()
    if host == "llmapi.paratera.com" and cleaned.lower() in {"deepseek-light", "deepseek-v4-pro"}:
        return "DeepSeek-V4-Flash"
    return cleaned


def resolve_llm_config(config: dict[str, Any]) -> tuple[str, str, str, bool]:
    client_key = str(config.get("api_key") or "").strip()
    server_key = server_llm_secret()
    use_server_secret = not client_key and bool(server_key)
    api_key = client_key or server_key
    provider = LLM_PROVIDER_BY_ID.get(_clean_text(config.get("provider"), limit=40))
    if use_server_secret:
        server_endpoint = server_llm_endpoint()
        default_endpoint = provider["base_url"] if provider else _clean_text(config.get("base_url"), limit=300)
        if default_endpoint and _endpoint_host(default_endpoint) == _endpoint_host(server_endpoint):
            model = _clean_text(config.get("model") or server_llm_model(), limit=120)
            endpoint = _clean_text(config.get("base_url") or server_endpoint, limit=300)
            return api_key, normalize_llm_model_for_endpoint(model, endpoint), endpoint, True
        return api_key, normalize_llm_model_for_endpoint(server_llm_model(), server_endpoint), server_endpoint, True
    default_model = provider["model"] if provider else server_llm_model() or DEFAULT_LLM_MODEL
    default_endpoint = provider["base_url"] if provider else server_llm_endpoint() or MISTRAL_CHAT_COMPLETIONS_URL
    model = _clean_text(config.get("model") or default_model, limit=120)
    endpoint = _clean_text(config.get("base_url") or default_endpoint, limit=300)
    return api_key, normalize_llm_model_for_endpoint(model, endpoint), endpoint, False


def is_safe_https_endpoint(endpoint: str) -> bool:
    parsed = urlparse(str(endpoint or "").strip())
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if parsed.scheme == "https":
        return True
    return parsed.scheme == "http" and host in {"localhost", "127.0.0.1", "::1"}


def _json_text_part(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
        return "\n".join(part for part in parts if part)
    return ""


def extract_chat_completion_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    return _json_text_part(message.get("content")).strip()


def extract_response_text(payload: dict[str, Any]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    parts: list[str] = []
    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        parts.append(str(block.get("text") or ""))
                    elif isinstance(block, str):
                        parts.append(block)
            elif isinstance(content, str):
                parts.append(content)
    return "\n".join(part for part in parts if part).strip()


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def _upstream_local_status(status_code: int) -> HTTPStatus:
    if status_code == HTTPStatus.TOO_MANY_REQUESTS:
        return HTTPStatus.TOO_MANY_REQUESTS
    if status_code in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
        return HTTPStatus.UNAUTHORIZED
    if 400 <= status_code < 500:
        return HTTPStatus.BAD_REQUEST
    if status_code == HTTPStatus.GATEWAY_TIMEOUT:
        return HTTPStatus.GATEWAY_TIMEOUT
    return HTTPStatus.BAD_GATEWAY


def _extract_upstream_error(raw: bytes) -> str:
    if not raw:
        return "empty error response"
    text = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return _clean_text(text)
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            parts = [
                _clean_text(error.get("message")),
                _clean_text(error.get("type")),
                _clean_text(error.get("code")),
            ]
            return "; ".join(part for part in parts if part) or "upstream error"
        if isinstance(error, str):
            return _clean_text(error)
    return _clean_text(payload)


def _is_valid_hostname(host: str) -> bool:
    if host in {"localhost"}:
        return True
    try:
        socket.inet_aton(host)
        return True
    except OSError:
        pass
    if "." not in host or len(host) > 253:
        return False
    labels = host.rstrip(".").split(".")
    return all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)


def _hostname_resolves_to_public_ip(host: str) -> bool:
    if host in {"localhost"}:
        return False
    try:
        ip = ipaddress.ip_address(host)
        return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved)
    except ValueError:
        pass
    try:
        addresses = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    checked = False
    for address in addresses:
        raw_ip = address[4][0]
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError:
            continue
        checked = True
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            return False
    return checked


def _favorite_url_for_settings(value: Any, *, strict: bool = False) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        canonical = canonicalize_url(text)
        parsed = urlparse(canonical)
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if scheme not in {"http", "https"} or not _is_valid_hostname(host):
            raise ValueError
        port = f":{parsed.port}" if parsed.port else ""
    except (TypeError, ValueError):
        if strict:
            raise ValueError("Favorite website must be a valid URL, for example https://example.com.") from None
        return None
    return f"{scheme}://{host}{port}/"


def validate_favorite_website(url: Any, *, timeout: int = 5) -> dict[str, Any]:
    try:
        storage_url = _favorite_url_for_settings(url, strict=True)
    except ValueError as exc:
        return {"valid": False, "reachable": False, "url": str(url or ""), "message": str(exc)}

    assert storage_url is not None
    parsed = urlparse(storage_url)
    host = parsed.hostname or ""
    if not _hostname_resolves_to_public_ip(host):
        return {
            "valid": False,
            "reachable": False,
            "url": str(url or ""),
            "storage_url": storage_url,
            "site_key": favorite_key(storage_url),
            "http_status": None,
            "message": "Only public websites can be validated.",
        }
    result: dict[str, Any] = {
        "valid": False,
        "reachable": False,
        "url": str(url or ""),
        "storage_url": storage_url,
        "site_key": favorite_key(storage_url),
        "http_status": None,
        "message": "Website did not respond.",
    }
    for method in ("HEAD", "GET"):
        request = urllib.request.Request(
            storage_url,
            method=method,
            headers={"User-Agent": "FinPortfolioIR/0.1 favorite-url-check contact=local"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = int(response.status)
                result.update(
                    {
                        "valid": status in REACHABLE_URL_STATUSES,
                        "reachable": status in REACHABLE_URL_STATUSES,
                        "http_status": status,
                        "message": "Website verified.",
                    }
                )
                return result
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            if status == 405 and method == "HEAD":
                continue
            result.update(
                {
                    "valid": status in REACHABLE_URL_STATUSES,
                    "reachable": status in REACHABLE_URL_STATUSES,
                    "http_status": status,
                    "message": "Website exists but blocks automated checks." if status in {401, 403, 405} else str(exc.reason),
                }
            )
            return result
        except (urllib.error.URLError, socket.timeout, ValueError) as exc:
            result["message"] = str(exc)
            if method == "HEAD":
                continue
            return result
    return result


def sanitize_settings(payload: dict[str, Any], *, strict: bool = False) -> dict[str, Any]:
    rows = payload.get("portfolio", [])
    portfolio: list[dict[str, Any]] = []
    if isinstance(rows, list):
        for row in rows[:60]:
            if not isinstance(row, dict):
                continue
            ticker = _safe_ticker(row.get("ticker"))
            price = _clean_number(row.get("purchase_price", row.get("price")))
            quantity = _clean_number(row.get("quantity"))
            if not ticker or ticker not in DOW30_TICKER_SET:
                if strict:
                    raise ValueError(f"{ticker or 'Ticker'} is not in the Dow 30 ticker list.")
                continue
            if price is None or quantity is None:
                if strict:
                    raise ValueError(f"{ticker} needs a positive purchase price and quantity.")
                continue
            portfolio.append({"ticker": ticker, "purchase_price": price, "quantity": quantity})
    favorites = payload.get("favorite_websites", [])
    if not isinstance(favorites, list):
        favorites = []
    normalized_favorites = []
    for item in favorites:
        normalized = _favorite_url_for_settings(item, strict=strict)
        if normalized:
            normalized_favorites.append(normalized)
    favorite_websites = [f"https://{key}/" for key in normalize_favorite_websites(normalized_favorites)]
    return {
        "portfolio": portfolio or list(DEFAULT_SETTINGS["portfolio"]),
        "favorite_websites": favorite_websites,
    }


@dataclass
class FinPortfolioWebService:
    root: Path = ROOT
    documents_path: Path = field(default_factory=default_documents_path)
    text_features_path: Path = TEXT_FEATURES_PATH
    feature_relations_path: Path = FEATURE_RELATIONS_PATH
    search_index_path: Path = field(default_factory=default_search_index_path)
    chart_panel_path: Path = CHART_LAB_PANEL_PATH
    settings_path: Path = ROOT / "data" / "user_settings" / "settings.json"
    public_demo: bool = False
    demo_settings_dir: Path = ROOT / "data" / "user_settings" / "demo_sessions"
    warm_chart_lab: bool = False
    macro_snapshot: dict[str, Any] | None = None
    _documents_cache: list[dict[str, Any]] = field(init=False, default_factory=list)
    _documents_cache_path: Path | None = field(init=False, default=None)
    _documents_cache_mtime_ns: int = field(init=False, default=-1)
    _corpus_summary_cache: dict[str, Any] = field(init=False, default_factory=dict)
    _documents_lock: Any = field(init=False, default_factory=threading.RLock)
    _vibe_rank_cache: dict[str, dict[str, Any]] = field(init=False, default_factory=dict)
    _text_features_cache: dict[str, dict[str, Any]] = field(init=False, default_factory=dict)
    _text_features_mtime_ns: int = field(init=False, default=-1)
    _text_features_summary_cache: dict[str, Any] = field(init=False, default_factory=dict)
    _feature_usefulness_cache: dict[str, float] = field(init=False, default_factory=dict)
    _feature_usefulness_mtime_ns: int = field(init=False, default=-1)
    _search_index_manifest_cache: dict[str, str] = field(init=False, default_factory=dict)
    _search_index_mtime_ns: int = field(init=False, default=-1)
    _chart_lab_store: ChartLabStore | None = field(init=False, default=None)
    _document_summary_cache: dict[str, dict[str, Any]] = field(init=False, default_factory=dict)
    _folder_analysis_cache: dict[str, dict[str, Any]] = field(init=False, default_factory=dict)
    _folder_analysis_lock: Any = field(init=False, default_factory=threading.RLock)
    _request_context: Any = field(init=False, default_factory=threading.local)

    def __post_init__(self) -> None:
        self.root = local_project_path(self.root)
        self.documents_path = local_project_path(self.documents_path)
        self.text_features_path = local_project_path(self.text_features_path)
        self.feature_relations_path = local_project_path(self.feature_relations_path)
        self.search_index_path = local_project_path(self.search_index_path)
        self.chart_panel_path = local_project_path(self.chart_panel_path)
        self.settings_path = local_project_path(self.settings_path)
        self.demo_settings_dir = local_project_path(self.demo_settings_dir)
        self.macro_snapshot = dict(self.macro_snapshot or DEFAULT_MACRO_SNAPSHOT)
        self._chart_lab_store = ChartLabStore(self.chart_panel_path)
        if self.warm_chart_lab:
            threading.Thread(target=self._chart_lab_store.warmup, name="chart-lab-warmup", daemon=True).start()
        if self.public_demo and FOLDER_ANALYSIS_WARMUP_ENABLED:
            threading.Thread(target=self._warm_demo_folder_analysis_cache, name="folder-analysis-warmup", daemon=True).start()

    def _active_settings_path(self) -> Path:
        if not self.public_demo:
            return self.settings_path
        session_id = str(getattr(self._request_context, "session_id", "") or "anonymous")
        safe_session = re.sub(r"[^a-zA-Z0-9_-]", "", session_id)[:80] or "anonymous"
        return self.demo_settings_dir / f"{safe_session}.json"

    def load_settings(self) -> dict[str, Any]:
        settings_path = self._active_settings_path()
        if settings_path.exists():
            try:
                loaded = json.loads(settings_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    sanitized = sanitize_settings(loaded)
                    if (
                        sanitized.get("favorite_websites") == ["https://example.com/"]
                        and self.documents_path.name == FULL_DOCUMENTS_PATH.name
                    ):
                        sanitized["favorite_websites"] = list(DEFAULT_SETTINGS["favorite_websites"])
                    return sanitized
            except (OSError, json.JSONDecodeError):
                pass
        return sanitize_settings(dict(DEFAULT_SETTINGS))

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        sanitized = sanitize_settings(payload, strict=True)
        settings_path = self._active_settings_path()
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(sanitized, ensure_ascii=False, indent=2), encoding="utf-8")
        self._vibe_rank_cache = {}
        return sanitized

    def validate_website_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return validate_favorite_website(payload.get("url", ""))

    def documents(self) -> list[dict[str, Any]]:
        with self._documents_lock:
            try:
                stat = self.documents_path.stat()
            except FileNotFoundError:
                return []
            if self._documents_cache_path != self.documents_path or self._documents_cache_mtime_ns != stat.st_mtime_ns:
                self._documents_cache = read_jsonl(self.documents_path)
                self._documents_cache_path = self.documents_path
                self._documents_cache_mtime_ns = stat.st_mtime_ns
                self._corpus_summary_cache = self._build_corpus_summary(self._documents_cache)
                self._vibe_rank_cache = {}
            return self._documents_cache

    def corpus_summary(self) -> dict[str, Any]:
        self.documents()
        return dict(self._corpus_summary_cache)

    def search_index_manifest(self) -> dict[str, str]:
        try:
            stat = self.search_index_path.stat()
        except FileNotFoundError:
            self._search_index_manifest_cache = {}
            self._search_index_mtime_ns = -1
            return {}
        if self._search_index_mtime_ns == stat.st_mtime_ns and self._search_index_manifest_cache:
            return dict(self._search_index_manifest_cache)
        connection = None
        try:
            connection = sqlite3.connect(self.search_index_path)
            rows = connection.execute("SELECT key, value FROM manifest").fetchall()
        except sqlite3.Error:
            self._search_index_manifest_cache = {}
            self._search_index_mtime_ns = stat.st_mtime_ns
            return {}
        finally:
            if connection is not None:
                connection.close()
        self._search_index_manifest_cache = {str(key): str(value) for key, value in rows}
        self._search_index_mtime_ns = stat.st_mtime_ns
        return dict(self._search_index_manifest_cache)

    def search_index_status(self) -> dict[str, Any]:
        manifest = self.search_index_manifest()
        if not manifest:
            return {"available": False, "usable": False, "path": str(self.search_index_path)}
        usable = self._search_index_is_usable(manifest)
        return {
            "available": True,
            "usable": usable,
            "path": str(self.search_index_path),
            "index_version": manifest.get("index_version", ""),
            "document_count": int(float(manifest.get("document_count", "0") or 0)),
            "feature_doc_count": int(float(manifest.get("feature_doc_count", "0") or 0)),
            "documents_path": manifest.get("documents_path", ""),
        }

    def _search_index_is_usable(self, manifest: dict[str, str] | None = None) -> bool:
        manifest = manifest or self.search_index_manifest()
        if manifest.get("index_version") != SEARCH_INDEX_VERSION:
            return False
        try:
            indexed_path = Path(manifest.get("documents_path", "")).resolve()
            current_path = self.documents_path.resolve()
            current_stat = self.documents_path.stat()
        except (OSError, RuntimeError, ValueError):
            return False
        if indexed_path != current_path:
            return False
        return str(current_stat.st_mtime_ns) == str(manifest.get("documents_mtime_ns", ""))

    def _open_search_index(self) -> sqlite3.Connection | None:
        if not self._search_index_is_usable():
            return None
        try:
            connection = sqlite3.connect(self.search_index_path)
        except sqlite3.Error:
            return None
        connection.row_factory = sqlite3.Row
        return connection

    def feature_usefulness(self) -> dict[str, float]:
        try:
            stat = self.feature_relations_path.stat()
        except FileNotFoundError:
            return {}
        if self._feature_usefulness_mtime_ns == stat.st_mtime_ns and self._feature_usefulness_cache:
            return self._feature_usefulness_cache
        weights: dict[str, float] = {}
        with self.feature_relations_path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                name = str(row.get("feature", "") or "")
                if not name:
                    continue
                score = max(
                    _float_value(row.get("train_only_screen_score")),
                    _float_value(row.get("best_abs_train_spearman")),
                    _float_value(row.get("best_abs_test_spearman")),
                )
                aliases = {name}
                for prefix in ("stock_text_avg_", "portfolio_text_avg_"):
                    if name.startswith(prefix):
                        aliases.add(name.removeprefix(prefix))
                for prefix in ("stock_signal_", "portfolio_signal_"):
                    if name.startswith(prefix):
                        clean = name.removeprefix(prefix)
                        clean = clean.removesuffix("_count").removesuffix("_flag")
                        aliases.add(f"signal_{clean}")
                for alias in aliases:
                    weights[alias] = max(weights.get(alias, 0.0), score)
        self._feature_usefulness_cache = weights
        self._feature_usefulness_mtime_ns = stat.st_mtime_ns
        return weights

    def text_features(self) -> dict[str, dict[str, Any]]:
        try:
            stat = self.text_features_path.stat()
        except FileNotFoundError:
            return {}
        if self._text_features_mtime_ns == stat.st_mtime_ns and self._text_features_cache:
            return self._text_features_cache
        usefulness = self.feature_usefulness()
        accum: dict[str, dict[str, Any]] = {}
        with self.text_features_path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                doc_id = str(row.get("doc_id", "") or "")
                if not doc_id:
                    continue
                bucket = accum.setdefault(
                    doc_id,
                    {
                        "_n": 0,
                        "_numeric_sums": {column: 0.0 for column in SIGNAL_FEATURE_COLUMNS},
                        "_flag_max": {column: 0.0 for column in SIGNAL_FLAG_COLUMNS},
                        "_impact_sum": 0.0,
                        "retrieval_layers": set(),
                        "query_intents": set(),
                        "active_signals": set(),
                    },
                )
                bucket["_n"] += 1
                for column in SIGNAL_FEATURE_COLUMNS:
                    bucket["_numeric_sums"][column] += _float_value(row.get(column))
                for column in SIGNAL_FLAG_COLUMNS:
                    value = _float_value(row.get(column))
                    bucket["_flag_max"][column] = max(bucket["_flag_max"][column], value)
                    if value > 0:
                        bucket["active_signals"].add(column)
                bucket["_impact_sum"] += _impact_direction_value(row.get("impact_direction"))
                if row.get("retrieval_layer"):
                    bucket["retrieval_layers"].add(str(row.get("retrieval_layer")))
                if row.get("query_intent_primary"):
                    bucket["query_intents"].add(str(row.get("query_intent_primary")))

        features: dict[str, dict[str, Any]] = {}
        signal_strength_values: list[float] = []
        for doc_id, bucket in accum.items():
            n = max(1, int(bucket["_n"]))
            numeric = {column: bucket["_numeric_sums"][column] / n for column in SIGNAL_FEATURE_COLUMNS}
            flags = {column: int(bucket["_flag_max"][column] > 0) for column in SIGNAL_FLAG_COLUMNS}
            active_signals = sorted(bucket["active_signals"])
            signal_usefulness = max([usefulness.get(signal, 0.0) for signal in active_signals] + [0.0])
            numeric_usefulness = max([usefulness.get(column, 0.0) for column in SIGNAL_FEATURE_COLUMNS] + [0.0])
            impact_score = bucket["_impact_sum"] / n
            active_signal_density = min(1.0, len(active_signals) / 6.0)
            signal_strength = (
                0.85 * numeric["portfolio_action_relevance"]
                + 0.65 * numeric["event_severity_score"]
                + 0.45 * numeric["risk_intensity"]
                + 0.35 * numeric["uncertainty_intensity"]
                + 0.40 * numeric["opportunity_intensity"]
                + 0.30 * numeric["forward_looking_intensity"]
                + 0.30 * abs(numeric["sentiment_proxy"])
                + 0.25 * numeric["final_score"]
                + 0.25 * active_signal_density
            )
            usefulness_multiplier = 1.0 + min(0.35, 4.0 * max(signal_usefulness, numeric_usefulness))
            calibrated_signal_score = signal_strength * usefulness_multiplier
            upside_score = (
                max(0.0, numeric["sentiment_proxy"])
                + numeric["opportunity_intensity"]
                + 0.5 * numeric["forward_looking_intensity"]
                + 0.6 * flags.get("signal_earnings_guidance", 0)
                + 0.4 * flags.get("signal_capital_return", 0)
                + max(0.0, impact_score)
            )
            risk_alert_score = (
                numeric["risk_intensity"]
                + numeric["uncertainty_intensity"]
                + max(0.0, -numeric["sentiment_proxy"])
                + 0.5 * flags.get("signal_company_risk", 0)
                + 0.5 * flags.get("signal_legal_regulatory", 0)
                + 0.4 * flags.get("signal_margin_pressure", 0)
                + 0.3 * flags.get("signal_supply_chain", 0)
                + max(0.0, -impact_score)
            )
            record = {
                **{column: round(value, 6) for column, value in numeric.items()},
                **flags,
                "impact_direction_score": round(impact_score, 6),
                "active_signals": active_signals,
                "retrieval_layers": sorted(bucket["retrieval_layers"]),
                "query_intents": sorted(bucket["query_intents"]),
                "signal_strength": round(signal_strength, 6),
                "calibrated_signal_score": round(calibrated_signal_score, 6),
                "historical_usefulness_score": round(max(signal_usefulness, numeric_usefulness), 6),
                "upside_signal_score": round(upside_score, 6),
                "risk_alert_score": round(risk_alert_score, 6),
                "feature_rows": n,
            }
            features[doc_id] = record
            signal_strength_values.append(calibrated_signal_score)
        self._text_features_cache = features
        self._text_features_mtime_ns = stat.st_mtime_ns
        self._text_features_summary_cache = {
            "path": str(self.text_features_path),
            "doc_count": len(features),
            "avg_calibrated_signal_score": round(sum(signal_strength_values) / max(1, len(signal_strength_values)), 6),
            "max_calibrated_signal_score": round(max(signal_strength_values or [0.0]), 6),
        }
        self._vibe_rank_cache = {}
        return self._text_features_cache

    def text_feature_summary(self) -> dict[str, Any]:
        self.text_features()
        return dict(self._text_features_summary_cache)

    def portfolio_signal_summary(self, portfolio_rows: list[dict[str, Any]]) -> dict[str, Any]:
        portfolio = summarize_portfolio(portfolio_rows)
        holdings = {str(row.get("ticker", "")).upper() for row in portfolio.get("holdings", []) if isinstance(row, dict)}
        features = self.text_features()
        scored: list[dict[str, Any]] = []
        risk_docs: list[dict[str, Any]] = []
        opportunity_docs: list[dict[str, Any]] = []
        ticker_scores: dict[str, dict[str, float]] = {}
        for record in self.documents():
            doc_features = features.get(str(record.get("doc_id", "")))
            if not doc_features:
                continue
            matched = {str(ticker).upper() for ticker in record.get("matched_tickers", []) or []}
            matched_holdings = sorted(matched & holdings)
            if not matched_holdings and "MARKET" not in matched:
                continue
            signal_score = float(doc_features.get("calibrated_signal_score", 0.0) or 0.0)
            risk_score = float(doc_features.get("risk_alert_score", 0.0) or 0.0)
            upside_score = float(doc_features.get("upside_signal_score", 0.0) or 0.0)
            item = self._result_row(record, signal_score, doc_features)
            item["matched_holdings"] = matched_holdings
            item["portfolio_signal_score"] = round(signal_score, 6)
            scored.append(item)
            if risk_score > 0:
                risk_docs.append({**item, "portfolio_signal_score": round(risk_score, 6)})
            if upside_score > 0:
                opportunity_docs.append({**item, "portfolio_signal_score": round(upside_score, 6)})
            for ticker in matched_holdings:
                bucket = ticker_scores.setdefault(ticker, {"signal": 0.0, "risk": 0.0, "upside": 0.0, "docs": 0.0})
                bucket["signal"] += signal_score
                bucket["risk"] += risk_score
                bucket["upside"] += upside_score
                bucket["docs"] += 1.0
        for values in ticker_scores.values():
            docs = max(1.0, values["docs"])
            values["signal"] = round(values["signal"] / docs, 6)
            values["risk"] = round(values["risk"] / docs, 6)
            values["upside"] = round(values["upside"] / docs, 6)
        scored.sort(key=lambda row: (float(row["portfolio_signal_score"]), str(row.get("available_at", ""))), reverse=True)
        risk_docs.sort(key=lambda row: (float(row["portfolio_signal_score"]), str(row.get("available_at", ""))), reverse=True)
        opportunity_docs.sort(key=lambda row: (float(row["portfolio_signal_score"]), str(row.get("available_at", ""))), reverse=True)
        strongest_tickers = sorted(
            [{"ticker": ticker, **values} for ticker, values in ticker_scores.items()],
            key=lambda row: (float(row["signal"]), float(row["upside"]), -float(row["risk"])),
            reverse=True,
        )
        return {
            "feature_doc_count": len(features),
            "portfolio_relevant_doc_count": len(scored),
            "strongest_documents": scored[:5],
            "risk_documents": risk_docs[:5],
            "opportunity_documents": opportunity_docs[:5],
            "ticker_signal_summary": strongest_tickers[:10],
        }

    def _build_corpus_summary(self, documents: list[dict[str, Any]]) -> dict[str, Any]:
        source_types = Counter(str(record.get("source_type", "") or "unknown") for record in documents)
        domains = Counter()
        official_docs = 0
        company_ir_docs = 0
        historical_docs = 0
        current_macro_docs = 0
        for record in documents:
            url = str(record.get("canonical_url") or record.get("url") or "")
            key = favorite_key(url)
            if key:
                domains[key] += 1
            source_type = str(record.get("source_type", "")).lower()
            source_tier = str(record.get("source_reliability_tier", "")).lower()
            if self._is_historical_search_row(record):
                historical_docs += 1
            elif source_type.startswith("official_macro"):
                current_macro_docs += 1
            if source_tier == "official" or source_type.startswith(("official_macro", "sec_filing")):
                official_docs += 1
            if source_type.startswith("company_"):
                company_ir_docs += 1
        return {
            "documents_path": str(self.documents_path),
            "document_count": len(documents),
            "source_type_counts": dict(source_types.most_common(12)),
            "top_domains": dict(domains.most_common(12)),
            "domain_counts": dict(domains),
            "official_document_count": official_docs,
            "company_ir_document_count": company_ir_docs,
            "historical_document_count": historical_docs,
            "current_macro_document_count": current_macro_docs,
            "historical_search_cutoff": SEARCH_END_LABEL,
            "sample_mode": self.documents_path.name == SAMPLE_DOCUMENTS_PATH.name,
        }

    def dashboard_payload(self) -> dict[str, Any]:
        settings = self.load_settings()
        portfolio_summary = summarize_portfolio(settings["portfolio"])
        portfolio_signals = self.portfolio_signal_summary(settings["portfolio"])
        macro = build_us_macro_dashboard(self.macro_snapshot or {})
        translation = build_macro_portfolio_translation(
            self.macro_snapshot or {},
            portfolio_summary.get("sector_weights", {}),
        )
        return {
            "language": "en",
            "macro_snapshot": self.macro_snapshot,
            "macro_dashboard": macro,
            "macro_portfolio_translation": translation,
            "portfolio_summary": portfolio_summary,
            "portfolio_signal_summary": portfolio_signals,
            "settings": settings,
            "icons": {name: f"/icons/{filename}" for name, filename in ICON_FILES.items()},
            "allowed_tickers": dow30_options(),
            "chart_lab": self.chart_lab_options_payload(settings),
            "llm": public_llm_config(),
            "corpus": {
                **self.corpus_summary(),
                "text_features": self.text_feature_summary(),
                "search_index": self.search_index_status(),
            },
        }

    def chart_lab_options_payload(self, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        settings = settings or self.load_settings()
        portfolio_tickers = [
            str(row.get("ticker", "")).upper()
            for row in settings.get("portfolio", [])
            if isinstance(row, dict) and str(row.get("ticker", "")).upper() in DOW30_TICKER_SET
        ]
        options = chart_lab_options()
        options["charts"] = [chart for chart in options.get("charts", []) if chart.get("scope") != "macro"]
        options["portfolio_tickers"] = portfolio_tickers
        options["default_ticker"] = portfolio_tickers[0] if portfolio_tickers else "AAPL"
        options["default_chart_id"] = "company_revenue_eps"
        options["default_mode"] = "structured"
        options["default_window"] = "all"
        options["panel_available"] = self.chart_panel_path.exists()
        return options

    def chart_lab_payload(self, query: dict[str, list[str]]) -> dict[str, Any]:
        ticker = _safe_ticker((query.get("ticker") or [""])[0]) or "AAPL"
        chart_id = str((query.get("chart_id") or ["company_revenue_eps"])[0] or "company_revenue_eps")
        mode = str((query.get("mode") or ["structured"])[0] or "structured")
        window = str((query.get("window") or ["all"])[0] or "all")
        store = self._chart_lab_store or ChartLabStore(self.chart_panel_path)
        return store.payload(ticker=ticker, chart_id=chart_id, mode=mode, window=window)

    def analyze_chart_lab_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        ticker = _safe_ticker(payload.get("ticker")) or "AAPL"
        chart_id = _clean_text(payload.get("chart_id") or "company_revenue_eps", limit=80)
        mode = _clean_text(payload.get("mode") or "structured", limit=40)
        window = _clean_text(payload.get("window") or "all", limit=20)
        chart = self.chart_lab_payload({"ticker": [ticker], "chart_id": [chart_id], "mode": [mode], "window": [window]})
        result = self._rule_based_chart_analysis(chart)
        llm_config = payload.get("llm") if isinstance(payload.get("llm"), dict) else {}
        api_key, model, endpoint, used_server_llm = resolve_llm_config(_llm_config_for_task(llm_config, "graph"))
        result["api_key_received"] = bool(str(llm_config.get("api_key", "")).strip())
        result["server_llm"] = used_server_llm
        result["api_key_persisted"] = False
        if not api_key:
            return result
        try:
            llm_result = self._call_llm_for_chart_analysis(chart, result, model, endpoint, api_key)
        except UpstreamServiceError as exc:
            result["llm_error"] = str(exc)
            return result
        if llm_result:
            result.update(llm_result)
            result["analysis_mode"] = "llm"
            result["model"] = model
        return result

    def _chart_metric_orientation(self, key: str, label: str) -> str:
        text = f"{key} {label}".lower()
        if "current ratio" in text or "cur_ratio" in text:
            return "low_bad"
        if any(term in text for term in ("revenue", "eps", "margin", "sentiment", "revision", "upside", "growth", "liquidity")):
            return "low_bad"
        if any(
            term in text
            for term in (
                "risk",
                "warning",
                "stress",
                "debt",
                "credit",
                "legal",
                "regulatory",
                "pressure",
                "spread",
                "yield level",
                "vix",
                "vol",
                "inversion",
                "cost",
            )
        ):
            return "high_bad"
        return "neutral"

    def _chart_value_label(self, value: float, unit: str) -> str:
        if unit == "ratio":
            return f"{value * 100:.1f}%"
        if unit == "flag":
            return "on" if value > 0 else "off"
        if unit in {"docs", "terms"}:
            return f"{value:.0f} {unit}"
        if unit == "USD":
            return self._compact_chart_number(value)
        if unit == "USD/share":
            return f"${value:.2f}"
        suffix = f" {unit}" if unit and unit not in {"score", "index"} else ""
        return f"{value:.2f}{suffix}"

    def _compact_chart_number(self, value: float) -> str:
        abs_value = abs(value)
        if abs_value >= 1_000_000_000:
            return f"{value / 1_000_000_000:.1f}B"
        if abs_value >= 1_000_000:
            return f"{value / 1_000_000:.1f}M"
        if abs_value >= 1_000:
            return f"{value / 1_000:.1f}K"
        if 0 < abs_value < 0.01:
            return f"{value:.4f}"
        return f"{value:.1f}" if abs_value >= 10 else f"{value:.2f}"

    def _chart_change_label(self, first: float, last: float, unit: str) -> str:
        delta = last - first
        if abs(first) > 1e-9 and unit not in {"flag", "docs", "terms"}:
            return f"{delta / abs(first) * 100:+.1f}%"
        return f"{delta:+.2f}"

    def _linear_trend_y(self, points: list[dict[str, Any]]) -> tuple[float, float, float]:
        values = [_float_value(point.get("y"), 0.5) for point in points]
        n = len(values)
        if n < 2:
            y = values[0] if values else 0.5
            return y, y, 0.0
        mean_x = (n - 1) / 2
        mean_y = sum(values) / n
        denom = sum((index - mean_x) ** 2 for index in range(n)) or 1.0
        slope = sum((index - mean_x) * (value - mean_y) for index, value in enumerate(values)) / denom
        start_y = _clamp(mean_y - slope * mean_x)
        end_y = _clamp(mean_y + slope * ((n - 1) - mean_x))
        return start_y, end_y, slope

    def _tone_for_change(self, orientation: str, change_y: float) -> str:
        if abs(change_y) < 0.06:
            return "neutral"
        if orientation == "high_bad":
            return "positive" if change_y < 0 else "warning"
        if orientation == "low_bad":
            return "positive" if change_y > 0 else "warning"
        return "positive" if change_y > 0 else "neutral"

    def _chart_analysis_headline(self, chart: dict[str, Any], verdict: str, lead_trend: dict[str, Any]) -> str:
        label = _clean_text(lead_trend.get("label") or chart.get("title") or "Chart", limit=44)
        change = _clean_text(lead_trend.get("change_label") or "", limit=24)
        title = _clean_text(chart.get("title") or "", limit=80).lower()
        if verdict == "Caution":
            if "debt" in title or "liquidity" in title:
                return f"{label} puts the balance sheet on watch"
            if "margin" in title or "cost" in title:
                return f"{label} is squeezing the profit story"
            if change:
                return f"{label} moved the wrong way ({change})"
            return f"{label} deserves a harder look"
        if verdict == "Constructive":
            if "revenue" in title or "eps" in title:
                return f"{label} is carrying the fundamental story"
            if change:
                return f"{label} improved {change}"
            return f"{label} gives the chart a cleaner setup"
        if change:
            return f"{label} moved {change}, but the picture is mixed"
        return f"{label} is mixed, not decisive"

    def _chart_analysis_takeaways(self, trends: list[dict[str, Any]]) -> list[dict[str, str]]:
        ordered = sorted(
            trends,
            key=lambda trend: (
                0 if trend.get("tone") == "warning" else 1 if trend.get("tone") == "positive" else 2,
                -abs(float(trend.get("end_y", 0.5)) - float(trend.get("start_y", 0.5))),
            ),
        )
        takeaways: list[dict[str, str]] = []
        for trend in ordered[:3]:
            label = _clean_text(trend.get("label") or "Metric", limit=42)
            tone = _clean_text(trend.get("tone") or "neutral", limit=16)
            if tone not in {"positive", "warning", "neutral"}:
                tone = "neutral"
            latest = _clean_text(trend.get("latest_value_label") or "", limit=40)
            first = _clean_text(trend.get("first_value_label") or "", limit=40)
            change = _clean_text(trend.get("change_label") or "", limit=24)
            text = f"{label} moved {change} from {first} to {latest}."
            takeaways.append({"tone": tone, "label": label, "text": text})
        return takeaways

    def _chart_follow_up(self, chart: dict[str, Any], trends: list[dict[str, Any]]) -> dict[str, str]:
        chart_id = _clean_text(chart.get("chart_id"), limit=80)
        follow_ups = {
            "company_revenue_eps": (
                "company_margins",
                "Check margins to see whether revenue is really turning into shareholder profit.",
            ),
            "company_margins": (
                "company_balance_stress",
                "Check balance-sheet stress to see whether margins are being supported by leverage or liquidity pressure.",
            ),
            "company_balance_stress": (
                "company_revenue_eps",
                "Check revenue and EPS to see whether leverage is funding real growth or just adding risk.",
            ),
            "company_filing_risk": (
                "company_balance_stress",
                "Check debt and liquidity to see whether filing risk is backed by balance-sheet pressure.",
            ),
            "company_guidance_events": (
                "company_revenue_eps",
                "Check revenue and EPS to see whether guidance language is visible in fundamentals.",
            ),
            "macro_rates_pressure": (
                "macro_curve_shape",
                "Check the yield curve to separate ordinary rate pressure from late-cycle recession risk.",
            ),
            "macro_credit_stress": (
                "macro_financial_conditions",
                "Check broad financial conditions to confirm whether credit stress is isolated or systemic.",
            ),
            "macro_volatility": (
                "macro_credit_stress",
                "Check credit spreads to see whether volatility is turning into funding stress.",
            ),
            "macro_curve_shape": (
                "macro_credit_stress",
                "Check credit stress to see whether curve inversion is already leaking into financing conditions.",
            ),
            "macro_financial_conditions": (
                "macro_volatility",
                "Check volatility to see whether tighter conditions are already hitting market risk appetite.",
            ),
        }
        suggested_id, reason = follow_ups.get(
            chart_id,
            (
                "company_margins" if str(chart.get("scope", "")).lower() != "macro" else "macro_credit_stress",
                "Check a second chart before treating this as a standalone signal.",
            ),
        )
        return {
            "chart_id": suggested_id,
            "label": next(
                (option["title"] for option in chart_lab_options().get("charts", []) if option.get("id") == suggested_id),
                suggested_id.replace("_", " ").title(),
            ),
            "reason": reason,
        }

    def _chart_analysis_commentary(
        self,
        chart: dict[str, Any],
        trends: list[dict[str, Any]],
        verdict: str,
        lead_trend: dict[str, Any],
    ) -> str:
        if not lead_trend:
            return "The chart does not yet have enough usable movement for an analyst-style read."
        lead = (
            f"{lead_trend['label']} is the main driver: it moved {lead_trend['change_label']} "
            f"from {lead_trend['first_value_label']} to {lead_trend['latest_value_label']}."
        )
        warnings = [trend for trend in trends if trend.get("tone") == "warning"]
        positives = [trend for trend in trends if trend.get("tone") == "positive"]
        if warnings and positives:
            second = (
                f"The useful part is {positives[0]['label']} at {positives[0]['latest_value_label']}, "
                f"but {warnings[0]['label']} at {warnings[0]['latest_value_label']} keeps the verdict from being clean."
            )
        elif warnings:
            second = f"The red flag is {warnings[0]['label']} at {warnings[0]['latest_value_label']}; check whether this is a one-off or a persistent deterioration."
        elif positives:
            second = f"The constructive signal is {positives[0]['label']} at {positives[0]['latest_value_label']}; the next check is whether it survives the next filing."
        else:
            second = "The lines are not sending a strong directional signal, so this is better treated as a monitoring chart."
        label = "supportive" if verdict == "Constructive" else "defensive" if verdict == "Caution" else "mixed"
        return f"{lead} {second} Overall, this is a {label} chart rather than a standalone decision."

    def _rule_based_chart_analysis(self, chart: dict[str, Any]) -> dict[str, Any]:
        series = [item for item in chart.get("series", [])[:3] if isinstance(item, dict)]
        trends: list[dict[str, Any]] = []
        highlights: list[dict[str, Any]] = []
        tone_score = 0
        for item in series:
            points = [
                point
                for point in item.get("points", [])
                if isinstance(point, dict) and point.get("date") and _float_value(point.get("value"), math.nan) == _float_value(point.get("value"), math.nan)
            ]
            if len(points) < 2:
                continue
            label = _clean_text(item.get("base_label") or item.get("label") or "Series", limit=60)
            key = _clean_text(item.get("key") or label, limit=80)
            unit = _clean_text(item.get("unit"), limit=30)
            orientation = self._chart_metric_orientation(key, label)
            first = _float_value(points[0].get("value"))
            latest = _float_value(points[-1].get("value"))
            start_y, end_y, slope = self._linear_trend_y(points)
            change_y = end_y - start_y
            tone = self._tone_for_change(orientation, change_y)
            tone_score += 1 if tone == "positive" else -1 if tone == "warning" else 0
            change_label = self._chart_change_label(first, latest, unit)
            trends.append(
                {
                    "series_key": key,
                    "label": label,
                    "tone": tone,
                    "start_y": round(start_y, 4),
                    "end_y": round(end_y, 4),
                    "slope": round(slope, 6),
                    "change_label": change_label,
                    "first_value_label": self._chart_value_label(first, unit),
                    "latest_value_label": self._chart_value_label(latest, unit),
                }
            )
            values_y = [_float_value(point.get("y"), 0.5) for point in points]
            if orientation == "high_bad":
                focus_index = max(range(len(points)), key=lambda index: values_y[index])
                focus_tone = "warning" if values_y[focus_index] >= 0.72 else "neutral"
                reason = f"Highest {label.lower()} reading: {self._chart_value_label(_float_value(points[focus_index].get('value')), unit)}."
            elif orientation == "low_bad":
                focus_index = min(range(len(points)), key=lambda index: values_y[index])
                focus_tone = "warning" if values_y[focus_index] <= 0.28 else "neutral"
                reason = f"Weakest {label.lower()} reading: {self._chart_value_label(_float_value(points[focus_index].get('value')), unit)}."
            else:
                focus_index = len(points) - 1
                focus_tone = "neutral"
                reason = f"Latest {label.lower()} reading: {self._chart_value_label(latest, unit)}."
            if focus_tone != "neutral" or not highlights:
                point = points[focus_index]
                highlights.append(
                    {
                        "series_key": key,
                        "label": label,
                        "date": str(point.get("date", ""))[:10],
                        "value_label": self._chart_value_label(_float_value(point.get("value")), unit),
                        "tone": focus_tone,
                        "reason": reason,
                    }
                )
            if abs(change_y) >= 0.18:
                highlights.append(
                    {
                        "series_key": key,
                        "label": label,
                        "date": str(points[-1].get("date", ""))[:10],
                        "value_label": self._chart_value_label(latest, unit),
                        "tone": tone,
                        "reason": f"Trend moved {change_label} from {self._chart_value_label(first, unit)} to {self._chart_value_label(latest, unit)}.",
                    }
                )
        unique_highlights: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for point in highlights:
            key = (str(point.get("series_key", "")), str(point.get("date", "")), str(point.get("reason", "")))
            if key not in seen:
                seen.add(key)
                unique_highlights.append(point)
        lead_trend = max(trends, key=lambda trend: abs(float(trend.get("end_y", 0.5)) - float(trend.get("start_y", 0.5))), default={})
        if lead_trend:
            sentence = (
                f"{lead_trend['label']} moved {lead_trend['change_label']} "
                f"from {lead_trend['first_value_label']} to {lead_trend['latest_value_label']}."
            )
        else:
            sentence = "Not enough chart movement was found for a reliable reading."
        verdict = "Constructive" if tone_score > 0 else "Caution" if tone_score < 0 else "Watch"
        headline = self._chart_analysis_headline(chart, verdict, lead_trend)
        takeaways = self._chart_analysis_takeaways(trends)
        return {
            "ticker": chart.get("ticker", ""),
            "chart_id": chart.get("chart_id", ""),
            "mode": chart.get("mode", ""),
            "window": chart.get("window", "all"),
            "analysis_mode": "rule_based",
            "verdict": verdict,
            "headline": headline,
            "sentence": sentence,
            "commentary": self._chart_analysis_commentary(chart, trends, verdict, lead_trend),
            "takeaways": takeaways,
            "points": unique_highlights[:4],
            "trends": trends,
            "follow_up": self._chart_follow_up(chart, trends),
        }

    def _extract_llm_chart_analysis(self, text: str, fallback: dict[str, Any]) -> dict[str, Any] | None:
        cleaned = str(text or "").strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
            cleaned = re.sub(r"```$", "", cleaned).strip()
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        valid_points = {
            (str(point.get("series_key", "")), str(point.get("date", "")))
            for point in fallback.get("points", [])
            if isinstance(point, dict)
        }
        safe_points: list[dict[str, Any]] = []
        raw_points = parsed.get("points")
        if isinstance(raw_points, list):
            for point in raw_points[:4]:
                if not isinstance(point, dict):
                    continue
                key = _clean_text(point.get("series_key"), limit=80)
                date = _clean_text(point.get("date"), limit=10)
                if valid_points and (key, date) not in valid_points:
                    continue
                tone = _clean_text(point.get("tone"), limit=20)
                if tone not in {"warning", "positive", "neutral"}:
                    tone = "neutral"
                safe_points.append(
                    {
                        "series_key": key,
                        "label": _clean_text(point.get("label"), limit=60),
                        "date": date,
                        "value_label": _clean_text(point.get("value_label"), limit=40),
                        "tone": tone,
                        "reason": _clean_text(point.get("reason"), limit=150),
                    }
                )
        safe_takeaways: list[dict[str, str]] = []
        raw_takeaways = parsed.get("takeaways")
        if isinstance(raw_takeaways, list):
            for takeaway in raw_takeaways[:3]:
                if not isinstance(takeaway, dict):
                    continue
                tone = _clean_text(takeaway.get("tone"), limit=20)
                if tone not in {"warning", "positive", "neutral"}:
                    tone = "neutral"
                safe_takeaways.append(
                    {
                        "tone": tone,
                        "label": _clean_text(takeaway.get("label"), limit=50),
                        "text": _clean_text(takeaway.get("text"), limit=220),
                    }
                )
        return {
            "verdict": _clean_text(parsed.get("verdict"), limit=40) or fallback.get("verdict", "Watch"),
            "headline": _clean_text(parsed.get("headline"), limit=96) or fallback.get("headline", ""),
            "sentence": _clean_text(parsed.get("sentence"), limit=220) or fallback.get("sentence", ""),
            "commentary": _clean_text(parsed.get("commentary"), limit=520) or fallback.get("commentary", ""),
            "takeaways": safe_takeaways or fallback.get("takeaways", []),
            "points": safe_points or fallback.get("points", []),
            "follow_up": fallback.get("follow_up", {}),
        }

    def _call_llm_for_chart_analysis(
        self,
        chart: dict[str, Any],
        fallback: dict[str, Any],
        model: str,
        endpoint: str,
        api_key: str,
    ) -> dict[str, Any] | None:
        if not is_safe_https_endpoint(endpoint):
            raise ValueError("LLM endpoint must use HTTPS, except localhost endpoints.")
        request_format = llm_request_format(endpoint)
        compact_series = []
        for series in chart.get("series", [])[:3]:
            if not isinstance(series, dict):
                continue
            points = series.get("points", [])
            compact_series.append(
                {
                    "key": series.get("key"),
                    "label": series.get("base_label") or series.get("label"),
                    "unit": series.get("unit"),
                    "points": points[:8] + points[-8:] if isinstance(points, list) and len(points) > 16 else points,
                }
            )
        system_prompt = (
            "You are a cautious financial chart analyst. Return JSON only. "
            "Write like a concise Smartlab-style analyst: practical, numeric, and slightly vivid, "
            "but without jokes that hide the evidence. Use only supplied chart data and candidate points. "
            "Do not give investment advice."
        )
        user_prompt = (
            "Analyze this chart for a dashboard. Return JSON with schema "
            "{\"verdict\":\"Constructive|Watch|Caution\",\"headline\":\"short analyst headline\","
            "\"sentence\":\"one sentence with numeric evidence\","
            "\"commentary\":\"2-3 concise analyst sentences explaining what matters, what is good/bad, and what to watch next\","
            "\"takeaways\":[{\"tone\":\"positive|warning|neutral\",\"label\":\"short label\",\"text\":\"numeric takeaway\"}],"
            "\"follow_up\":{\"chart_id\":\"use supplied follow_up.chart_id\",\"label\":\"use supplied follow_up.label\",\"reason\":\"why this chart verifies the hypothesis\"},"
            "\"points\":[{\"series_key\":\"...\",\"label\":\"...\",\"date\":\"YYYY-MM-DD\",\"value_label\":\"...\","
            "\"tone\":\"warning|positive|neutral\",\"reason\":\"short reason\"}]}. "
            "The sentence must be one sentence, no more than 28 words, and include at least one number. "
            "The headline must be under 12 words and should summarize the chart, not repeat the chart title. "
            "The commentary must stay under 95 words total and include one hypothesis, one risk, and one next check. "
            "Only choose points from candidate_points; keep trend interpretation consistent with rule_trends.\n\n"
            f"Chart:\n{json.dumps({k: chart.get(k) for k in ['ticker', 'chart_id', 'mode', 'scope', 'title', 'description']}, ensure_ascii=False)}\n"
            f"Series:\n{json.dumps(compact_series, ensure_ascii=False)}\n"
            f"Rule trends and candidate points:\n{json.dumps({'rule_trends': fallback.get('trends', []), 'candidate_points': fallback.get('points', []), 'follow_up': fallback.get('follow_up', {})}, ensure_ascii=False)}"
        )
        request_payload = (
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.2,
            }
            if request_format == "chat_completions"
            else {
                "model": model,
                "instructions": system_prompt,
                "input": user_prompt,
                "store": False,
            }
        )
        response_payload = self._post_llm_json(endpoint, request_payload, api_key)
        text = extract_chat_completion_text(response_payload) if request_format == "chat_completions" else extract_response_text(response_payload)
        if not text:
            raise UpstreamServiceError(HTTPStatus.BAD_GATEWAY, "LLM returned an empty response.")
        return self._extract_llm_chart_analysis(text, fallback)

    def analyze_portfolio_ticker(self, payload: dict[str, Any]) -> dict[str, Any]:
        ticker = _safe_ticker(payload.get("ticker")) or ""
        if not ticker:
            raise ValueError("ticker is required")
        settings = self.load_settings()
        portfolio_summary = summarize_portfolio(settings["portfolio"])
        holdings = {
            str(holding.get("ticker", "")).upper(): holding
            for holding in portfolio_summary.get("holdings", [])
            if isinstance(holding, dict)
        }
        if ticker not in holdings and ticker not in DOW30_TICKER_SET:
            raise ValueError("ticker is not available for analysis")
        chart_ids = payload.get("chart_ids")
        if not isinstance(chart_ids, list) or not chart_ids:
            chart_ids = ["company_revenue_eps", "company_margins", "company_balance_stress"]
        chart_ids = [_clean_text(chart_id, limit=80) for chart_id in chart_ids[:6]]
        chart_payloads = [
            self.chart_lab_payload({"ticker": [ticker], "chart_id": [chart_id], "mode": ["structured"]})
            for chart_id in chart_ids
        ]
        chart_payloads = [chart for chart in chart_payloads if chart.get("available")]
        documents = self._ticker_evidence_documents(ticker, limit=10)
        llm = payload.get("llm", {}) if isinstance(payload.get("llm", {}), dict) else {}
        api_key, model, endpoint, used_server_llm = resolve_llm_config(_llm_config_for_task(llm, "portfolio"))
        api_key_received = bool(str(llm.get("api_key", "")).strip())
        graph_api_key, graph_model, graph_endpoint, _ = resolve_llm_config(_llm_config_for_task(llm, "graph"))
        chart_analyses: list[dict[str, Any]] = []
        for index, chart in enumerate(chart_payloads[:4]):
            chart_analysis = self._rule_based_chart_analysis(chart)
            if graph_api_key and index == 0:
                try:
                    llm_chart_analysis = self._call_llm_for_chart_analysis(chart, chart_analysis, graph_model, graph_endpoint, graph_api_key)
                except Exception as exc:
                    chart_analysis["llm_error"] = str(exc)
                else:
                    if llm_chart_analysis:
                        chart_analysis.update(llm_chart_analysis)
                        chart_analysis["analysis_mode"] = "llm"
                        chart_analysis["model"] = graph_model
            chart_analyses.append(chart_analysis)
        chart_cards = [
            self._chart_payload_for_dashboard(chart, chart_analyses[index] if index < len(chart_analyses) else None)
            for index, chart in enumerate(chart_payloads[:4])
        ]
        if api_key:
            markdown = self._call_llm_for_portfolio_ticker(
                ticker=ticker,
                holding=holdings.get(ticker, {}),
                charts=chart_payloads[:4],
                chart_analyses=chart_analyses,
                documents=documents,
                model=model,
                endpoint=endpoint,
                api_key=api_key,
            )
            llm_used = True
        else:
            markdown = self._local_portfolio_ticker_markdown(ticker, holdings.get(ticker, {}), chart_analyses, documents)
            model = "rule-based-local"
            used_server_llm = False
            llm_used = False
        return {
            "ticker": ticker,
            "holding": holdings.get(ticker, {}),
            "llm_used": llm_used,
            "server_llm": used_server_llm,
            "model": model,
            "api_key_received": api_key_received,
            "api_key_persisted": False,
            "analysis_markdown": markdown,
            "charts": chart_cards,
            "documents": documents,
            "available_chart_ids": [chart.get("chart_id") for chart in chart_payloads],
        }

    def _chart_payload_for_dashboard(self, chart: dict[str, Any], analysis: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "title": _clean_text(chart.get("title"), limit=80),
            "subtitle": _clean_text(chart.get("description"), limit=140),
            "chart_id": _clean_text(chart.get("chart_id"), limit=80),
            "analysis": analysis or None,
            "series": [
                {
                    "key": _clean_text(series.get("key"), limit=80),
                    "label": _clean_text(series.get("base_label") or series.get("label"), limit=60),
                    "unit": _clean_text(series.get("unit"), limit=30),
                    "points": [
                        {
                            "date": _clean_text(point.get("date"), limit=10),
                            "value": _float_value(point.get("value")),
                        }
                        for point in series.get("points", [])[-36:]
                        if isinstance(point, dict)
                    ],
                }
                for series in chart.get("series", [])[:3]
                if isinstance(series, dict)
            ],
        }

    def _ticker_evidence_documents(self, ticker: str, *, limit: int = 10) -> list[dict[str, Any]]:
        raw_count, rows = self._search_rows(ticker)
        del raw_count
        rows = self._historical_search_rows(rows)
        ticker_rows = [
            row
            for row in rows
            if ticker in {str(value).upper() for value in (row.get("matched_tickers", []) or row.get("matched_holdings", []) or [])}
            or re.search(rf"\b{re.escape(ticker)}\b", str(row.get("title", "")), flags=re.IGNORECASE)
        ]
        if not ticker_rows:
            ticker_rows = rows
        self._sort_results_fresh_first(ticker_rows)
        docs: list[dict[str, Any]] = []
        for row in ticker_rows[:limit]:
            doc_id = _clean_text(row.get("doc_id") or row.get("id"), limit=160)
            docs.append(
                {
                    "doc_id": doc_id,
                    "title": _clean_text(row.get("title"), limit=180),
                    "available_at": _clean_text(row.get("available_at") or row.get("published_at"), limit=40),
                    "source_type": _clean_text(row.get("source_type"), limit=50),
                    "site_name": _clean_text(row.get("site_name") or _site_label(str(row.get("url", ""))), limit=80),
                    "excerpt": _clean_text(row.get("excerpt"), limit=320),
                    "document_url": f"/document/{quote(doc_id)}" if doc_id and not doc_id.startswith("http") else "",
                    "source_url": _clean_text(row.get("url"), limit=400),
                    "signal": round(float((row.get("text_signal") or {}).get("calibrated_signal_score", 0.0) or 0.0), 4),
                    "risk": round(float((row.get("text_signal") or {}).get("risk_alert_score", 0.0) or 0.0), 4),
                    "upside": round(float((row.get("text_signal") or {}).get("upside_signal_score", 0.0) or 0.0), 4),
                }
            )
        return docs

    def _local_portfolio_ticker_markdown(
        self,
        ticker: str,
        holding: dict[str, Any],
        chart_analyses: list[dict[str, Any]],
        documents: list[dict[str, Any]],
    ) -> str:
        lead = next((analysis for analysis in chart_analyses if analysis.get("sentence")), {})
        verdict = lead.get("verdict", "Watch")
        sentence = lead.get("sentence", "No strong chart trend is available yet.")
        doc_lines = "\n".join(
            f"- [{doc['title']}]({doc['document_url']}) - {doc.get('site_name', 'source')}, {doc.get('available_at', '')[:10]}"
            for doc in documents[:5]
            if doc.get("document_url")
        )
        return (
            f"## Verdict\n{verdict}: {sentence}\n\n"
            "| Item | Value |\n|---|---:|\n"
            f"| Ticker | {ticker} |\n"
            f"| Portfolio weight | {float(holding.get('weight', 0.0) or 0.0) * 100:.1f}% |\n"
            f"| Evidence docs | {len(documents)} |\n\n"
            "## Evidence Documents\n"
            f"{doc_lines or '- No linked local documents found.'}\n\n"
            "## Note\nRule-based analysis is active because no LLM key is configured; use it as a rough pre-check."
        )

    def _call_llm_for_portfolio_ticker(
        self,
        *,
        ticker: str,
        holding: dict[str, Any],
        charts: list[dict[str, Any]],
        chart_analyses: list[dict[str, Any]],
        documents: list[dict[str, Any]],
        model: str,
        endpoint: str,
        api_key: str,
    ) -> str:
        if not is_safe_https_endpoint(endpoint):
            raise ValueError("LLM endpoint must use HTTPS, except localhost endpoints.")
        request_format = llm_request_format(endpoint)
        compact_charts = [
            {
                "title": chart.get("title"),
                "description": chart.get("description"),
                "series": [
                    {
                        "key": series.get("key"),
                        "label": series.get("base_label") or series.get("label"),
                        "unit": series.get("unit"),
                        "latest": series.get("latest"),
                        "points": series.get("points", [])[-18:],
                    }
                    for series in chart.get("series", [])[:3]
                    if isinstance(series, dict)
                ],
            }
            for chart in charts
        ]
        safe_docs = [
            {key: doc.get(key) for key in ("title", "available_at", "source_type", "document_url", "excerpt", "signal", "risk", "upside")}
            for doc in documents[:8]
        ]
        system_prompt = (
            "You are a portfolio-aware equity analyst. Use only supplied chart data and evidence documents. "
            "Write concise Markdown with tables. Use real newline characters between headings, paragraphs, "
            "tables, and bullet items. Never compress the answer into one line. Do not make investment advice; "
            "give an evidence verdict."
        )
        user_prompt = (
            "Analyze the selected portfolio ticker using exactly this Markdown structure:\n"
            "## Verdict\nOne sentence.\n\n"
            "## Compact Metric Table\nA Markdown table with 3-5 columns.\n\n"
            "## What The Charts Say\n3-5 bullet points.\n\n"
            "## Good And Suspicious Points\nA small Markdown table.\n\n"
            "## Evidence Documents\n3-5 bullet links using supplied document_url.\n\n"
            "## Conclusion\n1-2 sentences: say whether a stronger LLM should generate an extra scenario diagram, KPI bridge, or forecast-style chart, and name the required data. Do not invent forecasts.\n\n"
            "Keep it concise and numeric. If evidence is weak, say so clearly. "
            "Every heading must start on its own line, every bullet must start on its own line, and tables must use real Markdown rows with newlines.\n\n"
            f"Ticker and holding:\n{json.dumps({'ticker': ticker, 'holding': holding}, ensure_ascii=False)}\n\n"
            f"Analyst Charts:\n{json.dumps(compact_charts, ensure_ascii=False)}\n\n"
            f"Rule trend pre-check:\n{json.dumps(chart_analyses, ensure_ascii=False)}\n\n"
            f"Evidence documents:\n{json.dumps(safe_docs, ensure_ascii=False)}"
        )
        request_payload = (
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.25,
            }
            if request_format == "chat_completions"
            else {
                "model": model,
                "instructions": system_prompt,
                "input": user_prompt,
                "store": False,
            }
        )
        response_payload = self._post_llm_json(endpoint, request_payload, api_key)
        text = extract_chat_completion_text(response_payload) if request_format == "chat_completions" else extract_response_text(response_payload)
        if not text:
            raise UpstreamServiceError(HTTPStatus.BAD_GATEWAY, "LLM returned an empty response.")
        return self._normalize_portfolio_markdown(text)

    def _normalize_portfolio_markdown(self, text: str) -> str:
        cleaned = re.sub(r"\r\n?", "\n", str(text or "")).strip()
        cleaned = re.sub(
            r"\s*(##\s+(?:Verdict|Compact Metric Table|What The Charts Say|Good And Suspicious Points|Evidence Documents|Conclusion)\b)",
            r"\n\n\1",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"(##\s+Evidence Documents)\s+-\s+", r"\1\n- ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+-\s+(?=(?:Apple|Microsoft|JPMorgan|3M|Home Depot|Chevron|[A-Z]{2,5})[^.\n]{0,120})", "\n- ", cleaned)
        cleaned = re.sub(r"\s+(\|[^|\n]{1,80}\|[^|\n]{1,120}\|)", r"\n\1", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned[:8_000]

    def _search_score(self, record: dict[str, Any], query: str) -> float:
        text = f"{record.get('title', '')} {record.get('body', '')}".lower()
        terms = self._query_scoring_terms(query)
        if not terms:
            return 0.25
        title = str(record.get("title", "")).lower()
        score = 0.0
        for term in terms:
            if term in title:
                score += 2.0
            if term in text:
                score += 1.0
        for ticker in record.get("matched_tickers", []) or []:
            if str(ticker).lower() in terms:
                score += 2.5
        return score

    def _query_scoring_terms(self, query: str) -> list[str]:
        terms = [
            re.sub(r"[^a-z0-9]", "", term.lower())
            for term in tokenize(query)
            if re.sub(r"[^a-z0-9]", "", term.lower())
        ]
        for ticker in self._query_entity_tickers(query):
            terms.append(ticker.lower())
            company = next((row for row in DOW30_COMPANIES if row["ticker"] == ticker), None)
            if company:
                terms.extend(_company_name_terms(company)[:3])
        return list(dict.fromkeys(term for term in terms if term))

    def _signal_discovery_mode(self, query: str) -> str:
        text = query.lower()
        if any(term in text for term in ("risk", "sell", "avoid", "danger", "downside", "warning")):
            return "risk"
        if any(term in text for term in ("invest", "buy", "opportunity", "best stock", "which stock", "what stock", "signal")):
            return "opportunity"
        return ""

    def _record_signal_features(self, record: dict[str, Any]) -> dict[str, Any]:
        return self.text_features().get(str(record.get("doc_id", "")), {})

    def _compact_tags(self, record: dict[str, Any], text_features: dict[str, Any] | None = None, *, limit: int = 3) -> list[str]:
        text_features = text_features or self._record_signal_features(record)
        tags: list[str] = []
        for ticker in record.get("matched_tickers", []) or []:
            ticker_text = str(ticker).upper()
            if ticker_text and ticker_text != "MARKET" and ticker_text not in tags:
                tags.append(ticker_text)
        for tag in (text_features.get("active_signals", []) or []) + (record.get("event_tags", []) or []):
            clean = str(tag or "").removeprefix("signal_").replace("_", " ").strip().title()
            if clean in {"Filing", "Sec Section", "Sec Filing", "Document"}:
                continue
            if clean and clean.upper() != "MARKET" and clean not in tags:
                tags.append(clean)
        return tags[:limit]

    def _intensity_label(self, value: float) -> str:
        if value >= 0.67:
            return "high"
        if value >= 0.34:
            return "medium"
        return "low"

    def _macro_rule_payload(self, record: dict[str, Any]) -> dict[str, Any] | None:
        rule = evaluate_official_macro(record)
        if rule is None:
            return None
        return {
            "series_id": rule.series_id,
            "value": rule.value,
            "impact_direction": rule.impact_direction,
            "risk_intensity": rule.risk_intensity,
            "uncertainty_intensity": rule.uncertainty_intensity,
            "sentiment_proxy": rule.sentiment_proxy,
            "opportunity_intensity": rule.opportunity_intensity,
            "portfolio_action_relevance": rule.portfolio_action_relevance,
            "reason": rule.reason,
        }

    def _macro_rule_excerpt(self, record: dict[str, Any]) -> str:
        rule = evaluate_official_macro(record)
        if rule is None:
            return excerpt(str(record.get("body", "")), 220)
        family = str(record.get("macro_family") or "macro").replace("_", " ").title()
        series = str(record.get("macro_series_title") or rule.series_id or "Macro series")
        units = str(record.get("macro_units") or "").strip()
        value = "n/a" if rule.value is None else f"{rule.value:g}{(' ' + units) if units else ''}"
        direction = {
            "positive": "supportive",
            "negative": "adverse",
            "mixed": "mixed",
            "neutral": "neutral",
        }.get(rule.impact_direction, rule.impact_direction or "neutral")
        return (
            f"{family}: {direction}. {series}: {value}. "
            f"Risk {self._intensity_label(rule.risk_intensity)}; "
            f"upside {self._intensity_label(rule.opportunity_intensity)}."
        )

    def _result_excerpt(self, record: dict[str, Any], text_features: dict[str, Any]) -> str:
        signal_score = float(text_features.get("calibrated_signal_score", 0.0) or 0.0)
        risk_score = float(text_features.get("risk_alert_score", 0.0) or 0.0)
        upside_score = float(text_features.get("upside_signal_score", 0.0) or 0.0)
        source_type = str(record.get("source_type", "") or "").lower()
        if source_type.startswith("official_macro"):
            return self._macro_rule_excerpt(record)
        if signal_score > 0:
            tags = self._compact_tags(record, text_features, limit=2)
            label = ", ".join(tags) if tags else str(record.get("source_type", "evidence") or "evidence").replace("_", " ")
            return (
                f"Signal {signal_score:.2f}. Risk {risk_score:.2f}. Upside {upside_score:.2f}. "
                f"{label} evidence available {str(record.get('available_at', '') or '')[:10]}."
            )
        return excerpt(str(record.get("body", "")), 220)

    def _query_field_profile(self, query: str) -> set[str]:
        normalized = " ".join(str(query or "").lower().split())
        profile: set[str] = set()
        if any(term in normalized for term in ("earnings", "guidance", "eps", "results of operations")):
            profile.add("earnings_guidance")
        if any(term in normalized for term in ("risk", "risk factors", "litigation", "lawsuit", "legal", "regulatory", "supply chain")):
            profile.add("company_risk")
        if any(term in normalized for term in ("litigation", "lawsuit", "legal proceedings", "regulatory")):
            profile.add("legal_regulatory")
        if "supply chain" in normalized:
            profile.add("supply_chain")
        if any(term in normalized for term in ("energy", "oil", "wti", "crude", "commodity demand")):
            profile.add("energy")
        if any(term in normalized for term in ("consumer demand", "consumer spending", "spending", "payments demand", "card spending")):
            profile.add("consumer_demand")
        if any(term in normalized for term in ("bank", "banks", "banking", "credit cycle", "credit risk")):
            profile.add("bank_credit")
        if any(term in normalized for term in ("margin", "margins", "profitability", "cost pressure")):
            profile.add("margin_pressure")
        return profile

    def _record_field_text(self, record: dict[str, Any], features: dict[str, Any]) -> str:
        tags = [str(tag) for tag in (record.get("event_tags", []) or [])]
        tags.extend(str(tag) for tag in (features.get("active_signals", []) or []))
        return " ".join(
            [
                str(record.get("title", "")),
                str(record.get("source_type", "")),
                " ".join(tags),
            ]
        ).lower().replace("_", " ")

    def _field_alignment_score(self, record: dict[str, Any], query: str, features: dict[str, Any]) -> float:
        profile = self._query_field_profile(query)
        if not profile:
            return 0.0
        field_text = self._record_field_text(record, features)
        source_type = str(record.get("source_type", "") or "").lower()
        matched_tickers = {str(ticker).upper() for ticker in record.get("matched_tickers", []) or []}
        score = 0.0

        if "earnings_guidance" in profile:
            if any(term in field_text for term in ("earnings guidance", "earnings release", "results of operations", "item 2.02")):
                score += 5.0
            elif "earnings release candidate" in field_text:
                score += 3.0
            elif source_type.startswith("company_") and "press release" in field_text:
                score -= 2.0

        if "company_risk" in profile:
            if any(term in field_text for term in ("risk factors", "company risk", "market risk", "legal regulatory", "legal proceedings", "mda", "management s discussion")):
                score += 5.0
            if any(term in field_text for term in ("earnings guidance", "earnings release")) and not any(term in field_text for term in ("risk", "legal", "supply chain")):
                score -= 7.0

        if "legal_regulatory" in profile and any(term in field_text for term in ("legal regulatory", "legal proceedings", "litigation", "lawsuit", "regulatory")):
            score += 4.0
        if "supply_chain" in profile and any(term in field_text for term in ("supply chain", "supplier", "production", "inventory", "company risk", "mda")):
            score += 3.0
        if "energy" in profile and any(term in field_text for term in ("energy", "oil", "commodity", "market risk", "risk factors")):
            score += 4.0
        if "consumer_demand" in profile:
            if any(term in field_text for term in ("consumer demand", "consumer spending", "card member spending", "sales", "revenue")):
                score += 4.0
            if source_type.startswith("official_macro") and "credit" in field_text and "consumer" not in field_text:
                score -= 3.0
        if "bank_credit" in profile:
            financial_hits = matched_tickers.intersection(FINANCIAL_CREDIT_TICKERS)
            if financial_hits:
                score += 5.0
            elif matched_tickers and not any(DOW30_SECTOR_BY_TICKER.get(ticker) == "Financials" for ticker in matched_tickers):
                score -= 4.0
            if any(term in field_text for term in ("credit", "loan", "deposit", "financial statements", "mda", "company risk")):
                score += 3.0
        if "margin_pressure" in profile and any(term in field_text for term in ("margin pressure", "operating income", "gross margin", "cost", "mda")):
            score += 4.0

        return score

    def _feature_aware_score(
        self,
        record: dict[str, Any],
        lexical_score: float,
        query: str,
        features: dict[str, Any] | None = None,
    ) -> float:
        features = features if features is not None else self._record_signal_features(record)
        source_score = float(record.get("source_credibility", 0.0) or 0.0)
        signal_score = float(features.get("calibrated_signal_score", 0.0) or 0.0)
        event_score = float(features.get("event_severity_score", 0.0) or 0.0)
        usefulness = float(features.get("historical_usefulness_score", 0.0) or 0.0)
        mode = self._signal_discovery_mode(query)
        mode_score = 0.0
        matched_tickers = {str(ticker).upper() for ticker in record.get("matched_tickers", []) or []}
        has_stock_ticker = any(ticker and ticker != "MARKET" for ticker in matched_tickers)
        source_type = str(record.get("source_type", "")).lower()
        stock_evidence_bonus = 0.0
        if mode == "opportunity":
            mode_score = float(features.get("upside_signal_score", 0.0) or 0.0)
            stock_evidence_bonus = 2.0 if has_stock_ticker else -1.25
            if source_type.startswith("official_macro"):
                stock_evidence_bonus -= 1.25
        elif mode == "risk":
            mode_score = float(features.get("risk_alert_score", 0.0) or 0.0)
            stock_evidence_bonus = 0.75 if has_stock_ticker else 0.0
        source_intent_bonus = self._source_intent_boost(record, query)
        entity_alignment = self._entity_alignment_score(record, query)
        field_alignment = self._field_alignment_score(record, query, features)
        return (
            lexical_score
            + 1.15 * signal_score
            + 0.75 * mode_score
            + 0.35 * event_score
            + 0.30 * source_score
            + 2.50 * usefulness
            + stock_evidence_bonus
            + source_intent_bonus
            + entity_alignment
            + field_alignment
        )

    def _fts_query(self, query: str) -> str:
        entity_terms = self._entity_expansion_terms(query)
        if entity_terms:
            return " OR ".join(entity_terms[:8])
        terms = []
        for token in tokenize(query):
            clean = re.sub(r"[^a-z0-9_]", "", token.lower())
            if len(clean) < 2 or clean in SEARCH_STOPWORDS:
                continue
            terms.append(clean)
        unique_terms = list(dict.fromkeys(terms))[:12]
        return " OR ".join(f"{term}*" for term in unique_terms)

    def _entity_expansion_terms(self, query: str) -> list[str]:
        tickers = self._query_entity_tickers(query)
        if not tickers:
            return []
        terms: list[str] = []
        for company in DOW30_COMPANIES:
            if company["ticker"] not in tickers:
                continue
            ticker = company["ticker"].lower()
            name_tokens = [
                token
                for token in _company_name_terms(company)
                if len(token) >= 3 or (len(token) >= 2 and any(char.isdigit() for char in token))
            ]
            terms.append(ticker)
            terms.extend(name_tokens[:3])
        return list(dict.fromkeys(term for term in terms if term and term not in SEARCH_STOPWORDS))

    def _matched_entity_tickers(self, query: str) -> list[str]:
        query_tokens = [
            token
            for token in _entity_words(query, stopwords=ENTITY_QUERY_STOPWORDS)
            if len(token) >= 2
        ]
        if not query_tokens:
            return []
        query_phrase = " ".join(query_tokens)
        query_compact = "".join(query_tokens)
        tickers: list[str] = []
        for company in DOW30_COMPANIES:
            ticker = company["ticker"].lower()
            name_tokens = _company_name_terms(company)
            aliases = _company_aliases(company)
            if (
                ticker in query_tokens
                or any(ticker.startswith(token) and len(token) >= 2 for token in query_tokens)
                or any(alias == query_phrase for alias in aliases if len(alias) >= 2)
                or any(alias == query_compact for alias in aliases if len(alias) >= 2)
                or any(alias.startswith(query_phrase) or query_phrase.startswith(alias) for alias in aliases if len(alias) >= 3)
                or any(alias.startswith(query_compact) or query_compact.startswith(alias) for alias in aliases if len(alias) >= 3)
                or any(name_token == token for name_token in name_tokens for token in query_tokens if len(token) >= 2)
                or any(any(name_token.startswith(token) for name_token in name_tokens) for token in query_tokens if len(token) >= 3)
            ):
                tickers.append(company["ticker"])
        return list(dict.fromkeys(tickers))

    def _query_entity_tickers(self, query: str) -> list[str]:
        intent_tickers = classify_query_intent(query).matched_tickers
        local_tickers = self._matched_entity_tickers(query)
        return list(dict.fromkeys([*intent_tickers, *local_tickers]))

    def _record_matches_query_entity(self, record: dict[str, Any], query_tickers: list[str]) -> bool:
        if not query_tickers:
            return True
        matched = {str(ticker).upper() for ticker in record.get("matched_tickers", []) or []}
        return bool(matched.intersection(query_tickers))

    def _entity_alignment_score(self, record: dict[str, Any], query: str) -> float:
        query_tickers = self._query_entity_tickers(query)
        if not query_tickers:
            return 0.0
        matched = {str(ticker).upper() for ticker in record.get("matched_tickers", []) or []}
        if matched.intersection(query_tickers):
            return 6.0
        source_type = str(record.get("source_type", "") or "").lower()
        weights = self._query_source_intent_weights(query)
        macro_is_primary = weights.get("macro", 0.0) > max(weights.get("sec_filings", 0.0), weights.get("company_ir", 0.0))
        if source_type.startswith("official_macro"):
            return 0.0 if macro_is_primary else -7.0
        if any(ticker and ticker != "MARKET" for ticker in matched):
            return -10.0
        return -4.0

    def _records_from_index_rows(self, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for row in rows:
            try:
                parsed = json.loads(str(row["record_json"]))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
        return records

    def _freshness_value(self, row: dict[str, Any]) -> str:
        return str(row.get("available_at") or row.get("published_at") or "")

    def _is_historical_search_row(self, row: dict[str, Any]) -> bool:
        available_at = self._freshness_value(row)
        return bool(available_at) and available_at <= SEARCH_CUTOFF

    def _historical_search_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [row for row in rows if self._is_historical_search_row(row)]

    def _sort_results_fresh_first(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows.sort(
            key=lambda row: (
                self._freshness_value(row),
                float(row.get("score", 0.0) or 0.0),
                str(row.get("doc_id", "")),
            ),
            reverse=True,
        )
        for rank, row in enumerate(rows, start=1):
            row["rank"] = rank
        return rows

    def _sort_search_results(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows.sort(
            key=lambda row: (
                float(row.get("score", 0.0) or 0.0),
                self._freshness_value(row),
                str(row.get("doc_id", "")),
            ),
            reverse=True,
        )
        for rank, row in enumerate(rows, start=1):
            row["rank"] = rank
        return rows

    def _normalized_group_title(self, title: str) -> str:
        text = html.unescape(str(title or "")).lower()
        text = text.replace("&", " and ")
        text = re.sub(r"\bfiled\s+\d{4}-\d{2}-\d{2}\b", "filed", text)
        text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", text)
        text = re.sub(r"\b(19|20)\d{2}\b", " ", text)
        text = re.sub(r"\bq[1-4]\s*(fy)?\s*\d{2,4}\b", "quarter", text)
        text = re.sub(r"\b(first|second|third|fourth)\s+quarter\s+\d{2,4}\b", r"\1 quarter", text)
        text = re.sub(r"\bfiscal\s+\d{2,4}\b", "fiscal", text)
        text = re.sub(r"\bitem\s+([0-9]+)\s*([a-z])\b", r"item \1\2", text)
        text = re.sub(r"[^a-z0-9]+", " ", text)
        return " ".join(token for token in text.split() if token)

    def _macro_snapshot_group(self, row: dict[str, Any]) -> tuple[str, str, str] | None:
        source_type = str(row.get("source_type", "") or "").lower()
        if not source_type.startswith("official_macro"):
            return None
        normalized_title = "official macro snapshots"
        display_title = "US macro snapshots"
        raw_key = "official_macro_snapshots"
        digest = hashlib.sha1(raw_key.encode("utf-8")).hexdigest()[:16]
        return f"g_{digest}", normalized_title, display_title

    def _sec_filing_group(self, row: dict[str, Any]) -> tuple[str, str, str] | None:
        source_type = str(row.get("source_type", "") or "").lower()
        if not source_type.startswith("sec_filing"):
            return None
        doc_id = str(row.get("doc_id", "") or "")
        title = str(row.get("title", "") or "")
        accession_key = doc_id.split("__", 1)[0] if "__" in doc_id else ""
        title_match = re.match(
            r"(.+?\b(?:10-K|10-Q|8-K)\s+filing filed\s+\d{4}-\d{2}-\d{2})\s+-\s+(?:Item|Exhibit)\b",
            title,
            flags=re.IGNORECASE,
        )
        display_title = title_match.group(1) if title_match else title
        normalized_title = self._normalized_group_title(display_title)
        raw_key = f"sec_filing|{accession_key or normalized_title}"
        digest = hashlib.sha1(raw_key.encode("utf-8")).hexdigest()[:16]
        return f"g_{digest}", normalized_title, display_title

    def _search_group_key(self, row: dict[str, Any]) -> tuple[str, str, str]:
        specialized = self._macro_snapshot_group(row) or self._sec_filing_group(row)
        if specialized is not None:
            return specialized
        tickers = ",".join(
            sorted(
                str(ticker).upper()
                for ticker in row.get("matched_tickers", []) or []
                if str(ticker).upper() != "MARKET"
            )
        )
        normalized_title = self._normalized_group_title(str(row.get("title", "")))
        raw_key = "|".join(
            [
                tickers,
                str(row.get("source_type", "") or "").lower(),
                normalized_title,
            ]
        )
        digest = hashlib.sha1(raw_key.encode("utf-8")).hexdigest()[:16]
        return f"g_{digest}", normalized_title, str(row.get("title", "") or "")

    def _group_search_results(self, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        buckets: dict[str, list[dict[str, Any]]] = {}
        titles: dict[str, str] = {}
        display_titles: dict[str, str] = {}
        for row in self._sort_results_fresh_first([dict(item) for item in rows]):
            group_key, normalized_title, display_title = self._search_group_key(row)
            row["group_key"] = group_key
            row["group_normalized_title"] = normalized_title
            buckets.setdefault(group_key, []).append(row)
            titles.setdefault(group_key, normalized_title)
            display_titles.setdefault(group_key, display_title)

        grouped: list[dict[str, Any]] = []
        for group_key, docs in buckets.items():
            leader = dict(docs[0])
            leader["result_kind"] = "group" if len(docs) > 1 else "document"
            leader["group_key"] = group_key
            leader["group_title"] = display_titles.get(group_key, leader.get("title", ""))
            leader["group_count"] = len(docs)
            leader["group_latest_available_at"] = self._freshness_value(docs[0])
            leader["group_normalized_title"] = titles.get(group_key, "")
            leader["group_children"] = docs[1:6]
            leader["group_has_more"] = len(docs) > 6
            grouped.append(leader)

        self._sort_results_fresh_first(grouped)
        return grouped, buckets

    def _folder_descriptor(self, row: dict[str, Any]) -> tuple[str, str, str]:
        source_type = str(row.get("source_type", "") or "").lower()
        if source_type.startswith("sec_filing"):
            return "sec_filings", "SEC filings", "10-K, 10-Q, 8-K sections and exhibits"
        if source_type.startswith("official_macro"):
            return "macro", "Macro", "official rates, credit, volatility, inflation and growth evidence"
        if source_type.startswith("company_"):
            return "company_ir", "Company IR", "earnings releases, press releases, reports and company archives"
        if "news" in source_type or "headline" in source_type:
            return "news", "News", "market headlines and external news-style evidence"
        return "other_sources", "Other evidence", "additional portfolio-relevant documents"

    def _folder_title_summary(self, folder_key: str) -> tuple[str, str]:
        labels = {
            "sec_filings": ("SEC filings", "10-K, 10-Q, 8-K sections and exhibits"),
            "company_ir": ("Company IR", "earnings releases, press releases, reports and company archives"),
            "macro": ("Macro", "official rates, credit, volatility, inflation and growth evidence"),
            "news": ("News", "market headlines and external news-style evidence"),
            "other_sources": ("Other evidence", "additional portfolio-relevant documents"),
        }
        return labels.get(str(folder_key or ""), (str(folder_key or "Evidence folder"), "portfolio-relevant evidence"))

    def _query_source_intent_weights(self, query: str) -> dict[str, float]:
        normalized = " ".join(str(query or "").lower().split())
        intent = classify_query_intent(query)
        matched_tickers = intent.matched_tickers or self._matched_entity_tickers(query)
        weights = {
            "sec_filings": 0.0,
            "company_ir": 0.0,
            "macro": 0.0,
            "news": 0.0,
            "other_sources": 0.0,
        }

        routes = set(intent.source_routes or [])
        if "sec_filings" in routes:
            weights["sec_filings"] += 3.0
        if "official_macro" in routes:
            weights["macro"] += 3.5
        if "market_news" in routes:
            weights["news"] += 1.5

        def has_any(phrases: tuple[str, ...]) -> bool:
            return any(phrase in normalized for phrase in phrases)

        if has_any(("10-k", "10k", "10-q", "10q", "8-k", "8k", "filing", "filings", "sec", "annual report", "quarterly report")):
            weights["sec_filings"] += 4.0
        if matched_tickers and has_any(("risk", "risk factor", "risk factors", "litigation", "lawsuit", "legal", "legal proceedings", "regulatory", "market risk", "credit risk", "supply chain")):
            weights["sec_filings"] += 4.0
        elif has_any(("risk factor", "risk factors", "litigation", "lawsuit", "legal proceedings", "regulatory", "market risk", "credit risk")):
            weights["sec_filings"] += 2.0
        if matched_tickers and has_any(("margin", "margins", "revenue", "sales", "cloud", "mda", "md&a", "financial statements", "cash flow", "debt", "balance sheet", "income statement", "operating income", "net income", "eps")):
            weights["sec_filings"] += 3.0
        elif has_any(("mda", "md&a", "financial statements", "cash flow", "debt", "balance sheet", "income statement", "operating income", "net income")):
            weights["sec_filings"] += 1.5
        if matched_tickers and has_any(("energy demand", "oil demand", "commodity demand")):
            weights["sec_filings"] += 2.5
        if has_any(("earnings", "guidance", "press release", "presentation", "investor day", "buyback", "dividend", "product launch", "launch")):
            weights["company_ir"] += 3.0
        elif "company_ir" in routes or (matched_tickers and not any(weights[key] > 0 for key in ("sec_filings", "macro", "news"))):
            weights["company_ir"] += 0.75
        if matched_tickers and has_any(("consumer spending", "card spending", "payments demand")):
            weights["company_ir"] += 2.5
        if has_any(("fed", "fomc", "macro", "treasury", "yield", "yields", "rate", "rates", "inflation", "cpi", "pce", "vix", "spread", "oil", "unemployment", "payrolls", "housing")):
            weights["macro"] += 4.0
        if matched_tickers and weights["sec_filings"] > 0:
            weights["sec_filings"] += 1.0
        if matched_tickers and weights["company_ir"] > 0:
            weights["company_ir"] += 0.75
        return weights

    def _source_intent_boost(self, row: dict[str, Any], query: str) -> float:
        if not str(query or "").strip():
            return 0.0
        weights = self._query_source_intent_weights(query)
        max_weight = max(weights.values()) if weights else 0.0
        if max_weight <= 0:
            return 0.0

        folder_key, _title, _summary = self._folder_descriptor(row)
        source_type = str(row.get("source_type", "") or "").lower()
        event_text = " ".join(str(tag).lower() for tag in row.get("event_tags", []) or [])
        boost = weights.get(folder_key, 0.0)

        if source_type == "sec_filing_exhibit" and weights["company_ir"] > weights["sec_filings"]:
            if any(term in event_text for term in ("earnings", "guidance", "investor", "press release")):
                boost = max(boost, weights["company_ir"] - 0.5)

        if boost <= 0 and max_weight >= 3.0:
            boost -= min(1.0, max_weight * 0.15)
        return round(boost, 6)

    def _should_folder_results(self, query: str, grouped: list[dict[str, Any]]) -> bool:
        query_tokens = {
            re.sub(r"[^a-z0-9.-]", "", token.lower())
            for token in tokenize(query)
        }
        exact_ticker_match = any(ticker.lower() in query_tokens for ticker in DOW30_TICKER_SET)
        company_name_match = bool(self._query_entity_tickers(query))
        return len(grouped) >= 3 and (exact_ticker_match or company_name_match)

    def _folder_search_results(
        self,
        grouped: list[dict[str, Any]],
        query: str = "",
    ) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        buckets: dict[str, list[dict[str, Any]]] = {}
        descriptors: dict[str, tuple[str, str]] = {}
        for row in grouped:
            folder_key, folder_title, folder_summary = self._folder_descriptor(row)
            buckets.setdefault(folder_key, []).append(row)
            descriptors.setdefault(folder_key, (folder_title, folder_summary))

        folders: list[dict[str, Any]] = []
        for folder_key, children in buckets.items():
            if str(query or "").strip():
                self._sort_search_results(children)
            else:
                self._sort_results_fresh_first(children)
            document_count = sum(int(child.get("group_count", 1) or 1) for child in children)
            title, summary = descriptors.get(folder_key, (folder_key, ""))
            latest = children[0] if children else {}
            folder_intent_score = max((self._source_intent_boost(child, query) for child in children), default=0.0)
            folders.append(
                {
                    "result_kind": "folder",
                    "folder_key": folder_key,
                    "folder_title": title,
                    "folder_summary": summary,
                    "folder_count": len(children),
                    "folder_document_count": document_count,
                    "folder_latest_available_at": self._freshness_value(latest),
                    "folder_intent_score": round(folder_intent_score, 6),
                    "folder_children": children[:5],
                    "folder_has_more": len(children) > 5,
                    "matched_tickers": sorted(
                        {
                            str(ticker).upper()
                            for child in children
                            for ticker in child.get("matched_tickers", []) or []
                            if str(ticker).upper() != "MARKET"
                        }
                    ),
                }
            )
        folders.sort(
            key=lambda row: (
                float(row.get("folder_intent_score", 0.0) or 0.0),
                str(row.get("folder_latest_available_at", "")),
                int(row.get("folder_document_count", 0) or 0),
                str(row.get("folder_title", "")),
            ),
            reverse=True,
        )
        for rank, row in enumerate(folders, start=1):
            row["rank"] = rank
        return folders, buckets

    def _parse_date_prefix(self, value: Any) -> datetime | None:
        text = str(value or "")[:10]
        try:
            return datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            return None

    def _chart_suggestions_for_folder(self, folder_key: str) -> list[dict[str, str]]:
        if folder_key == "sec_filings":
            return [
                {
                    "title": "Risk Language Trend",
                    "why": "tracks whether Item 1A / 10-K risk pressure is rising or fading",
                    "inputs": "risk intensity, event severity, company-risk evidence",
                },
                {
                    "title": "Guidance & Earnings Pressure",
                    "why": "connects filings and 8-K exhibits to forward-looking earnings signals",
                    "inputs": "guidance evidence, sentiment, earnings impact",
                },
                {
                    "title": "Legal / Regulatory Timeline",
                    "why": "separates recurring legal boilerplate from fresh regulatory pressure",
                    "inputs": "legal-regulatory evidence, risk terms, dated filings",
                },
            ]
        if folder_key == "company_ir":
            return [
                {
                    "title": "Revenue & Guidance Momentum",
                    "why": "shows whether company language supports or weakens the growth story",
                    "inputs": "earnings releases, revenue terms, guidance evidence",
                },
                {
                    "title": "Margin Pressure",
                    "why": "captures inflation, costs, supply chain and operating leverage language",
                    "inputs": "margin-pressure evidence, sentiment, uncertainty",
                },
                {
                    "title": "Capital Return & Event Map",
                    "why": "highlights buybacks, dividends, restructuring and major corporate events",
                    "inputs": "capital-return, M&A, event-severity signals",
                },
            ]
        if folder_key == "macro":
            return [
                {
                    "title": "Rates & Yield Curve",
                    "why": "links Fed pressure and curve inversion to equity risk appetite",
                    "inputs": "Treasury yields, policy pressure, rates evidence",
                },
                {
                    "title": "Credit & Volatility Stress",
                    "why": "shows whether macro stress is tightening financial conditions",
                    "inputs": "credit spreads, VIX, volatility regime score",
                },
                {
                    "title": "Growth / Demand Pulse",
                    "why": "summarizes whether official macro supports or pressures revenues",
                    "inputs": "labor, consumer demand, inflation and energy evidence",
                },
            ]
        return [
            {
                "title": "Signal / Risk / Upside Map",
                "why": "separates high-signal documents from low-value noise",
                "inputs": "signal score, risk score, upside score",
            },
            {
                "title": "Event Freshness",
                "why": "shows which documents are still fresh enough to matter",
                "inputs": "available_at, age, source type",
            },
            {
                "title": "Source Quality",
                "why": "compares source reliability and extraction readiness",
                "inputs": "source tier, provenance, body structure",
            },
        ]

    def _doc_feature_float(self, doc: dict[str, Any], key: str) -> float:
        try:
            return float((doc.get("text_signal") or {}).get(key, 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _doc_signal_text(self, doc: dict[str, Any]) -> str:
        parts = [
            str(doc.get("title", "")),
            str(doc.get("excerpt", "")),
            " ".join(str(item) for item in doc.get("active_signals", []) or []),
            " ".join(str(item) for item in (doc.get("text_signal") or {}).get("active_signals", []) or []),
            " ".join(str(item) for item in doc.get("event_tags", []) or []),
            " ".join(str(item) for item in doc.get("risk_terms", []) or []),
        ]
        return " ".join(parts).lower().replace("-", "_").replace(" ", "_")

    def _doc_matches_concept(self, doc: dict[str, Any], concepts: tuple[str, ...]) -> bool:
        text = self._doc_signal_text(doc)
        return any(concept in text for concept in concepts)

    def _folder_month_buckets(self, docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        buckets: dict[str, dict[str, Any]] = {}
        for doc in docs:
            parsed = self._parse_date_prefix(doc.get("available_at"))
            if parsed is None:
                continue
            month_key = parsed.strftime("%Y-%m")
            bucket = buckets.setdefault(month_key, {"date": parsed.date().isoformat(), "docs": []})
            if parsed.date().isoformat() > bucket["date"]:
                bucket["date"] = parsed.date().isoformat()
            bucket["docs"].append(doc)
        return [buckets[key] for key in sorted(buckets)]

    def _folder_series(
        self,
        buckets: list[dict[str, Any]],
        *,
        label: str,
        unit: str,
        metric: str,
        concepts: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        points: list[dict[str, Any]] = []
        for bucket in buckets:
            docs = bucket["docs"]
            if metric == "count":
                value = float(sum(1 for doc in docs if not concepts or self._doc_matches_concept(doc, concepts)))
            elif metric == "source_quality":
                values = [float(doc.get("source_credibility", 0.0) or 0.0) for doc in docs]
                value = sum(values) / max(1, len(values))
            elif metric == "risk_terms":
                value = float(sum(len(doc.get("risk_terms", []) or []) for doc in docs))
            elif metric.startswith("feature_count:"):
                _, feature_key, threshold_text = (metric.split(":", 2) + ["0.05"])[:3]
                threshold = _float_value(threshold_text, 0.05)
                selected = [doc for doc in docs if not concepts or self._doc_matches_concept(doc, concepts)]
                value = float(sum(1 for doc in selected if abs(self._doc_feature_float(doc, feature_key)) >= threshold))
            else:
                selected = [doc for doc in docs if not concepts or self._doc_matches_concept(doc, concepts)]
                values = [self._doc_feature_float(doc, metric) for doc in selected]
                value = sum(values) / max(1, len(values))
            points.append({"date": bucket["date"], "value": round(value, 6)})
        if len(points) == 1:
            points.insert(0, {**points[0], "value": 0.0})
        return {"label": label, "unit": unit, "points": points}

    def _records_by_doc_ids(self, doc_ids: list[str]) -> dict[str, dict[str, Any]]:
        ids = [str(doc_id) for doc_id in dict.fromkeys(doc_ids) if str(doc_id)]
        if not ids:
            return {}
        records: dict[str, dict[str, Any]] = {}
        connection = self._open_search_index()
        if connection is not None:
            try:
                for start in range(0, len(ids), 250):
                    batch = ids[start : start + 250]
                    placeholders = ",".join("?" for _ in batch)
                    rows = connection.execute(
                        f"SELECT record_json FROM documents WHERE doc_id IN ({placeholders})",
                        batch,
                    ).fetchall()
                    for row in rows:
                        try:
                            record = json.loads(str(row["record_json"]))
                        except (json.JSONDecodeError, KeyError, TypeError):
                            continue
                        doc_id = str(record.get("doc_id", "") or "")
                        if doc_id:
                            records[doc_id] = record
            except sqlite3.Error:
                records = {}
            finally:
                connection.close()
        if len(records) < len(ids):
            missing = set(ids) - set(records)
            for record in self.documents():
                doc_id = str(record.get("doc_id", "") or "")
                if doc_id in missing:
                    records[doc_id] = record
        return records

    def _analysis_records_for_results(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        lookup = self._records_by_doc_ids([str(row.get("doc_id", "") or "") for row in rows])
        records: list[dict[str, Any]] = []
        for row in rows:
            doc_id = str(row.get("doc_id", "") or "")
            record = dict(lookup.get(doc_id, row))
            if row.get("text_signal") and not record.get("text_signal"):
                record["text_signal"] = row.get("text_signal")
            if row.get("active_signals") and not record.get("active_signals"):
                record["active_signals"] = row.get("active_signals")
            if row.get("excerpt") and not record.get("excerpt"):
                record["excerpt"] = row.get("excerpt")
            records.append(record)
        return records

    def _document_number_snippets(self, text: str, limit: int = 8) -> list[str]:
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        if not normalized:
            return []
        pattern = re.compile(
            r"(?:\$?\b\d[\d,]*(?:\.\d+)?\s?(?:%|percent|billion|million|trillion|B|M|K|years?|days?|class|companies|businesses)?\b)",
            flags=re.IGNORECASE,
        )
        snippets: list[str] = []
        seen: set[str] = set()
        for match in pattern.finditer(normalized):
            start = max(0, match.start() - 44)
            end = min(len(normalized), match.end() + 58)
            snippet = normalized[start:end].strip(" ,.;:-")
            key = snippet.lower()
            if key and key not in seen:
                seen.add(key)
                snippets.append(_clean_text(snippet, limit=130))
            if len(snippets) >= limit:
                break
        return snippets

    def _document_sentence_candidates(self, text: str, limit: int = 8) -> list[str]:
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        if not normalized:
            return []
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", normalized)
        candidates: list[str] = []
        for part in parts:
            clean = _clean_text(part, limit=240)
            if len(clean) < 35:
                continue
            candidates.append(clean)
            if len(candidates) >= limit:
                break
        if candidates:
            return candidates
        return [_clean_text(normalized, limit=240)]

    def _local_document_summary(self, record: dict[str, Any], body: str) -> dict[str, Any]:
        source_type = _source_type_label(record.get("source_type"))
        title = _clean_text(record.get("title") or "Document", limit=120)
        lower_title = title.lower()
        if "risk" in lower_title:
            headline = "The risk section brought receipts"
        elif "earnings" in lower_title or "10-q" in lower_title or "10-k" in lower_title:
            headline = "Numbers entered the chat, excuses can wait"
        elif "launch" in lower_title or "announces" in lower_title:
            headline = "PR fireworks, fundamentals still need receipts"
        else:
            headline = "Useful evidence, minus the document fog"
        facts = self._document_sentence_candidates(body, limit=5)
        numbers = self._document_number_snippets(body, limit=8)
        tickers = ", ".join(str(ticker) for ticker in (record.get("matched_tickers") or []) if str(ticker))
        if tickers:
            facts.insert(0, f"Linked ticker evidence: {tickers}.")
        return {
            "headline": headline,
            "summary": f"{source_type} summary for {title}. The cached text was compressed into decision-useful facts; use the original link for legal/source verification.",
            "facts": facts[:6],
            "numbers": numbers,
            "mode": "local",
            "model": "local rules",
        }

    def _extract_llm_document_summary(self, text: str, fallback: dict[str, Any]) -> dict[str, Any] | None:
        cleaned = str(text or "").strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
            cleaned = re.sub(r"```$", "", cleaned).strip()
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        facts = []
        for item in parsed.get("facts", [])[:6] if isinstance(parsed.get("facts"), list) else []:
            clean = _clean_text(item, limit=220)
            if clean:
                facts.append(clean)
        numbers = []
        for item in parsed.get("numbers", [])[:8] if isinstance(parsed.get("numbers"), list) else []:
            clean = _clean_text(item, limit=140)
            if clean:
                numbers.append(clean)
        summary = _clean_text(parsed.get("summary"), limit=900)
        headline = _clean_text(parsed.get("headline"), limit=120)
        if not facts and not summary:
            return None
        return {
            "headline": headline or fallback.get("headline", "Document summary"),
            "summary": summary or fallback.get("summary", ""),
            "facts": facts or fallback.get("facts", []),
            "numbers": numbers or fallback.get("numbers", []),
            "mode": "llm",
        }

    def _call_llm_for_document_summary(
        self,
        record: dict[str, Any],
        body: str,
        fallback: dict[str, Any],
        model: str,
        endpoint: str,
        api_key: str,
    ) -> dict[str, Any] | None:
        if not is_safe_https_endpoint(endpoint):
            raise ValueError("LLM endpoint must use HTTPS, except localhost endpoints.")
        request_format = llm_request_format(endpoint)
        compact_record = {
            "title": record.get("title"),
            "source": record.get("source"),
            "source_type": record.get("source_type"),
            "available_at": record.get("available_at"),
            "published_at": record.get("published_at"),
            "matched_tickers": record.get("matched_tickers") or [],
            "event_tags": record.get("event_tags") or [],
            "canonical_url": record.get("canonical_url") or record.get("url") or "",
        }
        body_excerpt = re.sub(r"\s+", " ", body).strip()[:9000]
        system_prompt = (
            "You summarize financial source documents for an investor IR search system. "
            "Return JSON only. Be factual, concise, and do not give buy/sell advice."
        )
        user_prompt = (
            "Create a compact document brief. Return JSON with schema "
            "{\"headline\":\"witty but evidence-grounded headline, max 14 words\","
            "\"summary\":\"one compact paragraph, under 90 words\","
            "\"facts\":[\"3-6 fact bullets, each with concrete evidence\"],"
            "\"numbers\":[\"0-8 numeric facts copied or summarized from the document\"]}. "
            "The whole answer must stay under 300 words. Use a mildly funny or sarcastic headline, "
            "but facts must remain serious. Prefer numbers, dates, named programs, filings, products, "
            "customers, risk items, and financial metrics. If this is a press release with little financial evidence, say that plainly.\n\n"
            f"Metadata:\n{json.dumps(compact_record, ensure_ascii=False)}\n\n"
            f"Document text:\n{body_excerpt}"
        )
        request_payload = (
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.25,
            }
            if request_format == "chat_completions"
            else {
                "model": model,
                "instructions": system_prompt,
                "input": user_prompt,
                "store": False,
            }
        )
        response_payload = self._post_llm_json(endpoint, request_payload, api_key)
        text = extract_chat_completion_text(response_payload) if request_format == "chat_completions" else extract_response_text(response_payload)
        if not text:
            raise UpstreamServiceError(HTTPStatus.BAD_GATEWAY, "LLM returned an empty response.")
        parsed = self._extract_llm_document_summary(text, fallback)
        if parsed:
            parsed["model"] = model
        return parsed

    def _document_summary(self, record: dict[str, Any], body: str) -> dict[str, Any]:
        fallback = self._local_document_summary(record, body)
        doc_hash = str(record.get("document_hash") or hashlib.sha256(body.encode("utf-8")).hexdigest())
        api_key, model, endpoint, used_server_llm = resolve_llm_config({"provider": "paratera_deepseek"})
        cache_key = f"{doc_hash}:{model}:{_endpoint_host(endpoint)}"
        if cache_key in self._document_summary_cache:
            return dict(self._document_summary_cache[cache_key])
        result = dict(fallback)
        result["server_llm"] = used_server_llm
        if api_key:
            try:
                llm_result = self._call_llm_for_document_summary(record, body, fallback, model, endpoint, api_key)
                if llm_result:
                    result = {**llm_result, "server_llm": used_server_llm}
            except Exception as exc:
                result["llm_error"] = _clean_text(exc, limit=220)
        self._document_summary_cache[cache_key] = dict(result)
        return result

    def document_view_html(self, doc_id: str) -> str:
        doc_id = str(doc_id or "").strip()
        record = self._records_by_doc_ids([doc_id]).get(doc_id)
        if not record:
            raise KeyError("Document not found")
        title = str(record.get("title") or doc_id)
        source_url = str(record.get("canonical_url") or record.get("url") or "")
        source_label = _site_label(source_url)
        source_type = _source_type_label(record.get("source_type"))
        available_at = str(record.get("available_at") or "")
        published_at = str(record.get("published_at") or "")
        tickers = ", ".join(str(ticker) for ticker in (record.get("matched_tickers") or []) if str(ticker))
        tags = ", ".join(str(tag).replace("_", " ").title() for tag in (record.get("event_tags") or [])[:6] if str(tag))
        body = str(record.get("body") or record.get("text") or record.get("excerpt") or "")
        if not body.strip():
            body = "No full text is available for this record."
        document_summary = self._document_summary(record, body)
        source_link = (
            f'<a class="source-link" href="{html.escape(source_url, quote=True)}" target="_blank" rel="noopener">Open original source</a>'
            if source_url
            else ""
        )
        metadata_items = [
            ("Source", source_label),
            ("Type", source_type),
            ("Available at", available_at),
            ("Published at", published_at),
            ("Tickers", tickers),
            ("Tags", tags),
        ]
        metadata_html = "\n".join(
            f"""
            <div class="meta-item">
              <span>{html.escape(label)}</span>
              <strong>{html.escape(value or "unknown")}</strong>
            </div>
            """
            for label, value in metadata_items
            if value or label in {"Source", "Type", "Available at"}
        )
        facts_html = "\n".join(
            f"<li>{html.escape(fact)}</li>"
            for fact in (document_summary.get("facts") or [])[:6]
            if str(fact).strip()
        )
        numbers_html = "\n".join(
            f"<span>{html.escape(number)}</span>"
            for number in (document_summary.get("numbers") or [])[:8]
            if str(number).strip()
        )
        summary_mode = "LLM summary" if document_summary.get("mode") == "llm" else "Local structured summary"
        summary_model = _clean_text(document_summary.get("model") or "", limit=80)
        summary_status = f"{summary_mode}{' - ' + summary_model if summary_model else ''}"
        llm_error_html = (
            f'<p class="summary-error">LLM fallback used: {html.escape(document_summary.get("llm_error", ""))}</p>'
            if document_summary.get("llm_error")
            else ""
        )
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --bg: #08111f;
      --paper: rgba(13, 24, 41, 0.88);
      --panel: rgba(16, 29, 48, 0.84);
      --line: rgba(139, 154, 178, 0.22);
      --text: #dbe5f2;
      --muted: #93a4ba;
      --accent: #7aa2d6;
      --accent-soft: rgba(122, 162, 214, 0.18);
      --shadow: 0 18px 54px rgba(0, 0, 0, 0.30);
    }}
    * {{ box-sizing: border-box; }}
    html {{
      min-height: 100%;
      background: var(--bg);
    }}
    body {{
      margin: 0;
      min-height: 100vh;
      background:
        linear-gradient(180deg, rgba(8, 17, 31, 0.36), rgba(8, 17, 31, 0.72)),
        var(--bg);
      color: var(--text);
      font-family: Inter, "Segoe UI", Arial, sans-serif;
      font-size: 15px;
      -webkit-font-smoothing: antialiased;
      text-rendering: optimizeLegibility;
    }}
    body::before {{
      content: "";
      position: fixed;
      inset: -22vmax;
      z-index: 0;
      pointer-events: none;
      background:
        conic-gradient(from 140deg at 50% 42%,
          rgba(91, 127, 178, 0),
          rgba(91, 127, 178, 0.12),
          rgba(113, 105, 160, 0.08),
          rgba(88, 150, 148, 0.08),
          rgba(91, 127, 178, 0));
      filter: blur(42px);
      opacity: 0.34;
      animation: galaxyVeil 96s linear infinite;
    }}
    .cosmic-backdrop {{
      position: fixed;
      inset: 0;
      z-index: 0;
      width: 100vw;
      height: 100vh;
      pointer-events: none;
      opacity: 0.88;
      background: radial-gradient(ellipse at 50% 42%, #172a44 0%, #0b1728 43%, #08111f 100%);
    }}
    .document-shell {{
      position: relative;
      z-index: 1;
      width: min(1120px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 28px 0 44px;
    }}
    .topline {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      margin-bottom: 16px;
      border: 1px solid rgba(139, 154, 178, 0.20);
      border-radius: 18px;
      background: linear-gradient(180deg, rgba(16, 29, 48, 0.84), rgba(11, 22, 38, 0.78));
      box-shadow: var(--shadow);
      backdrop-filter: blur(18px);
      padding: 14px 16px;
    }}
    .back-link,
    .source-link {{
      color: var(--accent);
      font-weight: 600;
      text-decoration: none;
    }}
    .back-link:hover,
    .source-link:hover {{
      color: #dce8f5;
    }}
    .source-link {{
      border: 1px solid rgba(139, 154, 178, 0.24);
      border-radius: 10px;
      background: rgba(11, 21, 36, 0.72);
      padding: 10px 13px;
      white-space: nowrap;
    }}
    h1 {{
      color: #c7d4e3;
      font-size: clamp(25px, 3vw, 40px);
      font-weight: 650;
      line-height: 1.12;
      margin: 0 0 18px;
    }}
    .metadata {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 10px;
      margin-bottom: 18px;
    }}
    .meta-item {{
      border: 1px solid rgba(139, 154, 178, 0.20);
      border-radius: 12px;
      background: linear-gradient(180deg, rgba(16, 29, 48, 0.84), rgba(11, 22, 38, 0.78));
      padding: 10px 12px;
      min-width: 0;
      box-shadow: 0 14px 34px rgba(0, 0, 0, 0.18);
    }}
    .meta-item span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      font-weight: 500;
      margin-bottom: 4px;
    }}
    .meta-item strong {{
      display: block;
      font-size: 13px;
      font-weight: 600;
      color: #cbd7e6;
      overflow-wrap: anywhere;
    }}
    .document-body {{
      border: 1px solid rgba(139, 154, 178, 0.20);
      border-radius: 18px;
      background: linear-gradient(180deg, rgba(13, 24, 41, 0.88), rgba(10, 20, 35, 0.74));
      padding: clamp(16px, 2vw, 26px);
      box-shadow: var(--shadow);
      backdrop-filter: blur(16px);
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      line-height: 1.62;
      font-size: 14px;
      color: #c8d4e1;
    }}
    .document-brief {{
      border: 1px solid rgba(130, 169, 209, 0.22);
      border-radius: 18px;
      background:
        radial-gradient(circle at 12% 0%, rgba(122, 162, 214, 0.16), transparent 34%),
        linear-gradient(180deg, rgba(15, 31, 52, 0.92), rgba(10, 21, 37, 0.82));
      box-shadow: var(--shadow);
      backdrop-filter: blur(16px);
      padding: clamp(16px, 2vw, 26px);
      margin-bottom: 18px;
    }}
    .brief-kicker {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      margin-bottom: 10px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}
    .brief-kicker span {{
      border: 1px solid rgba(139, 154, 178, 0.22);
      border-radius: 999px;
      background: rgba(8, 17, 31, 0.42);
      padding: 5px 9px;
    }}
    .document-brief h2 {{
      margin: 0 0 12px;
      color: #dbe6f2;
      font-size: clamp(22px, 2.4vw, 34px);
      line-height: 1.12;
      font-weight: 700;
    }}
    .brief-summary {{
      margin: 0 0 16px;
      color: #c5d3e2;
      font-size: 15px;
      line-height: 1.62;
      max-width: 88ch;
    }}
    .brief-grid {{
      display: grid;
      grid-template-columns: minmax(0, 1.45fr) minmax(220px, 0.8fr);
      gap: 14px;
      align-items: start;
    }}
    .brief-facts,
    .brief-numbers {{
      border: 1px solid rgba(139, 154, 178, 0.16);
      border-radius: 14px;
      background: rgba(8, 17, 31, 0.34);
      padding: 13px 14px;
    }}
    .brief-facts strong,
    .brief-numbers strong {{
      display: block;
      margin-bottom: 8px;
      color: #d7e3f0;
      font-size: 13px;
    }}
    .brief-facts ul {{
      margin: 0;
      padding-left: 18px;
      color: #c8d5e3;
      line-height: 1.55;
    }}
    .brief-facts li + li {{
      margin-top: 7px;
    }}
    .brief-numbers div {{
      display: flex;
      flex-wrap: wrap;
      gap: 7px;
    }}
    .brief-numbers span {{
      border: 1px solid rgba(127, 182, 157, 0.22);
      border-radius: 999px;
      background: rgba(127, 182, 157, 0.10);
      color: #cfe0d9;
      padding: 6px 9px;
      font-size: 12px;
      line-height: 1.3;
    }}
    .summary-error {{
      margin: 12px 0 0;
      color: #b8c7d8;
      font-size: 12px;
    }}
    .raw-document-details {{
      margin-top: 18px;
    }}
    .raw-document-details > summary {{
      cursor: pointer;
      color: var(--accent);
      font-weight: 650;
      margin-bottom: 10px;
    }}
    .raw-document-details > summary::-webkit-details-marker {{
      display: none;
    }}
    .raw-document-details > summary::before {{
      content: "+";
      display: inline-block;
      width: 20px;
      color: #9bbbe1;
    }}
    .raw-document-details[open] > summary::before {{
      content: "-";
    }}
    @keyframes galaxyVeil {{
      from {{ transform: rotate(0deg); }}
      to {{ transform: rotate(360deg); }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      body::before {{ animation: none; }}
    }}
    @media (max-width: 700px) {{
      .topline {{ align-items: flex-start; flex-direction: column; }}
      .source-link {{ white-space: normal; }}
      .brief-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <canvas id="cosmicBackdrop" class="cosmic-backdrop" aria-hidden="true"></canvas>
  <main class="document-shell">
    <div class="topline">
      <a class="back-link" href="/">Back to search</a>
      {source_link}
    </div>
    <h1>{html.escape(title)}</h1>
    <section class="metadata" aria-label="Document metadata">
      {metadata_html}
    </section>
    <section class="document-brief" aria-label="Document summary">
      <div class="brief-kicker">
        <span>Document Brief</span>
        <span>{html.escape(summary_status)}</span>
      </div>
      <h2>{html.escape(document_summary.get("headline") or "Document summary")}</h2>
      <p class="brief-summary">{html.escape(document_summary.get("summary") or "")}</p>
      <div class="brief-grid">
        <div class="brief-facts">
          <strong>Facts that matter</strong>
          <ul>{facts_html or "<li>No compact facts extracted.</li>"}</ul>
        </div>
        <div class="brief-numbers">
          <strong>Numbers pulled out</strong>
          <div>{numbers_html or "<span>No clear numeric evidence</span>"}</div>
        </div>
      </div>
      {llm_error_html}
    </section>
    <details class="raw-document-details">
      <summary>Original cached text</summary>
      <article class="document-body">{html.escape(body)}</article>
    </details>
  </main>
  <script>
    (() => {{
      const canvas = document.getElementById("cosmicBackdrop");
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      let width = 0;
      let height = 0;
      let stars = [];
      let raf = 0;
      const rand = (min, max) => min + Math.random() * (max - min);
      const resize = () => {{
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        width = window.innerWidth;
        height = window.innerHeight;
        canvas.width = Math.floor(width * dpr);
        canvas.height = Math.floor(height * dpr);
        canvas.style.width = `${{width}}px`;
        canvas.style.height = `${{height}}px`;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        const count = Math.min(260, Math.max(90, Math.floor((width * height) / 9000)));
        stars = Array.from({{ length: count }}, (_, i) => {{
          const arm = i % 4;
          const radius = Math.pow(Math.random(), 0.62) * Math.min(width, height) * 0.42;
          const angle = arm * Math.PI / 2 + radius * 0.012 + rand(-0.55, 0.55);
          return {{
            x: width * 0.5 + Math.cos(angle) * radius,
            y: height * 0.42 + Math.sin(angle) * radius * 0.42,
            r: rand(0.45, 1.7),
            a: rand(0.12, 0.54),
            p: rand(0, Math.PI * 2),
            hue: rand(205, 220),
          }};
        }});
      }};
      const draw = (time) => {{
        ctx.clearRect(0, 0, width, height);
        ctx.save();
        ctx.translate(width * 0.5, height * 0.42);
        ctx.rotate(time * 0.000018);
        ctx.translate(-width * 0.5, -height * 0.42);
        for (const star of stars) {{
          const alpha = Math.max(0.05, star.a + Math.sin(time * 0.001 + star.p) * 0.12);
          ctx.beginPath();
          ctx.fillStyle = `hsla(${{star.hue}}, 70%, 80%, ${{alpha}})`;
          ctx.arc(star.x, star.y, star.r, 0, Math.PI * 2);
          ctx.fill();
        }}
        ctx.restore();
        raf = window.requestAnimationFrame(draw);
      }};
      resize();
      window.addEventListener("resize", resize, {{ passive: true }});
      if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {{
        raf = window.requestAnimationFrame(draw);
      }} else {{
        draw(0);
        window.cancelAnimationFrame(raf);
      }}
    }})();
  </script>
</body>
</html>
"""

    def _number_from_text(self, value: str) -> float | None:
        text = str(value or "").strip()
        if not text:
            return None
        negative = text.startswith("(") and text.endswith(")")
        text = text.strip("()").replace("$", "").replace(",", "").replace("%", "").strip()
        try:
            number = float(text)
        except ValueError:
            return None
        return -number if negative else number

    def _metric_pair(self, text: str, labels: tuple[str, ...]) -> tuple[float, float] | None:
        normalized = re.sub(r"\s+", " ", text)
        for label in labels:
            pattern = rf"(?:^|[^A-Za-z]){re.escape(label)}\s*:?\s+\$?\s*(\(?-?\d[\d,]*(?:\.\d+)?\)?)\s+\$?\s*(\(?-?\d[\d,]*(?:\.\d+)?\)?)"
            match = re.search(pattern, normalized, flags=re.IGNORECASE)
            if not match:
                continue
            current = self._number_from_text(match.group(1))
            prior = self._number_from_text(match.group(2))
            if current is not None and prior is not None:
                return current, prior
        return None

    def _section_between(self, text: str, start: str, end: str) -> str:
        normalized = re.sub(r"\s+", " ", text)
        start_index = normalized.lower().find(start.lower())
        if start_index < 0:
            return ""
        end_index = normalized.lower().find(end.lower(), start_index + len(start))
        if end_index < 0:
            return normalized[start_index:]
        return normalized[start_index:end_index]

    def _delta_pct(self, current: float | None, prior: float | None) -> float | None:
        if current is None or prior in {None, 0}:
            return None
        return round((current - prior) / abs(prior) * 100.0, 2)

    def _tone_from_delta(self, delta: float | None, inverse: bool = False) -> str:
        if delta is None or abs(delta) < 1.0:
            return "neutral"
        positive = delta > 0
        if inverse:
            positive = not positive
        return "positive" if positive else "negative"

    def _format_metric_value(self, value: float | None, unit: str = "USD millions") -> str:
        if value is None:
            return "n/a"
        if unit == "USD/share":
            return f"${value:,.2f}"
        if unit == "%":
            return f"{value:.1f}%"
        if unit == "USD millions":
            if abs(value) >= 1000:
                return f"${value / 1000:,.1f}B"
            return f"${value:,.0f}M"
        return f"{value:,.2f}"

    def _format_delta(self, delta: float | None) -> str:
        if delta is None:
            return "n/a"
        sign = "+" if delta > 0 else ""
        return f"{sign}{delta:.1f}%"

    def _financial_candidate_records(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        def priority(record: dict[str, Any]) -> tuple[int, str]:
            text = str(record.get("body") or record.get("text") or "")
            title = str(record.get("title", "") or "").lower()
            source_type = str(record.get("source_type", "") or "").lower()
            score = 0
            if "exhibit" in source_type or "earnings release" in title:
                score += 5
            if "financial statements" in title or "item 1 financial statements" in title:
                score += 5
            if "management" in title or "mda" in title:
                score += 3
            if re.search(r"\b(total net sales|total revenue|net income|earnings per share|gross margin)\b", text, re.IGNORECASE):
                score += 5
            return score, str(record.get("available_at") or "")

        candidates = [
            record for record in records
            if priority(record)[0] >= 5 and str(record.get("body") or record.get("text") or "")
        ]
        return sorted(candidates, key=priority, reverse=True)

    def _financial_snapshot_from_record(self, record: dict[str, Any]) -> dict[str, Any] | None:
        body = str(record.get("body") or record.get("text") or "")
        if not body:
            return None
        revenue = self._metric_pair(body, ("Total net sales", "Total revenue", "Total revenues", "Net sales", "Revenue"))
        if revenue is None:
            return None
        gross_profit = self._metric_pair(body, ("Gross margin", "Gross profit"))
        operating_income = self._metric_pair(body, ("Operating income", "Income from operations"))
        net_income = self._metric_pair(body, ("Net income", "Net earnings"))
        eps_section = self._section_between(body, "Earnings per share", "Shares used")
        eps = self._metric_pair(eps_section or body, ("Diluted", "Diluted earnings per share", "Diluted EPS"))
        net_sales_section = self._section_between(body, "Net sales", "Cost of sales")
        products = self._metric_pair(net_sales_section, ("Products",))
        services = self._metric_pair(net_sales_section, ("Services",))
        dividends = self._metric_pair(body, ("Payments for dividends and dividend equivalents", "Dividends and dividend equivalents paid"))
        repurchases = self._metric_pair(body, ("Repurchases of common stock", "Payments for repurchase of common stock", "Share repurchases"))

        def row(label: str, pair: tuple[float, float] | None, unit: str = "USD millions", inverse: bool = False) -> dict[str, Any] | None:
            if pair is None:
                return None
            current, prior = pair
            delta = self._delta_pct(current, prior)
            return {
                "metric": label,
                "current": current,
                "prior": prior,
                "current_label": self._format_metric_value(current, unit),
                "prior_label": self._format_metric_value(prior, unit),
                "change_pct": delta,
                "change_label": self._format_delta(delta),
                "tone": self._tone_from_delta(delta, inverse=inverse),
                "unit": unit,
            }

        rows = [
            item for item in [
                row("Revenue", revenue),
                row("Gross profit", gross_profit),
                row("Operating income", operating_income),
                row("Net income", net_income),
                row("Diluted EPS", eps, unit="USD/share"),
            ]
            if item is not None
        ]
        if not rows:
            return None

        revenue_current = revenue[0]
        gross_margin = round(gross_profit[0] / revenue_current * 100.0, 2) if gross_profit and revenue_current else None
        operating_margin = round(operating_income[0] / revenue_current * 100.0, 2) if operating_income and revenue_current else None
        net_margin = round(net_income[0] / revenue_current * 100.0, 2) if net_income and revenue_current else None
        mix_segments = []
        if products and services:
            total = products[0] + services[0]
            if total:
                mix_segments = [
                    {"label": "Products", "value": products[0], "share": round(products[0] / total * 100.0, 1), "value_label": self._format_metric_value(products[0])},
                    {"label": "Services", "value": services[0], "share": round(services[0] / total * 100.0, 1), "value_label": self._format_metric_value(services[0])},
                ]

        capital_rows = [
            item for item in [
                row("Dividends paid", dividends, inverse=True),
                row("Share repurchases", repurchases, inverse=True),
            ]
            if item is not None
        ]
        return {
            "record": record,
            "rows": rows,
            "mix_segments": mix_segments,
            "margins": [
                item for item in [
                    {"label": "Gross", "value": gross_margin, "value_label": self._format_metric_value(gross_margin, "%")},
                    {"label": "Operating", "value": operating_margin, "value_label": self._format_metric_value(operating_margin, "%")},
                    {"label": "Net", "value": net_margin, "value_label": self._format_metric_value(net_margin, "%")},
                ]
                if item.get("value") is not None
            ],
            "capital_rows": capital_rows,
        }

    def _analyst_headline_for_folder(self, folder_key: str, snapshot: dict[str, Any]) -> str:
        rows = snapshot.get("rows") or []
        record = snapshot.get("record") or {}
        tickers = [str(ticker).upper() for ticker in (record.get("matched_tickers") or []) if str(ticker)]
        subject = tickers[0] if tickers else "The filing"

        def pick(options: tuple[str, ...], extra: str = "") -> str:
            digest = hashlib.sha256(f"{subject}:{extra}".encode("utf-8")).hexdigest()
            return options[int(digest[:8], 16) % len(options)]

        def metric(name: str) -> dict[str, Any] | None:
            return next((row for row in rows if str(row.get("metric", "")).lower() == name.lower()), None)

        def delta(name: str) -> float | None:
            row = metric(name)
            value = row.get("change_pct") if row else None
            return float(value) if isinstance(value, (int, float)) else None

        def change(name: str) -> str:
            row = metric(name)
            return str(row.get("change_label") or "changed") if row else "changed"

        revenue_delta = delta("Revenue")
        eps_delta = delta("Diluted EPS")
        net_delta = delta("Net income")
        operating_delta = delta("Operating income")
        gross_delta = delta("Gross profit")
        has_capital_return = bool(snapshot.get("capital_rows"))

        if revenue_delta is not None and net_delta is not None:
            if revenue_delta > 5 and net_delta < -5:
                return pick(
                    (
                        f"{subject}: sales grew, profit asked for a lawyer",
                        f"{subject}: revenue smiled, net income checked the exits",
                        f"{subject}: the top line flexed, profit flinched",
                    ),
                    "revenue-up-profit-down",
                )
            if revenue_delta < -5 and net_delta > 5:
                return pick(
                    (
                        f"{subject}: sales slipped, profit held the umbrella",
                        f"{subject}: revenue coughed, net income kept walking",
                        f"{subject}: the top line sagged, profit refused to panic",
                    ),
                    "revenue-down-profit-up",
                )
        if revenue_delta is not None and eps_delta is not None:
            if revenue_delta > 5 and eps_delta < -5:
                return pick(
                    (
                        f"{subject}: sales rose, EPS found the stairs down",
                        f"{subject}: revenue climbed, EPS missed the memo",
                        f"{subject}: top-line cheer, per-share cold shower",
                    ),
                    "revenue-up-eps-down",
                )
            if revenue_delta < -5 and eps_delta > 5:
                return pick(
                    (
                        f"{subject}: revenue coughed, EPS still showed up",
                        f"{subject}: sales softened, EPS wore a helmet",
                        f"{subject}: the top line dipped, per-share math behaved",
                    ),
                    "revenue-down-eps-up",
                )
        if operating_delta is not None and operating_delta < -8:
            return pick(
                (
                    f"{subject}: operating profit {change('Operating income')}, margins filed a complaint",
                    f"{subject}: revenue talks, operating margin answers quietly",
                    f"{subject}: the income statement found a margin dent",
                    f"{subject}: operating profit slipped on the cost floor",
                    f"{subject}: margin pressure knocked; operating profit opened",
                ),
                "operating-down",
            )
        if gross_delta is not None and gross_delta < -8:
            return pick(
                (
                    f"{subject}: gross profit blinked first",
                    f"{subject}: the gross line brought a yellow flag",
                    f"{subject}: sales showed up, gross profit looked tired",
                ),
                "gross-down",
            )
        if has_capital_return and eps_delta is not None and eps_delta > 5:
            return pick(
                (
                    f"{subject}: buybacks, payouts, and EPS arrived together",
                    f"{subject}: capital returns did not come empty-handed",
                    f"{subject}: EPS brought cash returns to the party",
                ),
                "capital-return",
            )
        if net_delta is not None and net_delta > 8:
            return pick(
                (
                    f"{subject}: profit did its job this time",
                    f"{subject}: net income brought receipts",
                    f"{subject}: the bottom line earned its headline",
                ),
                "net-up",
            )
        if folder_key == "company_ir":
            return pick(
                (
                    f"{subject}: the press release smiles; numbers decide",
                    f"{subject}: IR brought the story, filings bring the bill",
                    f"{subject}: the headline is friendly, the table is stricter",
                ),
                "company-ir",
            )
        return pick(
            (
                f"{subject}: the filing talks; the numbers testify",
                f"{subject}: tables first, adjectives later",
                f"{subject}: the footnotes brought the flashlight",
            ),
            "default",
        )

    def _analyst_view_for_folder(self, folder_key: str, records: list[dict[str, Any]]) -> dict[str, Any]:
        if folder_key not in {"sec_filings", "company_ir"}:
            return {"available": False}
        candidates = self._financial_candidate_records(records)
        snapshot = None
        for record in candidates:
            snapshot = self._financial_snapshot_from_record(record)
            if snapshot:
                break
        if not snapshot:
            return {
                "available": False,
                "reason": "No usable financial statement or earnings-release table was found in the latest folder window.",
            }
        record = snapshot["record"]
        rows = snapshot["rows"]
        cards = []
        for item in rows[:4]:
            cards.append(
                {
                    "label": item["metric"],
                    "value": item["current_label"],
                    "delta": item["change_label"],
                    "tone": item["tone"],
                }
            )
        charts = [
            {
                "type": "compare_bars",
                "title": "Income Statement",
                "subtitle": "Current period vs prior comparable period",
                "rows": rows[:5],
            }
        ]
        if snapshot["mix_segments"]:
            charts.append(
                {
                    "type": "mix_bar",
                    "title": "Revenue Mix",
                    "subtitle": "Current period composition",
                    "segments": snapshot["mix_segments"],
                }
            )
        if snapshot["margins"]:
            charts.append(
                {
                    "type": "margin_bars",
                    "title": "Margins",
                    "subtitle": "Profitability as share of revenue",
                    "rows": snapshot["margins"],
                }
            )
        if snapshot["capital_rows"]:
            charts.append(
                {
                    "type": "compare_bars",
                    "title": "Capital Return",
                    "subtitle": "Cash returned to shareholders",
                    "rows": snapshot["capital_rows"],
                }
            )
        drivers = []
        revenue_row = next((item for item in rows if item["metric"] == "Revenue"), None)
        eps_row = next((item for item in rows if item["metric"] == "Diluted EPS"), None)
        net_row = next((item for item in rows if item["metric"] == "Net income"), None)
        if revenue_row:
            drivers.append({"label": "Sales", "tone": revenue_row["tone"], "text": f"Revenue changed {revenue_row['change_label']} versus the comparable period."})
        if eps_row:
            drivers.append({"label": "EPS", "tone": eps_row["tone"], "text": f"Diluted EPS changed {eps_row['change_label']}; this is closer to shareholder economics than document counts."})
        if net_row:
            drivers.append({"label": "Profit", "tone": net_row["tone"], "text": f"Net income changed {net_row['change_label']}; verify whether margin or one-off items explain it."})
        return {
            "available": True,
            "source": "cached_financial_statement",
            "title": self._analyst_headline_for_folder(folder_key, snapshot),
            "subtitle": "Latest usable filing table in this folder",
            "source_document": {
                "title": record.get("title", ""),
                "available_at": record.get("available_at", ""),
                "source_type": record.get("source_type", ""),
                "url": record.get("canonical_url") or record.get("url") or "",
            },
            "metric_cards": cards,
            "tables": [
                {
                    "title": "Key numbers",
                    "columns": ["Metric", "Current", "Prior", "Change"],
                    "rows": [
                        [item["metric"], item["current_label"], item["prior_label"], item["change_label"], item["tone"]]
                        for item in rows
                    ],
                }
            ],
            "charts": charts[:3],
            "drivers": drivers[:3],
        }

    def _folder_chart_pack(self, folder_key: str, docs: list[dict[str, Any]]) -> dict[str, Any]:
        buckets = self._folder_month_buckets(docs)
        if not buckets:
            return {"source": "local_text_features", "charts": []}

        def chart(title: str, subtitle: str, series: list[dict[str, Any]]) -> dict[str, Any]:
            def informative(item: dict[str, Any]) -> bool:
                values = [abs(float(point.get("value", 0.0) or 0.0)) for point in item.get("points", [])]
                if not values:
                    return False
                unit = str(item.get("unit", ""))
                if unit in {"docs", "terms"}:
                    return max(values) > 0.0
                return max(values) >= 0.05 or (max(values) - min(values)) >= 0.03

            cleaned = [item for item in series if item.get("points") and informative(item)]
            if len(cleaned) < 2:
                docs_series = self._folder_series(buckets, label="Documents", unit="docs", metric="count")
                if informative(docs_series) and not any(item.get("label") == "Documents" for item in cleaned):
                    cleaned.insert(0, docs_series)
            latest = [
                (str(item.get("label", "Series")), float((item.get("points") or [{}])[-1].get("value", 0.0) or 0.0))
                for item in cleaned
            ]
            leader = max(latest, key=lambda item: abs(item[1])) if latest else ("No signal", 0.0)
            note = (
                "No strong detected signal in the latest month."
                if abs(leader[1]) < 0.05
                else f"Latest focus: {leader[0]} {leader[1]:.2f}."
            )
            return {
                "title": title,
                "subtitle": subtitle,
                "note": note,
                "series": cleaned[:3],
            }

        if folder_key == "sec_filings":
            charts = [
                chart(
                    "Filing Activity",
                    "Fresh evidence by filing type",
                    [
                        self._folder_series(buckets, label="All docs", unit="docs", metric="count"),
                        self._folder_series(buckets, label="10-K / 10-Q", unit="docs", metric="count", concepts=("10_k", "10_q")),
                        self._folder_series(buckets, label="8-K / Exhibit", unit="docs", metric="count", concepts=("8_k", "exhibit")),
                    ],
                ),
                chart(
                    "Signal Heat",
                    "Only non-zero text signals are shown",
                    [
                        self._folder_series(buckets, label="Signal docs", unit="docs", metric="feature_count:calibrated_signal_score:0.05"),
                        self._folder_series(buckets, label="Risk docs", unit="docs", metric="feature_count:risk_alert_score:0.05"),
                        self._folder_series(buckets, label="Upside docs", unit="docs", metric="feature_count:upside_signal_score:0.05"),
                    ],
                ),
                chart(
                    "Event Themes",
                    "Actionable themes detected in filings",
                    [
                        self._folder_series(buckets, label="Guidance", unit="docs", metric="count", concepts=("earnings_guidance", "guidance", "earnings")),
                        self._folder_series(buckets, label="Legal", unit="docs", metric="count", concepts=("legal_regulatory", "lawsuit", "regulatory")),
                        self._folder_series(buckets, label="Capital return", unit="docs", metric="count", concepts=("capital_return", "dividend", "buyback")),
                    ],
                ),
            ]
        elif folder_key == "company_ir":
            charts = [
                chart(
                    "Revenue & Guidance Momentum",
                    "Company-language support for growth and guidance",
                    [
                        self._folder_series(buckets, label="Guidance docs", unit="docs", metric="count", concepts=("earnings_guidance", "guidance", "revenue")),
                        self._folder_series(buckets, label="Sentiment", unit="score", metric="sentiment_proxy"),
                        self._folder_series(buckets, label="Upside score", unit="score", metric="upside_signal_score"),
                    ],
                ),
                chart(
                    "Margin Pressure",
                    "Cost, inflation, supply chain and operating pressure",
                    [
                        self._folder_series(buckets, label="Margin docs", unit="docs", metric="count", concepts=("margin_pressure", "margin", "cost")),
                        self._folder_series(buckets, label="Risk score", unit="score", metric="risk_alert_score"),
                        self._folder_series(buckets, label="Uncertainty", unit="score", metric="uncertainty_intensity"),
                    ],
                ),
                chart(
                    "Capital Return & Events",
                    "Buybacks, dividends, restructuring and corporate events",
                    [
                        self._folder_series(buckets, label="Capital return", unit="docs", metric="count", concepts=("capital_return", "dividend", "buyback")),
                        self._folder_series(buckets, label="M&A docs", unit="docs", metric="count", concepts=("mna", "merger", "acquisition")),
                        self._folder_series(buckets, label="Event severity", unit="score", metric="event_severity_score"),
                    ],
                ),
            ]
        elif folder_key == "macro":
            charts = [
                chart(
                    "Rates & Yield Curve",
                    "Fed/rates pressure affecting valuation and risk appetite",
                    [
                        self._folder_series(buckets, label="Rates docs", unit="docs", metric="count", concepts=("macro_rates", "rates_policy", "yield")),
                        self._folder_series(buckets, label="Risk score", unit="score", metric="risk_alert_score"),
                        self._folder_series(buckets, label="Signal score", unit="score", metric="calibrated_signal_score"),
                    ],
                ),
                chart(
                    "Credit & Vol Stress",
                    "Credit, spreads and volatility evidence for portfolio risk",
                    [
                        self._folder_series(buckets, label="Credit docs", unit="docs", metric="count", concepts=("credit", "spread")),
                        self._folder_series(buckets, label="Vol docs", unit="docs", metric="count", concepts=("market_volatility", "vix", "volatility")),
                        self._folder_series(buckets, label="Uncertainty", unit="score", metric="uncertainty_intensity"),
                    ],
                ),
                chart(
                    "Growth / Demand Pulse",
                    "Demand, labor, inflation and energy evidence",
                    [
                        self._folder_series(buckets, label="Demand docs", unit="docs", metric="count", concepts=("consumer_demand", "labor_growth", "demand")),
                        self._folder_series(buckets, label="Inflation docs", unit="docs", metric="count", concepts=("inflation", "energy")),
                        self._folder_series(buckets, label="Sentiment", unit="score", metric="sentiment_proxy"),
                    ],
                ),
            ]
        else:
            charts = [
                chart(
                    "Signal / Risk / Upside",
                    "Core text feature balance by month",
                    [
                        self._folder_series(buckets, label="Signal", unit="score", metric="calibrated_signal_score"),
                        self._folder_series(buckets, label="Risk", unit="score", metric="risk_alert_score"),
                        self._folder_series(buckets, label="Upside", unit="score", metric="upside_signal_score"),
                    ],
                ),
                chart(
                    "Event Density",
                    "How much evidence appeared in each month",
                    [
                        self._folder_series(buckets, label="Documents", unit="docs", metric="count"),
                        self._folder_series(buckets, label="Risk terms", unit="terms", metric="risk_terms"),
                    ],
                ),
                chart(
                    "Source Quality",
                    "Credibility and extraction readiness proxy",
                    [
                        self._folder_series(buckets, label="Source tier", unit="score", metric="source_quality"),
                        self._folder_series(buckets, label="Signal", unit="score", metric="calibrated_signal_score"),
                    ],
                ),
            ]
        return {"source": "local_text_features", "charts": charts[:3]}

    def _extract_llm_chart_pack(self, text: str) -> dict[str, Any] | None:
        cleaned = str(text or "").strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
            cleaned = re.sub(r"```$", "", cleaned).strip()
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
        chart_pack = parsed.get("chart_pack") if isinstance(parsed, dict) else None
        if not isinstance(chart_pack, dict):
            chart_pack = parsed if isinstance(parsed, dict) else None
        if not isinstance(chart_pack, dict):
            return None
        charts = chart_pack.get("charts")
        if not isinstance(charts, list):
            return None
        safe_charts: list[dict[str, Any]] = []
        for chart in charts[:3]:
            if not isinstance(chart, dict):
                continue
            title = _clean_text(chart.get("title"), limit=80) or "LLM chart"
            subtitle = _clean_text(chart.get("subtitle"), limit=120)
            safe_series: list[dict[str, Any]] = []
            for series in chart.get("series", [])[:3] if isinstance(chart.get("series"), list) else []:
                if not isinstance(series, dict):
                    continue
                points = []
                for point in series.get("points", [])[:24] if isinstance(series.get("points"), list) else []:
                    if not isinstance(point, dict):
                        continue
                    date = str(point.get("date", ""))[:10]
                    value = _float_value(point.get("value"), math.nan)
                    if date and not math.isnan(value):
                        points.append({"date": date, "value": round(value, 6)})
                if len(points) >= 2:
                    safe_series.append(
                        {
                            "label": _clean_text(series.get("label"), limit=50) or "Series",
                            "unit": _clean_text(series.get("unit"), limit=20),
                            "points": points,
                        }
                    )
            if safe_series:
                safe_charts.append({"title": title, "subtitle": subtitle, "series": safe_series})
        return {"source": "llm", "charts": safe_charts} if safe_charts else None

    def _folder_analysis_window(self, payload: dict[str, Any]) -> dict[str, Any]:
        requested = str(payload.get("window") or payload.get("time_window") or "1y").strip().lower()
        aliases = {
            "year": "1y",
            "latest_year": "1y",
            "last_year": "1y",
            "one_year": "1y",
            "5_years": "5y",
            "five_years": "5y",
            "all_time": "all",
            "everything": "all",
        }
        key = aliases.get(requested, requested)
        if key not in {"1y", "5y", "all"}:
            key = "1y"
        if key == "5y":
            return {"key": key, "label": "5Y", "days": 365 * 5 + 2, "max_docs": FOLDER_ANALYSIS_MAX_DOCS_5Y}
        if key == "all":
            return {"key": key, "label": "All time", "days": None, "max_docs": FOLDER_ANALYSIS_MAX_DOCS_ALL}
        return {"key": "1y", "label": "1Y", "days": 365, "max_docs": FOLDER_ANALYSIS_MAX_DOCS_1Y}

    def _folder_analysis_cache_version(self) -> str:
        parts = []
        for label, path in {
            "documents": self.documents_path,
            "text_features": self.text_features_path,
            "search_index": self.search_index_path,
        }.items():
            try:
                stat = path.stat()
            except OSError:
                parts.append(f"{label}:missing")
            else:
                parts.append(f"{label}:{stat.st_mtime_ns}:{stat.st_size}")
        return "|".join(parts)

    def _folder_analysis_query_identity(self, query: str) -> str:
        tickers = sorted(str(ticker).upper() for ticker in self._query_entity_tickers(query) if ticker)
        if tickers:
            return "tickers:" + "|".join(dict.fromkeys(tickers))
        words = _entity_words(query, stopwords=SEARCH_STOPWORDS)
        if words:
            return "query:" + " ".join(words[:12])
        return "query:" + re.sub(r"\s+", " ", str(query or "").strip().lower())[:160]

    def _folder_analysis_cache_key(self, query_identity: str, folder_key: str, window_key: str) -> str:
        digest = hashlib.sha1(str(query_identity or "").encode("utf-8")).hexdigest()[:16]
        return f"{self._folder_analysis_cache_version()}:{folder_key}:{window_key}:{digest}"

    def _copy_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return json.loads(json.dumps(payload, ensure_ascii=False))

    def _folder_analysis_llm_allowed(self, payload: dict[str, Any]) -> bool:
        value = payload.get("force_llm") or payload.get("llm_folder_analysis")
        if isinstance(value, str):
            value = value.strip().lower() in {"1", "true", "yes", "on"}
        return FOLDER_ANALYSIS_LLM_ENABLED or bool(value)

    def _direct_folder_analysis_docs(self, query: str, folder_key: str) -> list[dict[str, Any]]:
        query_tickers = self._query_entity_tickers(query)
        feature_lookup = self.text_features()
        rows: list[dict[str, Any]] = []
        for record in self.documents():
            if not self._is_historical_search_row(record):
                continue
            if query_tickers and not self._record_matches_query_entity(record, query_tickers):
                continue
            row_folder_key, _title, _summary = self._folder_descriptor(record)
            if row_folder_key != folder_key:
                continue
            doc_id = str(record.get("doc_id", "") or "")
            text_features = feature_lookup.get(doc_id, {})
            lexical = self._search_score(record, query) if str(query or "").strip() else 0.25
            if not query_tickers and str(query or "").strip() and lexical <= 0:
                continue
            score = self._feature_aware_score(record, max(lexical, 0.25), query, text_features)
            rows.append(self._result_row(record, max(score, 0.25), text_features))
        self._sort_search_results(rows)
        return rows

    def _warm_demo_folder_analysis_cache(self) -> None:
        if FOLDER_ANALYSIS_WARMUP_DELAY_SECONDS > 0:
            time.sleep(FOLDER_ANALYSIS_WARMUP_DELAY_SECONDS)
        priority = ("AAPL", "BA", "MSFT", "JPM", "CVX")
        for query in priority:
            for folder_key in ("sec_filings", "company_ir"):
                for window_key in ("1y", "5y", "all"):
                    try:
                        self.analyze_search_folder({"query": query, "folder_key": folder_key, "window": window_key})
                    except Exception:
                        continue
                    time.sleep(0.05)
            company = next((item for item in DOW30_COMPANIES if item["ticker"] == query), None)
            if not company:
                continue
            # Prime common company-name searches too; they should resolve to the same cache key.
            for alias in (company["name"], company["name"].replace("The ", "", 1)):
                try:
                    self.analyze_search_folder({"query": alias, "folder_key": "sec_filings", "window": "1y"})
                except Exception:
                    continue

    def _extract_llm_analyst_view(self, text: str) -> dict[str, Any] | None:
        cleaned = str(text or "").strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
            cleaned = re.sub(r"```$", "", cleaned).strip()
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
        view = parsed.get("analyst_view") if isinstance(parsed, dict) else None
        if not isinstance(view, dict):
            return None

        cards = []
        for card in view.get("metric_cards", [])[:6] if isinstance(view.get("metric_cards"), list) else []:
            if not isinstance(card, dict):
                continue
            cards.append(
                {
                    "label": _clean_text(card.get("label"), limit=40),
                    "value": _clean_text(card.get("value"), limit=40),
                    "delta": _clean_text(card.get("delta"), limit=30),
                    "tone": _clean_text(card.get("tone"), limit=16) if str(card.get("tone", "")).lower() in {"positive", "neutral", "negative"} else "neutral",
                }
            )

        charts = []
        for chart in view.get("charts", [])[:3] if isinstance(view.get("charts"), list) else []:
            if not isinstance(chart, dict):
                continue
            chart_type = str(chart.get("type") or "compare_bars")
            if chart_type not in {"compare_bars", "mix_bar", "margin_bars"}:
                chart_type = "compare_bars"
            safe_chart: dict[str, Any] = {
                "type": chart_type,
                "title": _clean_text(chart.get("title"), limit=60) or "Financial chart",
                "subtitle": _clean_text(chart.get("subtitle"), limit=100),
            }
            if chart_type == "mix_bar":
                segments = []
                for segment in chart.get("segments", [])[:6] if isinstance(chart.get("segments"), list) else []:
                    if not isinstance(segment, dict):
                        continue
                    segments.append(
                        {
                            "label": _clean_text(segment.get("label"), limit=40),
                            "value": _float_value(segment.get("value"), 0.0),
                            "share": round(_float_value(segment.get("share"), 0.0), 2),
                            "value_label": _clean_text(segment.get("value_label"), limit=40),
                        }
                    )
                safe_chart["segments"] = segments
            elif chart_type == "margin_bars":
                rows = []
                for row in chart.get("rows", [])[:6] if isinstance(chart.get("rows"), list) else []:
                    if not isinstance(row, dict):
                        continue
                    rows.append(
                        {
                            "label": _clean_text(row.get("label"), limit=40),
                            "value": round(_float_value(row.get("value"), 0.0), 4),
                            "value_label": _clean_text(row.get("value_label"), limit=40),
                        }
                    )
                safe_chart["rows"] = rows
            else:
                rows = []
                for row in chart.get("rows", [])[:8] if isinstance(chart.get("rows"), list) else []:
                    if not isinstance(row, dict):
                        continue
                    tone = str(row.get("tone") or "neutral").lower()
                    rows.append(
                        {
                            "metric": _clean_text(row.get("metric"), limit=50),
                            "current": round(_float_value(row.get("current"), 0.0), 6),
                            "prior": round(_float_value(row.get("prior"), 0.0), 6),
                            "current_label": _clean_text(row.get("current_label"), limit=40),
                            "prior_label": _clean_text(row.get("prior_label"), limit=40),
                            "change_label": _clean_text(row.get("change_label"), limit=30),
                            "tone": tone if tone in {"positive", "neutral", "negative"} else "neutral",
                        }
                    )
                safe_chart["rows"] = rows
            if safe_chart.get("rows") or safe_chart.get("segments"):
                charts.append(safe_chart)

        tables = []
        for table in view.get("tables", [])[:2] if isinstance(view.get("tables"), list) else []:
            if not isinstance(table, dict):
                continue
            rows = []
            for row in table.get("rows", [])[:10] if isinstance(table.get("rows"), list) else []:
                if isinstance(row, list) and len(row) >= 4:
                    tone = str(row[4] if len(row) > 4 else "neutral").lower()
                    rows.append(
                        [
                            _clean_text(row[0], limit=50),
                            _clean_text(row[1], limit=40),
                            _clean_text(row[2], limit=40),
                            _clean_text(row[3], limit=30),
                            tone if tone in {"positive", "neutral", "negative"} else "neutral",
                        ]
                    )
            if rows:
                tables.append(
                    {
                        "title": _clean_text(table.get("title"), limit=60) or "Key numbers",
                        "columns": ["Metric", "Current", "Prior", "Change"],
                        "rows": rows,
                    }
                )
        if not cards and not charts and not tables:
            return None
        source_doc = view.get("source_document") if isinstance(view.get("source_document"), dict) else {}
        return {
            "available": True,
            "source": "llm",
            "title": _clean_text(view.get("title"), limit=120) or "Financial statement snapshot",
            "subtitle": _clean_text(view.get("subtitle"), limit=120),
            "source_document": {
                "title": _clean_text(source_doc.get("title"), limit=160),
                "available_at": _clean_text(source_doc.get("available_at"), limit=40),
                "source_type": _clean_text(source_doc.get("source_type"), limit=50),
                "url": _clean_text(source_doc.get("url"), limit=300),
            },
            "metric_cards": cards,
            "tables": tables,
            "charts": charts,
            "drivers": [],
        }

    def analyze_search_folder(self, payload: dict[str, Any]) -> dict[str, Any]:
        query = str(payload.get("query", "") or "")
        folder_key = str(payload.get("folder_key", "") or "")
        if not folder_key:
            raise ValueError("folder_key is required")
        window = self._folder_analysis_window(payload)
        query_identity = self._folder_analysis_query_identity(query)
        cache_key = self._folder_analysis_cache_key(query_identity, folder_key, str(window["key"]))
        with self._folder_analysis_lock:
            cached = self._folder_analysis_cache.get(cache_key)
            if cached is not None:
                result = self._copy_payload(cached)
                result["query"] = query
                result["query_identity"] = query_identity
                result["cache_status"] = "precomputed"
                return result
        raw_count, rows = self._search_rows(query)
        del raw_count
        rows = self._historical_search_rows(rows)
        grouped_results, grouped_lookup = self._group_search_results(rows)
        folders, folder_lookup = self._folder_search_results(grouped_results, query=query)
        folder = next((item for item in folders if item.get("folder_key") == folder_key), None)
        docs: list[dict[str, Any]] = []
        if folder is None:
            title, summary = self._folder_title_summary(folder_key)
            docs = self._direct_folder_analysis_docs(query, folder_key)
            folder = {
                "folder_key": folder_key,
                "folder_title": title,
                "folder_summary": summary,
                "folder_count": len(docs),
                "folder_document_count": len(docs),
            }
        else:
            folder_groups = folder_lookup.get(folder_key, [])
            for group in folder_groups:
                docs.extend(grouped_lookup.get(str(group.get("group_key", "")), [group]))
        self._sort_results_fresh_first(docs)
        latest_date = next((self._parse_date_prefix(doc.get("available_at")) for doc in docs if self._parse_date_prefix(doc.get("available_at"))), None)
        cutoff = latest_date - timedelta(days=int(window["days"])) if latest_date and window["days"] is not None else None
        recent_docs = [
            doc for doc in docs
            if cutoff is None or (self._parse_date_prefix(doc.get("available_at")) or latest_date) >= cutoff
        ]
        recent_docs = recent_docs[: int(window["max_docs"])]
        recent_records = self._analysis_records_for_results(recent_docs)
        risk_scores = [float((doc.get("text_signal") or {}).get("risk_alert_score", 0.0) or 0.0) for doc in recent_docs]
        upside_scores = [float((doc.get("text_signal") or {}).get("upside_signal_score", 0.0) or 0.0) for doc in recent_docs]
        signal_scores = [float((doc.get("text_signal") or {}).get("calibrated_signal_score", 0.0) or 0.0) for doc in recent_docs]
        active_signals = Counter(
            str(signal or "").removeprefix("signal_").replace("_", " ").strip().title()
            for doc in recent_docs
            for signal in ((doc.get("active_signals") or (doc.get("text_signal") or {}).get("active_signals") or []))
        )
        source_types = Counter(str(doc.get("source_type", "") or "unknown") for doc in recent_docs)
        def avg(values: list[float]) -> float:
            return round(sum(values) / max(1, len(values)), 4)
        top_docs = [
            {
                "title": doc.get("title", ""),
                "available_at": doc.get("available_at", ""),
                "source_type": doc.get("source_type", ""),
                "signal": round(float((doc.get("text_signal") or {}).get("calibrated_signal_score", 0.0) or 0.0), 4),
                "risk": round(float((doc.get("text_signal") or {}).get("risk_alert_score", 0.0) or 0.0), 4),
                "upside": round(float((doc.get("text_signal") or {}).get("upside_signal_score", 0.0) or 0.0), 4),
            }
            for doc in sorted(
                recent_docs,
                key=lambda item: (
                    float((item.get("text_signal") or {}).get("calibrated_signal_score", 0.0) or 0.0),
                    str(item.get("available_at", "")),
                ),
                reverse=True,
            )[:5]
        ]
        chart_pack = self._folder_chart_pack(folder_key, recent_docs)
        analyst_view = self._analyst_view_for_folder(folder_key, recent_records)
        result = {
            "query": query,
            "query_identity": query_identity,
            "folder_key": folder_key,
            "folder_title": folder.get("folder_title", folder_key),
            "window_start": cutoff.date().isoformat() if cutoff else "",
            "window_end": latest_date.date().isoformat() if latest_date else "",
            "window": window["key"],
            "window_label": window["label"],
            "document_count": len(docs),
            "analyzed_document_count": len(recent_docs),
            "llm_used": False,
            "model": "precomputed-local",
            "cache_status": "computed-local",
            "precomputed_demo_ready": True,
            "short_conclusion": (
                f"{folder.get('folder_title', 'This folder')} has {len(recent_docs)} documents in the {window['label']} window. "
                f"Average signal is {avg(signal_scores):.2f}, risk {avg(risk_scores):.2f}, upside {avg(upside_scores):.2f}."
            ),
            "metrics": {
                "avg_signal": avg(signal_scores),
                "avg_risk": avg(risk_scores),
                "avg_upside": avg(upside_scores),
                "top_signals": [{"label": key, "count": value} for key, value in active_signals.most_common(4)],
                "source_types": [{"label": key, "count": value} for key, value in source_types.most_common(4)],
            },
            "chart_pack": chart_pack,
            "analyst_view": analyst_view,
            "suggested_charts": self._chart_suggestions_for_folder(folder_key),
            "top_documents": top_docs,
        }
        llm_config = payload.get("llm") if isinstance(payload.get("llm"), dict) else {}
        api_key, model, endpoint, used_server_llm = resolve_llm_config(_llm_config_for_task(llm_config, "post"))
        result["api_key_received"] = bool(str(llm_config.get("api_key", "")).strip())
        allow_llm = self._folder_analysis_llm_allowed(payload)
        result["server_llm"] = bool(used_server_llm and allow_llm)
        result["api_key_persisted"] = False
        if api_key and allow_llm:
            request_format = llm_request_format(endpoint)
            system_prompt = (
                "You are a data-extraction engine and sharp financial headline writer for analyst dashboards. Return JSON only. "
                "Do not write prose, Markdown, conclusions, investment advice, or explanations. "
                "Use only supplied documents, dates, financial tables, and numeric text-feature scores. "
                "Create a fresh company-specific headline; never copy the fallback analyst_view title."
            )
            llm_docs = [
                {
                    "title": doc.get("title", ""),
                    "available_at": doc.get("available_at", ""),
                    "source_type": doc.get("source_type", ""),
                    "excerpt": doc.get("excerpt", ""),
                    "financial_text": excerpt(str(record.get("body") or record.get("text") or ""), FOLDER_ANALYSIS_LLM_TEXT_CHARS),
                    "signals": doc.get("active_signals", []),
                    "risk": (doc.get("text_signal") or {}).get("risk_alert_score", 0.0),
                    "upside": (doc.get("text_signal") or {}).get("upside_signal_score", 0.0),
                }
                for doc, record in zip(recent_docs[:FOLDER_ANALYSIS_LLM_DOC_LIMIT], recent_records[:FOLDER_ANALYSIS_LLM_DOC_LIMIT])
            ]
            user_prompt = (
                "Extract data for a compact professional investor dashboard. Return JSON only with this schema:\n"
                "{\"analyst_view\":{\"title\":\"Revenue grew, margins asked for a meeting\",\"subtitle\":\"...\","
                "\"source_document\":{\"title\":\"...\",\"available_at\":\"...\",\"source_type\":\"...\",\"url\":\"...\"},"
                "\"metric_cards\":[{\"label\":\"Revenue\",\"value\":\"$117.2B\",\"delta\":\"-5.5%\",\"tone\":\"negative\"}],"
                "\"tables\":[{\"title\":\"Key numbers\",\"columns\":[\"Metric\",\"Current\",\"Prior\",\"Change\"],"
                "\"rows\":[[\"Revenue\",\"$117.2B\",\"$123.9B\",\"-5.5%\",\"negative\"]]}],"
                "\"charts\":[{\"type\":\"compare_bars\",\"title\":\"Income Statement\",\"subtitle\":\"Current vs prior\","
                "\"rows\":[{\"metric\":\"Revenue\",\"current\":117154,\"prior\":123945,\"current_label\":\"$117.2B\","
                "\"prior_label\":\"$123.9B\",\"change_label\":\"-5.5%\",\"tone\":\"negative\"}]},"
                "{\"type\":\"mix_bar\",\"title\":\"Revenue Mix\",\"subtitle\":\"Current composition\","
                "\"segments\":[{\"label\":\"Products\",\"value\":96388,\"share\":82.3,\"value_label\":\"$96.4B\"}]},"
                "{\"type\":\"margin_bars\",\"title\":\"Margins\",\"subtitle\":\"As share of revenue\","
                "\"rows\":[{\"label\":\"Gross\",\"value\":43.0,\"value_label\":\"43.0%\"}]}]},"
                "\"chart_pack\":{\"charts\":[]}}\n"
                "Rules: extract real financial KPIs from tables if present; compare current period to prior comparable period; "
                "analyst_view.title must be a fresh, company-specific, witty or mildly sarcastic analyst headline grounded in the numbers, max 16 words, no emojis; "
                "do not reuse the supplied fallback title; mention the most important issue such as margin, EPS, revenue, debt, payout, guidance, or red flags; "
                "omit empty/all-zero charts; do not summarize in prose; if the supplied fallback analyst_view is already good, "
                "return its numeric dashboard improved but still write a new title.\n\n"
                f"Data:\n{json.dumps({**result, 'documents': llm_docs}, ensure_ascii=False)}"
            )
            request_payload = (
                {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.2,
                }
                if request_format == "chat_completions"
                else {
                    "model": model,
                    "instructions": system_prompt,
                    "input": user_prompt,
                    "store": False,
                }
            )
            response_payload = self._post_llm_json(endpoint, request_payload, api_key)
            text = extract_chat_completion_text(response_payload) if request_format == "chat_completions" else extract_response_text(response_payload)
            if not text:
                raise UpstreamServiceError(HTTPStatus.BAD_GATEWAY, "LLM returned an empty response.")
            llm_analyst_view = self._extract_llm_analyst_view(text)
            if llm_analyst_view:
                result["analyst_view"] = llm_analyst_view
            llm_chart_pack = self._extract_llm_chart_pack(text)
            if llm_chart_pack:
                result["chart_pack"] = llm_chart_pack
            result["llm_used"] = True
            result["model"] = model
            result["cache_status"] = "computed-llm"
        elif api_key:
            result["llm_skip_reason"] = (
                "Folder analysis uses precomputed local dashboards by default in public demo mode "
                "to avoid Cloudflare 524 timeouts. Set FINPORTFOLIO_FOLDER_ANALYSIS_LLM=1 for live LLM extraction."
            )
        if not result.get("llm_used"):
            with self._folder_analysis_lock:
                self._folder_analysis_cache[cache_key] = self._copy_payload(result)
        return result

    def _indexed_site_records(self, site_key: str) -> tuple[int, list[dict[str, Any]]] | None:
        connection = self._open_search_index()
        if connection is None:
            return None
        like_pattern = f"%{site_key.lower()}%"
        try:
            total = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM documents
                    WHERE lower(canonical_url) LIKE ? OR lower(url) LIKE ?
                    """,
                    (like_pattern, like_pattern),
                ).fetchone()[0]
            )
            rows = connection.execute(
                """
                SELECT d.record_json
                FROM documents d
                LEFT JOIN document_features f ON f.doc_id = d.doc_id
                WHERE lower(d.canonical_url) LIKE ? OR lower(d.url) LIKE ?
                ORDER BY
                    COALESCE(f.calibrated_signal_score, 0.0)
                    + 0.35 * COALESCE(f.upside_signal_score, 0.0)
                    + 0.35 * COALESCE(f.risk_alert_score, 0.0) DESC,
                    d.available_at DESC,
                    d.source_credibility DESC
                LIMIT ?
                """,
                (like_pattern, like_pattern, MY_VIBE_INDEX_CANDIDATE_LIMIT),
            ).fetchall()
            return total, self._records_from_index_rows(rows)
        except sqlite3.Error:
            return None
        finally:
            connection.close()

    def _indexed_signal_candidates(self, connection: sqlite3.Connection, mode: str) -> list[dict[str, Any]]:
        if mode == "opportunity":
            order_expression = "(f.upside_signal_score + f.calibrated_signal_score)"
        elif mode == "risk":
            order_expression = "(f.risk_alert_score + f.calibrated_signal_score)"
        else:
            order_expression = "f.calibrated_signal_score"
        rows = connection.execute(
            f"""
            SELECT d.record_json
            FROM document_features f
            JOIN documents d ON d.doc_id = f.doc_id
            WHERE f.calibrated_signal_score > 0 AND d.available_at <= ?
            ORDER BY {order_expression} DESC, d.available_at DESC
            LIMIT ?
            """,
            (SEARCH_CUTOFF, SEARCH_INDEX_CANDIDATE_LIMIT),
        ).fetchall()
        return self._records_from_index_rows(rows)

    def _indexed_ticker_candidates(self, connection: sqlite3.Connection, tickers: list[str]) -> tuple[int, list[dict[str, Any]]]:
        clauses = " OR ".join("d.matched_tickers_json LIKE ?" for _ in tickers)
        params = [f'%"{ticker}"%' for ticker in tickers]
        total = int(
            connection.execute(
                f"SELECT COUNT(*) FROM documents d WHERE ({clauses}) AND d.available_at <= ?",
                [*params, SEARCH_CUTOFF],
            ).fetchone()[0]
        )
        rows = connection.execute(
            f"""
            SELECT d.record_json
            FROM documents d
            LEFT JOIN document_features f ON f.doc_id = d.doc_id
            WHERE ({clauses}) AND d.available_at <= ?
            ORDER BY
                d.available_at DESC,
                COALESCE(f.calibrated_signal_score, 0.0)
                + 0.25 * COALESCE(f.upside_signal_score, 0.0)
                + 0.25 * COALESCE(f.risk_alert_score, 0.0) DESC,
                d.source_credibility DESC
            LIMIT ?
            """,
            [*params, SEARCH_CUTOFF, SEARCH_INDEX_CANDIDATE_LIMIT],
        ).fetchall()
        return total, self._records_from_index_rows(rows)

    def _indexed_search_rows(self, query: str) -> tuple[int, list[dict[str, Any]]] | None:
        connection = self._open_search_index()
        if connection is None:
            return None
        try:
            if not query.strip():
                rows = connection.execute(
                    """
                    SELECT record_json
                    FROM documents
                    WHERE available_at <= ?
                    ORDER BY available_at DESC, source_credibility DESC, doc_id DESC
                    LIMIT 25
                    """,
                    (SEARCH_CUTOFF,),
                ).fetchall()
                records = self._records_from_index_rows(rows)
                result_rows = [self._result_row(record, 0.25) for record in records]
                for rank, row in enumerate(result_rows, start=1):
                    row["rank"] = rank
                total = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM documents WHERE available_at <= ?",
                        (SEARCH_CUTOFF,),
                    ).fetchone()[0]
                )
                return total, result_rows

            mode = self._signal_discovery_mode(query)
            candidates: dict[str, dict[str, Any]] = {}
            lexical_scores: dict[str, float] = {}
            entity_tickers = self._query_entity_tickers(query)
            ticker_count = 0
            if entity_tickers:
                ticker_count, ticker_records = self._indexed_ticker_candidates(connection, entity_tickers)
                for record in ticker_records:
                    doc_id = str(record.get("doc_id", ""))
                    candidates[doc_id] = record
                    lexical_scores[doc_id] = self._search_score(record, query)
            fts_query = self._fts_query(query)
            fts_count = 0
            if fts_query and not entity_tickers:
                rows = connection.execute(
                    """
                    SELECT d.record_json
                    FROM documents_fts f
                    JOIN documents d ON d.doc_id = f.doc_id
                    WHERE documents_fts MATCH ? AND d.available_at <= ?
                    ORDER BY bm25(documents_fts)
                    LIMIT ?
                    """,
                    (fts_query, SEARCH_CUTOFF, SEARCH_INDEX_CANDIDATE_LIMIT),
                ).fetchall()
                for record in self._records_from_index_rows(rows):
                    doc_id = str(record.get("doc_id", ""))
                    candidates[doc_id] = record
                    lexical_scores[doc_id] = self._search_score(record, query)
                fts_count = len(rows)

            if mode:
                for record in self._indexed_signal_candidates(connection, mode):
                    if entity_tickers and not self._record_matches_query_entity(record, entity_tickers):
                        continue
                    doc_id = str(record.get("doc_id", ""))
                    candidates.setdefault(doc_id, record)
                    lexical_scores.setdefault(doc_id, self._search_score(record, query))

            feature_lookup = self.text_features()
            has_signal_features = bool(feature_lookup)
            rows: list[dict[str, Any]] = []
            for doc_id, record in candidates.items():
                lexical = lexical_scores.get(doc_id, 0.0)
                text_features = feature_lookup.get(doc_id, {})
                if lexical <= 0 and not (mode and has_signal_features):
                    continue
                if mode and has_signal_features and not text_features:
                    continue
                if mode == "opportunity":
                    matched_tickers = {str(ticker).upper() for ticker in record.get("matched_tickers", []) or []}
                    if not any(ticker and ticker != "MARKET" for ticker in matched_tickers):
                        continue
                score = self._feature_aware_score(record, lexical, query, text_features)
                if score <= 0:
                    continue
                rows.append(self._result_row(record, score, text_features))
            self._sort_search_results(rows)
            count = max(len(rows), fts_count, ticker_count)
            return count, rows
        except sqlite3.Error:
            return None
        finally:
            connection.close()

    def _result_row(
        self,
        record: dict[str, Any],
        score: float,
        text_features: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = str(record.get("canonical_url") or record.get("url") or "")
        text_features = text_features if text_features is not None else self._record_signal_features(record)
        macro_rule = self._macro_rule_payload(record)
        return {
            "rank": 0,
            "doc_id": record.get("doc_id", ""),
            "title": record.get("title", ""),
            "excerpt": self._result_excerpt(record, text_features),
            "source": record.get("source", ""),
            "source_type": record.get("source_type", ""),
            "site_name": _site_label(url),
            "url": url,
            "published_at": record.get("published_at", ""),
            "available_at": record.get("available_at", ""),
            "matched_tickers": record.get("matched_tickers", []),
            "matched_holdings": record.get("matched_holdings", []),
            "event_tags": self._compact_tags(record, text_features, limit=3),
            "risk_terms": (record.get("risk_terms", []) or [])[:3],
            "source_credibility": record.get("source_credibility", 0.0),
            "score": round(score, 6),
            "text_signal": text_features,
            "signal_strength": text_features.get("calibrated_signal_score", 0.0),
            "active_signals": text_features.get("active_signals", []),
            "macro_rule": macro_rule,
        }

    def _search_rows(self, query: str) -> tuple[int, list[dict[str, Any]]]:
        indexed = self._indexed_search_rows(query)
        if indexed is not None:
            return indexed

        documents = [
            record
            for record in self.documents()
            if self._is_historical_search_row(record)
        ]
        mode = self._signal_discovery_mode(query)
        if not query.strip():
            latest = sorted(
                documents,
                key=lambda record: (
                    str(record.get("available_at") or record.get("published_at") or ""),
                    float(record.get("source_credibility", 0.0) or 0.0),
                    str(record.get("doc_id", "")),
                ),
                reverse=True,
            )[:25]
            rows = [self._result_row(record, 0.25) for record in latest]
            for rank, row in enumerate(rows, start=1):
                row["rank"] = rank
            return len(documents), rows

        feature_lookup = self.text_features()
        has_signal_features = bool(feature_lookup)
        rows: list[dict[str, Any]] = []
        entity_tickers = self._query_entity_tickers(query)
        for record in documents:
            lexical = self._search_score(record, query)
            doc_id = str(record.get("doc_id", ""))
            text_features = feature_lookup.get(doc_id, {})
            if lexical <= 0 and not (mode and has_signal_features):
                continue
            if mode and has_signal_features and not text_features:
                continue
            if mode == "opportunity":
                matched_tickers = {str(ticker).upper() for ticker in record.get("matched_tickers", []) or []}
                if not any(ticker and ticker != "MARKET" for ticker in matched_tickers):
                    continue
            if mode and entity_tickers and not self._record_matches_query_entity(record, entity_tickers):
                continue
            score = self._feature_aware_score(record, lexical, query, text_features)
            if score <= 0:
                continue
            rows.append(self._result_row(record, score, text_features))
        self._sort_search_results(rows)
        return len(rows), rows

    def search_payload(
        self,
        query: str,
        *,
        limit: int = DEFAULT_SEARCH_LIMIT,
        offset: int = 0,
        group_key: str = "",
        folder_key: str = "",
    ) -> dict[str, Any]:
        settings = self.load_settings()
        raw_count, rows = self._search_rows(query)
        filtered_rows = self._historical_search_rows(rows)
        if len(filtered_rows) != len(rows):
            raw_count = len(filtered_rows)
        rows = filtered_rows
        grouped_results, grouped_lookup = self._group_search_results(rows)
        foldered_results, foldered_lookup = self._folder_search_results(grouped_results, query=query)
        use_folders = self._should_folder_results(query, grouped_results)
        group_meta = None
        folder_meta = None
        if group_key:
            results = annotate_results_with_favorites(grouped_lookup.get(group_key, []), settings["favorite_websites"])
            if results:
                group_meta = {
                    "group_key": group_key,
                    "title": results[0].get("group_title") or results[0].get("title", ""),
                    "count": len(results),
                    "latest_available_at": self._freshness_value(results[0]),
                    "normalized_title": results[0].get("group_normalized_title", ""),
                }
            count = len(results)
        elif folder_key:
            results = annotate_results_with_favorites(foldered_lookup.get(folder_key, []), settings["favorite_websites"])
            if results:
                descriptor = self._folder_descriptor(results[0])
                folder_meta = {
                    "folder_key": folder_key,
                    "title": descriptor[1],
                    "summary": descriptor[2],
                    "count": len(results),
                    "document_count": sum(int(item.get("group_count", 1) or 1) for item in results),
                    "latest_available_at": self._freshness_value(results[0]),
                }
            count = len(results)
        else:
            results = foldered_results if use_folders else sort_results_for_refresh(grouped_results, settings["favorite_websites"])
            count = len(results)
        safe_limit = min(MAX_SEARCH_LIMIT, max(1, int(limit or DEFAULT_SEARCH_LIMIT)))
        safe_offset = max(0, int(offset or 0))
        page = results[safe_offset : safe_offset + safe_limit]
        return {
            "query": query,
            "query_intent": classify_query_intent(query).to_dict(),
            "signal_discovery_mode": self._signal_discovery_mode(query),
            "count": count,
            "raw_count": raw_count,
            "group_mode": bool(group_key),
            "folder_mode": bool(folder_key),
            "foldered": use_folders and not group_key and not folder_key,
            "group": group_meta,
            "folder": folder_meta,
            "grouped_count": len(grouped_results),
            "limit": safe_limit,
            "offset": safe_offset,
            "next_offset": safe_offset + safe_limit if safe_offset + safe_limit < len(results) else None,
            "prev_offset": max(0, safe_offset - safe_limit) if safe_offset > 0 else None,
            "corpus": {
                **self.corpus_summary(),
                "text_features": self.text_feature_summary(),
                "search_index": self.search_index_status(),
            },
            "results": page,
        }

    def toggle_favorite_payload(self, results: list[dict[str, Any]], target_url: str) -> dict[str, Any]:
        settings = self.load_settings()
        favorites, annotated = toggle_favorite_in_place(results, settings["favorite_websites"], target_url)
        settings["favorite_websites"] = [f"https://{key}/" for key in favorites]
        self.save_settings(settings)
        return {"settings": settings, "results": annotated}

    def my_vibe_sites(self) -> dict[str, Any]:
        settings = self.load_settings()
        favorites = normalize_favorite_websites(settings["favorite_websites"])
        domain_counts = self.corpus_summary().get("domain_counts", {})
        sites = []
        for key in favorites:
            count = int(domain_counts.get(key, 0) or 0)
            sites.append({"site_key": key, "display_name": key, "url": f"https://{key}/", "post_count": count})
        return {"sites": sites}

    def _post_from_record(self, record: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(record.get("doc_id", "")),
            "site": _site_label(str(record.get("url", ""))),
            "title": str(record.get("title", "")),
            "author": str(record.get("source", "sample_fin_news")),
            "published_at": str(record.get("published_at", "")),
            "url": str(record.get("url", "")),
            "summary": excerpt(str(record.get("body", "")), 280),
            "text": str(record.get("body", "")),
            "matched_tickers": record.get("matched_tickers", []),
            "matched_holdings": record.get("matched_holdings", []),
            "event_tags": record.get("event_tags", []),
            "risk_terms": record.get("risk_terms", []),
            "source_type": record.get("source_type", ""),
        }

    def _my_vibe_ranked_post(self, record: dict[str, Any], portfolio: dict[str, Any], index: int) -> dict[str, Any]:
        body = str(record.get("body", "") or "")
        url = str(record.get("url", "") or record.get("canonical_url", "") or "")
        doc_features = self.text_features().get(str(record.get("doc_id", "")), {})
        row = {
            "id": str(record.get("doc_id", "") or url),
            "site": _site_label(url),
            "title": str(record.get("title", "")),
            "author": str(record.get("source", "")),
            "published_at": str(record.get("published_at", "")),
            "url": url,
            "summary": excerpt(body, 280),
            "text_char_count": len(body),
        }
        holding_weights = {
            str(holding.get("ticker", "")).upper(): float(holding.get("weight", 0.0) or 0.0)
            for holding in portfolio.get("holdings", []) or []
            if isinstance(holding, dict)
        }
        matched_tickers = {str(ticker).upper() for ticker in record.get("matched_tickers", []) or []}
        matched_holdings = {str(ticker).upper() for ticker in record.get("matched_holdings", []) or []}
        direct_matches = sorted((matched_tickers | matched_holdings) & set(holding_weights))
        holding_weight_score = min(1.0, sum(holding_weights.get(ticker, 0.0) for ticker in direct_matches))
        ticker_specificity_score = 1.0 if direct_matches else 0.0

        tags = {str(tag).lower() for tag in (record.get("event_tags", []) or []) + (record.get("risk_terms", []) or [])}
        source_type = str(record.get("source_type", "")).lower()
        event_keywords = {
            "earnings",
            "guidance",
            "risk",
            "rates",
            "inflation",
            "credit",
            "margin",
            "demand",
            "supply_chain",
            "legal_regulatory",
            "current_report",
            "8-k",
        }
        event_score = min(1.0, sum(1 for tag in tags if tag in event_keywords or any(key in tag for key in event_keywords)) / 4.0)
        if source_type in {"sec_filing_exhibit", "company_earnings_release"}:
            event_score = min(1.0, event_score + 0.35)
        elif source_type in {"sec_filing_section", "company_press_release", "company_financial_report"}:
            event_score = min(1.0, event_score + 0.15)

        risk_score = min(1.0, len(record.get("risk_terms", []) or []) / 6.0)
        source_quality_score = float(record.get("source_credibility", 0.0) or 0.0)
        scoring_post = {
            "title": row["title"],
            "summary": row["summary"],
            "author": row["author"],
            "text": body[:4_000],
        }
        text_relevance_score = post_portfolio_relevance(scoring_post, portfolio)
        feature_signal_score = float(doc_features.get("calibrated_signal_score", 0.0) or 0.0)
        feature_upside_score = float(doc_features.get("upside_signal_score", 0.0) or 0.0)
        feature_risk_score = float(doc_features.get("risk_alert_score", 0.0) or 0.0)
        feature_usefulness_score = float(doc_features.get("historical_usefulness_score", 0.0) or 0.0)
        vibe_score = (
            3.0 * holding_weight_score
            + 1.5 * ticker_specificity_score
            + 1.0 * text_relevance_score
            + 0.75 * event_score
            + 0.35 * risk_score
            + 0.20 * source_quality_score
            + 1.20 * feature_signal_score
            + 0.35 * max(feature_upside_score, feature_risk_score)
            + 2.00 * feature_usefulness_score
        )

        row["matched_tickers"] = sorted(matched_tickers)
        row["matched_holdings"] = direct_matches
        row["event_tags"] = self._compact_tags(record, doc_features, limit=3)
        row["source_type"] = record.get("source_type", "")
        row["source_credibility"] = source_quality_score
        row["portfolio_relevance_score"] = round(vibe_score, 6)
        row["vibe_score"] = round(vibe_score, 6)
        row["text_signal"] = doc_features
        row["active_signals"] = doc_features.get("active_signals", [])
        row["feature_signal_score"] = round(feature_signal_score, 6)
        row["vibe_score_components"] = {
            "holding_weight_score": round(holding_weight_score, 6),
            "ticker_specificity_score": round(ticker_specificity_score, 6),
            "text_relevance_score": round(text_relevance_score, 6),
            "event_score": round(event_score, 6),
            "risk_score": round(risk_score, 6),
            "source_quality_score": round(source_quality_score, 6),
            "feature_signal_score": round(feature_signal_score, 6),
            "feature_upside_score": round(feature_upside_score, 6),
            "feature_risk_score": round(feature_risk_score, 6),
            "historical_usefulness_score": round(feature_usefulness_score, 6),
        }
        row["_original_index"] = index
        return row

    def my_vibe_posts(self, site_key: str, *, limit: int = 5, offset: int = 0) -> dict[str, Any]:
        settings = self.load_settings()
        portfolio = summarize_portfolio(settings["portfolio"])
        safe_limit = min(25, max(1, int(limit or 5)))
        safe_offset = max(0, int(offset or 0))
        portfolio_key = json.dumps(settings["portfolio"], sort_keys=True, separators=(",", ":"))
        index_status = self.search_index_status()
        cache_version = self._search_index_mtime_ns if index_status.get("usable") else self._documents_cache_mtime_ns
        cache_key = f"{self.documents_path}:{cache_version}:{site_key}:{portfolio_key}:indexed_v2"
        cached = self._vibe_rank_cache.get(cache_key)
        if cached is None:
            indexed = self._indexed_site_records(site_key)
            if indexed is not None:
                total, matching_records = indexed
            else:
                documents = self.documents()
                matching_records = [
                    record
                    for record in documents
                    if favorite_key(str(record.get("canonical_url") or record.get("url", ""))) == site_key
                ]
                total = len(matching_records)
            sorted_posts = [
                self._my_vibe_ranked_post(record, portfolio, index)
                for index, record in enumerate(matching_records)
            ]
            sorted_posts.sort(
                key=lambda row: (
                    float(row["vibe_score"]),
                    str(row.get("published_at", "")),
                    -int(row["_original_index"]),
                ),
                reverse=True,
            )
            for row in sorted_posts:
                row.pop("_original_index", None)
            cached = {"posts": sorted_posts, "total": total}
            self._vibe_rank_cache[cache_key] = cached
        sorted_posts = cached["posts"]
        total = int(cached["total"])
        page = sorted_posts[safe_offset : safe_offset + safe_limit]
        next_offset = safe_offset + len(page)
        return {
            "site_key": site_key,
            "posts": page,
            "total": total,
            "limit": safe_limit,
            "offset": safe_offset,
            "next_offset": next_offset if next_offset < min(total, len(sorted_posts)) else None,
        }

    def _find_post(self, post_id: str) -> dict[str, Any] | None:
        for record in self.documents():
            if str(record.get("doc_id", "")) == post_id:
                return self._post_from_record(record)
        return None

    def analyze_my_vibe_post(self, payload: dict[str, Any]) -> dict[str, Any]:
        post = self._find_post(str(payload.get("post_id", "")))
        if post is None:
            raise KeyError("post_not_found")
        llm = payload.get("llm", {}) if isinstance(payload.get("llm", {}), dict) else {}
        settings = self.load_settings()
        portfolio_summary = summarize_portfolio(settings["portfolio"])
        prompt = build_portfolio_impact_prompt(post, portfolio_summary, self.macro_snapshot or {})
        post_llm = _llm_config_for_task(llm, "post")
        api_key, model, _, used_server_llm = resolve_llm_config(post_llm)
        api_key_received = bool(str(llm.get("api_key", "")).strip())
        if api_key:
            analysis = self._call_llm_for_post(prompt, post_llm)
            return {
                "post": post_for_ui(post),
                "model": model,
                "llm_used": True,
                "server_llm": used_server_llm,
                "api_key_received": api_key_received,
                "api_key_persisted": False,
                "analysis_markdown": analysis,
            }
        return self._local_analysis_payload(post, portfolio_summary, prompt, api_key_received)

    def _local_analysis_payload(
        self,
        post: dict[str, Any],
        portfolio_summary: dict[str, Any],
        prompt: dict[str, Any],
        api_key_received: bool,
    ) -> dict[str, Any]:
        post_text = prompt["post"]["text"].lower()
        matched_tickers = {str(ticker).upper() for ticker in post.get("matched_tickers", []) or []}
        direct_post_tickers = {ticker for ticker in matched_tickers if ticker and ticker != "MARKET"}
        source_type = str(post.get("source_type", "") or "").lower()
        is_macro_or_market = not direct_post_tickers or source_type.startswith("official_macro")
        holdings = []
        for holding in portfolio_summary.get("holdings", []):
            ticker = str(holding.get("ticker", "")).upper()
            linked = ticker in direct_post_tickers or re.search(rf"\b{re.escape(ticker.lower())}\b", post_text) is not None
            if linked:
                holdings.append(
                    {
                        "ticker": ticker,
                        "connection": "Direct ticker/document match",
                        "possible_effect": _effect_from_text(post_text),
                        "check_horizon": "1-2 weeks",
                    }
                )
        if not holdings and is_macro_or_market:
            holdings = [
                {
                    "ticker": str(item.get("ticker", "")).upper(),
                    "connection": "Portfolio-level macro/market context",
                    "possible_effect": "No immediate portfolio-specific signal",
                    "check_horizon": "no change",
                }
                for item in portfolio_summary.get("holdings", [])[:3]
            ]
        return {
            "post": post_for_ui(post),
            "model": "rule-based-local",
            "llm_used": False,
            "server_llm": False,
            "api_key_received": api_key_received,
            "api_key_persisted": False,
            "short_conclusion": _short_conclusion(post_text, holdings),
            "affected_holdings": holdings,
            "what_changed": [
                "The post adds source-grounded context to the current portfolio evidence set.",
                "It should be treated as an input for monitoring, not as a standalone trading instruction.",
            ],
            "checks_before_action": _checks_from_text(post_text),
            "confidence": "medium" if holdings else "low",
            "falsification_reasons": [
                "The event may already be priced in.",
                "Follow-up filings or official data may contradict the post.",
                "Macro regime changes can dominate single-document signals.",
            ],
        }

    def _call_llm_for_post(self, prompt_payload: dict[str, Any], llm_config: dict[str, Any]) -> str:
        api_key, model, endpoint, _ = resolve_llm_config(llm_config)
        if not api_key:
            raise ValueError("LLM API key is required for remote analysis.")
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("LLM endpoint must use HTTPS, except localhost endpoints.")

        request_format = llm_request_format(endpoint)
        system_prompt = (
            "You are a US equity portfolio risk analyst. Analyze selected financial posts as risk evidence, "
            "not as trading commands. Do not quote or reproduce the full source post. Separate facts from "
            "your assumptions and avoid buy/sell recommendations."
        )
        user_prompt = self._build_llm_user_prompt(prompt_payload)
        if request_format == "chat_completions":
            request_payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.2,
            }
        else:
            request_payload = {
                "model": model,
                "instructions": system_prompt,
                "input": user_prompt,
                "store": False,
            }
        response_payload = self._post_llm_json(endpoint, request_payload, api_key)
        text = extract_chat_completion_text(response_payload) if request_format == "chat_completions" else extract_response_text(response_payload)
        if not text:
            raise UpstreamServiceError(HTTPStatus.BAD_GATEWAY, "LLM returned an empty response.")
        return text

    def _build_llm_user_prompt(self, prompt_payload: dict[str, Any]) -> str:
        safe_payload = dict(prompt_payload)
        post = dict(safe_payload.get("post", {}))
        post["text"] = str(post.get("text", ""))[:MAX_PROMPT_POST_CHARS]
        safe_payload["post"] = post
        portfolio = dict(safe_payload.get("portfolio", {}))
        holdings = portfolio.get("holdings") if isinstance(portfolio.get("holdings"), list) else []
        portfolio["holdings"] = holdings[:MAX_PORTFOLIO_HOLDINGS]
        safe_payload["portfolio"] = portfolio
        return (
            "Analyze the selected post only from the perspective of the user's current US equity portfolio.\n"
            "Return plain Markdown, without code fences and without horizontal separators.\n"
            "Use exactly these sections:\n"
            "1. Short conclusion: 2-4 concise sentences.\n"
            "2. Affected portfolio holdings: a Markdown table with columns Ticker, Link to post, Possible risk/effect, Check horizon.\n"
            "3. What changed: distinguish New signal from the post vs Existing portfolio risk.\n"
            "4. Checks before action: 3-6 concrete checks using filings, official data, earnings releases, rates, credit spreads, oil, USD, or sector data where relevant.\n"
            "5. Confidence: low/medium/high, with 1-2 reasons that could falsify the conclusion.\n"
            "If there is no direct connection to a holding, say so explicitly. Do not recommend buying or selling.\n\n"
            f"Data:\n{json.dumps(safe_payload, ensure_ascii=False)}"
        )

    def _post_llm_json(self, endpoint: str, payload: dict[str, Any], api_key: str) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        for attempt in range(1, LLM_MAX_ATTEMPTS + 1):
            request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=LLM_TIMEOUT_SECONDS) as response:
                    raw = response.read()
                    return json.loads(raw.decode("utf-8"))
            except urllib.error.HTTPError as exc:
                raw_error = exc.read()
                retry_after = _parse_retry_after(exc.headers.get("Retry-After") if exc.headers else None)
                should_retry = exc.code in LLM_RETRYABLE_STATUS_CODES and attempt < LLM_MAX_ATTEMPTS
                if should_retry:
                    delay = retry_after if retry_after is not None else LLM_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
                    time.sleep(min(LLM_RETRY_MAX_SECONDS, delay))
                    continue
                detail = _extract_upstream_error(raw_error)
                raise UpstreamServiceError(
                    _upstream_local_status(exc.code),
                    f"LLM provider error ({exc.code}): {detail}",
                ) from exc
            except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
                if attempt < LLM_MAX_ATTEMPTS:
                    time.sleep(min(LLM_RETRY_MAX_SECONDS, LLM_RETRY_BASE_SECONDS * (2 ** (attempt - 1))))
                    continue
                raise UpstreamServiceError(HTTPStatus.GATEWAY_TIMEOUT, f"LLM request failed: {_clean_text(exc)}") from exc
            except json.JSONDecodeError as exc:
                raise UpstreamServiceError(HTTPStatus.BAD_GATEWAY, "LLM returned invalid JSON.") from exc
        raise UpstreamServiceError(HTTPStatus.BAD_GATEWAY, "LLM request retry loop ended unexpectedly.")


def _effect_from_text(text: str) -> str:
    if any(term in text for term in ("lawsuit", "investigation", "regulation", "scrutiny")):
        return "Regulatory or legal pressure could raise risk premium"
    if any(term in text for term in ("rates", "fed", "credit", "deposit")):
        return "Rates or credit conditions may affect valuation and funding"
    if any(term in text for term in ("earnings", "revenue", "margin", "demand")):
        return "Earnings expectations or demand assumptions may need review"
    if any(term in text for term in ("supply", "oil", "opec")):
        return "Supply or commodity conditions may affect sector cash flows"
    return "Portfolio impact is contextual rather than direct"


def _short_conclusion(text: str, holdings: list[dict[str, Any]]) -> str:
    if any(term in text for term in ("risk", "warn", "pressure", "investigation", "scrutiny")):
        return "The post adds a risk-monitoring signal for the current portfolio, especially for directly linked holdings."
    if any(term in text for term in ("boost", "strong", "support", "gained", "demand")):
        return "The post adds a supportive but still source-limited signal for linked holdings."
    if holdings:
        return "The post is relevant to portfolio monitoring, but does not justify a position change by itself."
    return "The post has limited direct relevance to the current portfolio."


def _checks_from_text(text: str) -> list[str]:
    checks = ["Compare against official filings, releases, or macro data before acting."]
    if any(term in text for term in ("rates", "fed", "inflation", "credit")):
        checks.append("Check Treasury yields, credit spreads, and the next Fed communication.")
    if any(term in text for term in ("earnings", "revenue", "margin", "guidance")):
        checks.append("Check the next earnings release, guidance, and analyst revision trend.")
    if any(term in text for term in ("oil", "opec", "supply")):
        checks.append("Check WTI/Brent, inventories, and OPEC supply headlines.")
    if any(term in text for term in ("lawsuit", "investigation", "regulation", "scrutiny")):
        checks.append("Check primary legal/regulatory documents and company responses.")
    return checks


class FinPortfolioRequestHandler(BaseHTTPRequestHandler):
    server_version = "FinPortfolioIR/0.1"

    @property
    def app(self) -> FinPortfolioWebService:
        return self.server.app  # type: ignore[attr-defined]

    def _ensure_public_demo_session(self) -> None:
        self._session_cookie_header = ""
        if not self.app.public_demo:
            return
        cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get("fpir_demo_session")
        session_id = morsel.value if morsel else ""
        if not re.fullmatch(r"[a-zA-Z0-9_-]{16,80}", session_id or ""):
            session_id = uuid.uuid4().hex
            self._session_cookie_header = (
                f"fpir_demo_session={session_id}; Path=/; HttpOnly; SameSite=Lax; Max-Age=604800"
            )
        self.app._request_context.session_id = session_id

    def do_GET(self) -> None:
        self._ensure_public_demo_session()
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/health":
                self._send_json({"status": "ok", "service": "FinPortfolio IR"})
                return
            if parsed.path == "/api/dashboard":
                self._send_json(self.app.dashboard_payload())
                return
            if parsed.path == "/api/search":
                query_values = parse_qs(parsed.query)
                query = query_values.get("q", [""])[0]
                limit = int(query_values.get("limit", [str(DEFAULT_SEARCH_LIMIT)])[0] or DEFAULT_SEARCH_LIMIT)
                offset = int(query_values.get("offset", ["0"])[0] or 0)
                group_key = query_values.get("group_key", [""])[0]
                folder_key = query_values.get("folder_key", [""])[0]
                self._send_json(
                    self.app.search_payload(query, limit=limit, offset=offset, group_key=group_key, folder_key=folder_key)
                )
                return
            if parsed.path == "/api/chart-lab/options":
                self._send_json(self.app.chart_lab_options_payload())
                return
            if parsed.path == "/api/chart-lab/chart":
                self._send_json(self.app.chart_lab_payload(parse_qs(parsed.query)))
                return
            if parsed.path == "/api/settings":
                self._send_json(self.app.load_settings())
                return
            if parsed.path == "/api/my-vibe/sites":
                self._send_json(self.app.my_vibe_sites())
                return
            if parsed.path == "/api/my-vibe/posts":
                query = parse_qs(parsed.query)
                site = query.get("site", [""])[0]
                limit = int(query.get("limit", ["5"])[0] or 5)
                offset = int(query.get("offset", ["0"])[0] or 0)
                self._send_json(self.app.my_vibe_posts(site, limit=limit, offset=offset))
                return
            if parsed.path.startswith("/document/"):
                doc_id = unquote(parsed.path.removeprefix("/document/"))
                self._send_html(self.app.document_view_html(doc_id))
                return
            if parsed.path.startswith("/icons/"):
                self._send_icon(parsed.path.removeprefix("/icons/"))
                return
            self._send_static(parsed.path)
        except KeyError as exc:
            self._send_json({"error": str(exc).strip("'")}, HTTPStatus.NOT_FOUND)
        except UpstreamServiceError as exc:
            self._send_json({"error": str(exc)}, exc.status)
        except Exception as exc:  # pragma: no cover - defensive server guard
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        self._ensure_public_demo_session()
        parsed = urlparse(self.path)
        try:
            body = _read_json_body(self)
            if parsed.path == "/api/settings":
                self._send_json(self.app.save_settings(body))
                return
            if parsed.path == "/api/favorites/toggle":
                self._send_json(self.app.toggle_favorite_payload(body.get("results", []), str(body.get("url", ""))))
                return
            if parsed.path == "/api/favorites/validate-url":
                self._send_json(self.app.validate_website_payload(body))
                return
            if parsed.path == "/api/my-vibe/analyze":
                self._send_json(self.app.analyze_my_vibe_post(body))
                return
            if parsed.path == "/api/search/folder-analysis":
                self._send_json(self.app.analyze_search_folder(body))
                return
            if parsed.path == "/api/chart-lab/analyze":
                self._send_json(self.app.analyze_chart_lab_payload(body))
                return
            if parsed.path == "/api/portfolio/analyze":
                self._send_json(self.app.analyze_portfolio_ticker(body))
                return
            self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
        except KeyError as exc:
            self._send_json({"error": str(exc).strip("'")}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except UpstreamServiceError as exc:
            self._send_json({"error": str(exc)}, exc.status)
        except Exception as exc:  # pragma: no cover - defensive server guard
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = _json_dumps(payload)
        self.send_response(int(status))
        self._send_common_headers()
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, html_text: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = html_text.encode("utf-8")
        self.send_response(int(status))
        self._send_common_headers()
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_common_headers(self) -> None:
        cookie = getattr(self, "_session_cookie_header", "")
        if cookie:
            self.send_header("set-cookie", cookie)
        self.send_header("x-content-type-options", "nosniff")
        self.send_header("referrer-policy", "same-origin")
        self.send_header("x-frame-options", "DENY")
        self.send_header("permissions-policy", "camera=(), microphone=(), geolocation=()")

    def _send_static(self, path: str) -> None:
        if path in {"", "/"}:
            path = "/index.html"
        safe = path.strip("/").replace("\\", "/")
        if ".." in safe.split("/"):
            self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            return
        target = local_project_path(ROOT / "web" / safe)
        if not target.exists() or not target.is_file():
            self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            return
        content_type, _ = mimetypes.guess_type(str(target))
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self._send_common_headers()
        self.send_header("content-type", content_type or "application/octet-stream")
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_icon(self, filename: str) -> None:
        if filename not in ICON_FILES.values():
            self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            return
        target = local_project_path(ROOT / filename)
        data = target.read_bytes()
        content_type, _ = mimetypes.guess_type(str(target))
        self.send_response(HTTPStatus.OK)
        self._send_common_headers()
        self.send_header("content-type", content_type or "application/octet-stream")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class FinPortfolioHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], app: FinPortfolioWebService):
        super().__init__(server_address, FinPortfolioRequestHandler)
        self.app = app


def build_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    settings_path: str | Path | None = None,
    documents_path: str | Path | None = None,
    search_index_path: str | Path | None = None,
    public_demo: bool = False,
    demo_settings_dir: str | Path | None = None,
    warm_chart_lab: bool = False,
) -> FinPortfolioHTTPServer:
    app = FinPortfolioWebService(
        documents_path=Path(documents_path) if documents_path else default_documents_path(),
        settings_path=Path(settings_path) if settings_path else ROOT / "data" / "user_settings" / "settings.json",
        search_index_path=Path(search_index_path) if search_index_path else default_search_index_path(),
        public_demo=public_demo,
        demo_settings_dir=Path(demo_settings_dir) if demo_settings_dir else ROOT / "data" / "user_settings" / "demo_sessions",
        warm_chart_lab=warm_chart_lab,
    )
    return FinPortfolioHTTPServer((host, port), app)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local FinPortfolio IR dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--settings-path", default="")
    parser.add_argument("--documents-path", default="", help="JSONL corpus used by search and My Vibe.")
    parser.add_argument("--search-index-path", default="", help="SQLite FTS index used by search.")
    parser.add_argument("--public-demo", action="store_true", help="Use cookie-isolated settings for public bug-bash sharing.")
    parser.add_argument("--demo-settings-dir", default="", help="Directory for per-browser public demo settings.")
    args = parser.parse_args(argv)

    server = build_server(
        args.host,
        args.port,
        args.settings_path or None,
        args.documents_path or None,
        args.search_index_path or None,
        args.public_demo,
        args.demo_settings_dir or None,
        True,
    )
    url = f"http://{args.host}:{server.server_address[1]}"
    print(f"FinPortfolio IR dashboard: {html.escape(url)}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
