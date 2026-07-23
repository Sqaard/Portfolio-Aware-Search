"""Background worker for live IR LLM enrichment.

The search index is refreshed before this worker runs.  This script enriches
queued documents with compact JSON summaries/features and never stores API keys.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finportfolio_ir.io_utils import read_jsonl, write_jsonl  # noqa: E402
from web_app import (  # noqa: E402
    LLM_MAX_ATTEMPTS,
    LLM_RETRY_BASE_SECONDS,
    LLM_RETRY_MAX_SECONDS,
    LLM_RETRYABLE_STATUS_CODES,
    LLM_TIMEOUT_SECONDS,
    UpstreamServiceError,
    _clean_text,
    _extract_upstream_error,
    _parse_retry_after,
    _upstream_local_status,
    extract_chat_completion_text,
    extract_response_text,
    is_safe_https_endpoint,
    llm_request_format,
    resolve_llm_config,
)


DEFAULT_LIVE_DIR = ROOT / "data" / "live_ir"
DEFAULT_QUEUE = DEFAULT_LIVE_DIR / "live_llm_queue.jsonl"
DEFAULT_DOCUMENTS = DEFAULT_LIVE_DIR / "merged_documents.jsonl"
DEFAULT_OUTPUT = DEFAULT_LIVE_DIR / "live_llm_summaries.jsonl"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, char in enumerate(stripped):
            if char != "{":
                continue
            try:
                value, _ = decoder.raw_decode(stripped[index:])
                break
            except json.JSONDecodeError:
                continue
        else:
            return {"headline": "LLM returned text, not JSON", "summary": stripped[:900], "facts": [], "numbers": []}
    return value if isinstance(value, dict) else {"headline": "LLM returned non-object JSON", "summary": str(value)[:900], "facts": [], "numbers": []}


def build_document_prompt(record: dict[str, Any]) -> tuple[str, str]:
    metadata = {
        "doc_id": record.get("doc_id", ""),
        "title": record.get("title", ""),
        "source": record.get("source", ""),
        "source_type": record.get("source_type", ""),
        "available_at": record.get("available_at", ""),
        "published_at": record.get("published_at", ""),
        "matched_tickers": record.get("matched_tickers", []) or [],
        "event_tags": record.get("event_tags", []) or [],
        "canonical_url": record.get("canonical_url") or record.get("url") or "",
    }
    body = " ".join(str(record.get("body", "") or "").split())[:9000]
    system_prompt = (
        "You enrich live financial IR documents after they are already searchable. "
        "Return JSON only. Be factual, concise, numeric when possible, and do not give buy/sell advice."
    )
    user_prompt = (
        "Create a compact IR enrichment record. Return JSON with schema "
        "{\"headline\":\"witty but evidence-grounded headline, max 14 words\","
        "\"summary\":\"one compact paragraph under 90 words\","
        "\"facts\":[\"3-6 concrete fact bullets\"],"
        "\"numbers\":[\"0-8 numeric facts\"],"
        "\"risk_level\":\"low|medium|high\","
        "\"investor_relevance\":\"low|medium|high\"}. "
        "If the document has little financial evidence, say that plainly.\n\n"
        f"Metadata:\n{json.dumps(metadata, ensure_ascii=False)}\n\n"
        f"Document text:\n{body}"
    )
    return system_prompt, user_prompt


def post_llm_json(endpoint: str, payload: dict[str, Any], api_key: str, timeout_seconds: int = LLM_TIMEOUT_SECONDS) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    for attempt in range(1, LLM_MAX_ATTEMPTS + 1):
        request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw_error = exc.read()
            retry_after = _parse_retry_after(exc.headers.get("Retry-After") if exc.headers else None)
            if exc.code in LLM_RETRYABLE_STATUS_CODES and attempt < LLM_MAX_ATTEMPTS:
                delay = retry_after if retry_after is not None else LLM_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
                time.sleep(min(LLM_RETRY_MAX_SECONDS, delay))
                continue
            raise UpstreamServiceError(
                _upstream_local_status(exc.code),
                f"LLM provider error ({exc.code}): {_extract_upstream_error(raw_error)}",
            ) from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            if attempt < LLM_MAX_ATTEMPTS:
                time.sleep(min(LLM_RETRY_MAX_SECONDS, LLM_RETRY_BASE_SECONDS * (2 ** (attempt - 1))))
                continue
            raise UpstreamServiceError(HTTPStatus.GATEWAY_TIMEOUT, f"LLM request failed: {_clean_text(exc)}") from exc
        except json.JSONDecodeError as exc:
            raise UpstreamServiceError(HTTPStatus.BAD_GATEWAY, "LLM returned invalid JSON.") from exc
    raise UpstreamServiceError(HTTPStatus.BAD_GATEWAY, "LLM request retry loop ended unexpectedly.")


def call_document_llm(record: dict[str, Any], *, api_key: str, model: str, endpoint: str) -> dict[str, Any]:
    if not is_safe_https_endpoint(endpoint):
        raise ValueError("LLM endpoint must use HTTPS, except localhost endpoints.")
    request_format = llm_request_format(endpoint)
    system_prompt, user_prompt = build_document_prompt(record)
    if request_format == "chat_completions":
        request_payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.15,
        }
    else:
        request_payload = {
            "model": model,
            "instructions": system_prompt,
            "input": user_prompt,
            "store": False,
        }
    response_payload = post_llm_json(endpoint, request_payload, api_key)
    text = extract_chat_completion_text(response_payload) if request_format == "chat_completions" else extract_response_text(response_payload)
    if not text:
        raise UpstreamServiceError(HTTPStatus.BAD_GATEWAY, "LLM returned an empty response.")
    return parse_json_object(text)


def process_queue(
    *,
    queue_path: Path,
    documents_path: Path,
    output_path: Path,
    limit: int,
    dry_run: bool = False,
    llm_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    queue_rows = read_jsonl(queue_path) if queue_path.exists() else []
    documents = {str(row.get("doc_id", "")): row for row in (read_jsonl(documents_path) if documents_path.exists() else [])}
    existing_summaries = read_jsonl(output_path) if output_path.exists() else []
    completed = {str(row.get("doc_id", "")) for row in existing_summaries if str(row.get("status", "")) == "complete"}
    pending = [
        row
        for row in queue_rows
        if str(row.get("status", "pending") or "pending") == "pending"
        and str(row.get("doc_id", "")) not in completed
        and str(row.get("doc_id", "")) in documents
    ]
    pending.sort(key=lambda row: (-int(row.get("priority", 0) or 0), str(row.get("available_at", ""))))
    if limit > 0:
        pending = pending[:limit]

    api_key, model, endpoint, used_server_llm = resolve_llm_config(llm_config or {})
    if not api_key and not dry_run:
        return {
            "status": "skipped",
            "reason": "missing_llm_api_key",
            "pending_count": len(pending),
            "processed": 0,
            "api_key_was_not_written": True,
        }

    summary_by_doc = {str(row.get("doc_id", "")): row for row in existing_summaries if str(row.get("doc_id", ""))}
    queue_by_doc = {str(row.get("doc_id", "")): dict(row) for row in queue_rows if str(row.get("doc_id", ""))}
    processed = 0
    failed = 0
    for row in pending:
        doc_id = str(row.get("doc_id", ""))
        record = documents[doc_id]
        now = utc_now()
        queue_update = queue_by_doc.get(doc_id, dict(row))
        queue_update["attempts"] = int(queue_update.get("attempts", 0) or 0) + (0 if dry_run else 1)
        if dry_run:
            enrichment = {
                "headline": f"Dry-run enrichment for {doc_id}",
                "summary": "LLM was not called.",
                "facts": [],
                "numbers": [],
                "risk_level": "unknown",
                "investor_relevance": "unknown",
            }
        else:
            try:
                enrichment = call_document_llm(record, api_key=api_key, model=model, endpoint=endpoint)
            except Exception as exc:  # noqa: BLE001 - preserve queue state and continue.
                failed += 1
                queue_update["status"] = "failed"
                queue_update["last_error"] = str(exc)[:500]
                queue_update["last_attempt_at"] = now
                queue_by_doc[doc_id] = queue_update
                continue
        processed += 1
        summary_by_doc[doc_id] = {
            "doc_id": doc_id,
            "status": "complete",
            "model": model if not dry_run else "dry-run",
            "endpoint_host": endpoint.split("/")[2] if "://" in endpoint else "",
            "used_server_llm": used_server_llm,
            "processed_at": now,
            "source_type": record.get("source_type", ""),
            "available_at": record.get("available_at", ""),
            "title": record.get("title", ""),
            "enrichment": enrichment,
        }
        queue_update["status"] = "complete"
        queue_update["processed_at"] = now
        queue_update["model"] = model if not dry_run else "dry-run"
        queue_update.pop("last_error", None)
        queue_by_doc[doc_id] = queue_update

    merged_queue = []
    seen = set()
    for original in queue_rows:
        doc_id = str(original.get("doc_id", ""))
        merged_queue.append(queue_by_doc.get(doc_id, original))
        seen.add(doc_id)
    for doc_id, row in queue_by_doc.items():
        if doc_id not in seen:
            merged_queue.append(row)
    write_jsonl(queue_path, merged_queue)
    write_jsonl(output_path, sorted(summary_by_doc.values(), key=lambda item: str(item.get("processed_at", ""))))
    return {
        "status": "completed",
        "dry_run": dry_run,
        "pending_before": len(pending),
        "processed": processed,
        "failed": failed,
        "queue_path": str(queue_path),
        "documents_path": str(documents_path),
        "output_path": str(output_path),
        "model": model if api_key else "",
        "server_llm": used_server_llm if api_key else False,
        "api_key_was_not_written": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Process pending live IR LLM enrichment queue.")
    parser.add_argument("--queue", default=str(DEFAULT_QUEUE))
    parser.add_argument("--documents", default=str(DEFAULT_DOCUMENTS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    summary = process_queue(
        queue_path=Path(args.queue),
        documents_path=Path(args.documents),
        output_path=Path(args.output),
        limit=args.limit,
        dry_run=args.dry_run,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
