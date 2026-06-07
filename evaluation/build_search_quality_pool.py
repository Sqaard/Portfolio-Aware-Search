"""Build a human annotation pool from the live web search surface.

This complements ``build_annotation_pool.py``. The older pool starts from
offline retrieval JSONL; this one starts from ``web_app.search_payload`` so we
can evaluate what a user actually sees after folders and grouping.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any, Optional, Union
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.evaluate_ir_metrics import load_qrels  # noqa: E402
from finportfolio_ir.favorites import annotate_results_with_favorites, sort_results_for_refresh  # noqa: E402
from web_app import FinPortfolioWebService, FULL_DOCUMENTS_PATH, SEARCH_INDEX_PATH  # noqa: E402


QUERY_FIELDS = ["query_id", "query", "intent", "expected_ticker", "source_scope", "description"]

POOL_FIELDS = [
    "query_id",
    "query",
    "intent",
    "expected_ticker",
    "source_scope",
    "description",
    "rank",
    "folder_rank",
    "surface_rank",
    "child_rank",
    "surface_kind",
    "folder_key",
    "folder_title",
    "group_key",
    "group_title",
    "group_count",
    "doc_id",
    "title",
    "source",
    "site_name",
    "source_type",
    "published_at",
    "available_at",
    "score",
    "signal_strength",
    "matched_tickers",
    "matched_holdings",
    "event_tags",
    "risk_terms",
    "excerpt",
    "url",
    "document_path",
    "existing_relevance",
    "relevance",
    "label_source",
    "annotator",
    "notes",
]

RUN_FIELDS = ["query_id", "doc_id", "rank", "score", "method"]


def _join_values(value: Any) -> str:
    if isinstance(value, list):
        return "|".join(str(item) for item in value)
    if isinstance(value, tuple):
        return "|".join(str(item) for item in value)
    return str(value or "")


def _clean_query_row(row: dict[str, str], line_number: int) -> dict[str, str]:
    cleaned = {field: str(row.get(field, "") or "").strip() for field in QUERY_FIELDS}
    if not cleaned["query_id"]:
        raise ValueError(f"Missing query_id on line {line_number}.")
    if not cleaned["query"]:
        raise ValueError(f"Missing query text for query_id={cleaned['query_id']!r}.")
    return cleaned


def load_search_quality_queries(path: Union[str, Path]) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [field for field in ("query_id", "query") if field not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"Query file is missing required columns: {', '.join(missing)}")
        rows = [_clean_query_row(row, index) for index, row in enumerate(reader, start=2)]

    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in rows:
        query_id = row["query_id"]
        if query_id in seen:
            duplicates.add(query_id)
        seen.add(query_id)
    if duplicates:
        raise ValueError(f"Duplicate query_id values: {', '.join(sorted(duplicates))}")
    return rows


def _document_path(doc_id: str) -> str:
    if not doc_id:
        return ""
    return f"/documents/{quote(doc_id, safe='')}"


def _row_sort_key(row: dict[str, Any]) -> tuple[int, int, int, str, str]:
    folder_rank = int(row.get("_folder_rank", 0) or 0)
    return (
        folder_rank,
        int(row.get("_surface_rank", 0) or 0),
        int(row.get("_child_rank", 0) or 0),
        str(row.get("doc_id", "")),
        str(row.get("available_at", "")),
    )


def _pool_row(
    query: dict[str, str],
    result: dict[str, Any],
    *,
    rank: int,
    surface_rank: int,
    surface_kind: str,
    folder_key: str,
    folder_title: str,
    qrels: dict[str, dict[str, int]],
) -> dict[str, Any]:
    query_id = query["query_id"]
    doc_id = str(result.get("doc_id", "") or "")
    return {
        "query_id": query_id,
        "query": query["query"],
        "intent": query.get("intent", ""),
        "expected_ticker": query.get("expected_ticker", ""),
        "source_scope": query.get("source_scope", ""),
        "description": query.get("description", ""),
        "rank": rank,
        "folder_rank": result.get("_folder_rank", ""),
        "surface_rank": surface_rank,
        "child_rank": result.get("_child_rank", ""),
        "surface_kind": surface_kind,
        "folder_key": folder_key,
        "folder_title": folder_title,
        "group_key": result.get("group_key", ""),
        "group_title": result.get("group_title", ""),
        "group_count": result.get("group_count", ""),
        "doc_id": doc_id,
        "title": result.get("title", ""),
        "source": result.get("source", ""),
        "site_name": result.get("site_name", ""),
        "source_type": result.get("source_type", ""),
        "published_at": result.get("published_at", ""),
        "available_at": result.get("available_at", ""),
        "score": result.get("score", ""),
        "signal_strength": result.get("signal_strength", ""),
        "matched_tickers": _join_values(result.get("matched_tickers", [])),
        "matched_holdings": _join_values(result.get("matched_holdings", [])),
        "event_tags": _join_values(result.get("event_tags", [])),
        "risk_terms": _join_values(result.get("risk_terms", [])),
        "excerpt": result.get("excerpt", ""),
        "url": result.get("url", ""),
        "document_path": _document_path(doc_id),
        "existing_relevance": qrels.get(query_id, {}).get(doc_id, ""),
        "relevance": "",
        "label_source": "",
        "annotator": "",
        "notes": "",
    }


def collect_document_results(
    results: list[dict[str, Any]],
    *,
    include_group_children: bool = True,
    folder_key: str = "",
    folder_title: str = "",
    folder_rank: int = 0,
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    for surface_index, result in enumerate(results, start=1):
        surface_kind = str(result.get("result_kind") or "document")
        if result.get("doc_id"):
            item = dict(result)
            item["_folder_rank"] = folder_rank
            item["_surface_rank"] = surface_index
            item["_child_rank"] = 0
            item["_surface_kind"] = surface_kind
            item["_folder_key"] = folder_key
            item["_folder_title"] = folder_title
            collected.append(item)
        if include_group_children:
            for child_index, child in enumerate(result.get("group_children", []) or [], start=1):
                if not isinstance(child, dict) or not child.get("doc_id"):
                    continue
                item = dict(child)
                item.setdefault("group_key", result.get("group_key", ""))
                item.setdefault("group_title", result.get("group_title", result.get("title", "")))
                item.setdefault("group_count", result.get("group_count", ""))
                item["_folder_rank"] = folder_rank
                item["_surface_rank"] = surface_index
                item["_child_rank"] = child_index
                item["_surface_kind"] = "group_child"
                item["_folder_key"] = folder_key
                item["_folder_title"] = folder_title
                collected.append(item)
    return collected


def web_search_surface_results(
    service: FinPortfolioWebService,
    query: str,
    *,
    limit: int,
    folder_key: str = "",
) -> dict[str, Any]:
    """Return the same ranked rows as web search without dashboard metadata.

    ``FinPortfolioWebService.search_payload`` also attaches corpus summaries,
    text-feature summaries, and search-index status. Those are useful for the UI
    API but expensive when building many annotation rows. This helper keeps the
    ranking, grouping, foldering, and favorite-site behavior aligned with the
    UI while avoiding that repeated metadata work.
    """

    settings = service.load_settings()
    _raw_count, rows = service._search_rows(query)
    rows = service._historical_search_rows(rows)
    grouped_results, _grouped_lookup = service._group_search_results(rows)
    foldered_results, foldered_lookup = service._folder_search_results(grouped_results, query=query)

    if folder_key:
        results = annotate_results_with_favorites(foldered_lookup.get(folder_key, []), settings["favorite_websites"])
        count = len(results)
        foldered = False
    else:
        use_folders = service._should_folder_results(query, grouped_results)
        results = (
            foldered_results
            if use_folders
            else sort_results_for_refresh(grouped_results, settings["favorite_websites"])
        )
        count = len(results)
        foldered = use_folders

    safe_limit = max(1, int(limit or 10))
    return {
        "query": query,
        "foldered": foldered,
        "count": count,
        "results": results[:safe_limit],
    }


def build_search_quality_pool(
    service: FinPortfolioWebService,
    queries: list[dict[str, str]],
    *,
    top_k: int = 10,
    include_group_children: bool = True,
    include_folder_contents: bool = True,
    qrels: Optional[dict[str, dict[str, int]]] = None,
) -> list[dict[str, Any]]:
    qrels = qrels or {}
    pool_rows: list[dict[str, Any]] = []

    for query in queries:
        payload = web_search_surface_results(service, query["query"], limit=top_k)
        candidates: list[dict[str, Any]] = []

        if include_folder_contents and payload.get("foldered"):
            for folder_index, folder in enumerate(payload.get("results", []) or [], start=1):
                if not isinstance(folder, dict):
                    continue
                folder_key = str(folder.get("folder_key", "") or "")
                if not folder_key:
                    continue
                folder_payload = web_search_surface_results(service, query["query"], limit=top_k, folder_key=folder_key)
                candidates.extend(
                    collect_document_results(
                        list(folder_payload.get("results", []) or []),
                        include_group_children=include_group_children,
                        folder_key=folder_key,
                        folder_title=str(folder.get("folder_title", "") or ""),
                        folder_rank=folder_index,
                    )
                )
        else:
            candidates.extend(
                collect_document_results(
                    list(payload.get("results", []) or []),
                    include_group_children=include_group_children,
                )
            )

        deduped: dict[str, dict[str, Any]] = {}
        for candidate in sorted(candidates, key=_row_sort_key):
            doc_id = str(candidate.get("doc_id", "") or "")
            if not doc_id or doc_id in deduped:
                continue
            deduped[doc_id] = candidate

        for rank, result in enumerate(deduped.values(), start=1):
            pool_rows.append(
                _pool_row(
                    query,
                    result,
                    rank=rank,
                    surface_rank=int(result.get("_surface_rank", 0) or 0),
                    surface_kind=str(result.get("_surface_kind", result.get("result_kind", "document")) or "document"),
                    folder_key=str(result.get("_folder_key", "") or ""),
                    folder_title=str(result.get("_folder_title", "") or ""),
                    qrels=qrels,
                )
            )

    return pool_rows


def write_csv(path: Union[str, Path], rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_run(path: Union[str, Path], rows: list[dict[str, Any]], *, method: str) -> None:
    run_rows = [
        {
            "query_id": row["query_id"],
            "doc_id": row["doc_id"],
            "rank": row["rank"],
            "score": row.get("score", ""),
            "method": method,
        }
        for row in rows
    ]
    write_csv(path, run_rows, RUN_FIELDS)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build a web-search human relevance annotation pool.")
    parser.add_argument(
        "--queries",
        default=str(ROOT / "data" / "annotations" / "search_quality_queries_v1.csv"),
        help="CSV with query_id and query columns.",
    )
    parser.add_argument("--output", required=True, help="Output annotation pool CSV.")
    parser.add_argument("--run-output", default="", help="Optional run CSV for evaluate_ir_metrics.py.")
    parser.add_argument("--qrels", default="", help="Optional existing qrels CSV to prefill existing_relevance.")
    parser.add_argument("--documents", default=str(FULL_DOCUMENTS_PATH), help="Document JSONL used by web_app.")
    parser.add_argument("--search-index", default=str(SEARCH_INDEX_PATH), help="SQLite search index used by web_app.")
    parser.add_argument("--top-k", type=int, default=10, help="Visible rows to inspect per query or folder.")
    parser.add_argument("--method", default="web_search_current", help="Method label for optional run CSV.")
    parser.add_argument(
        "--no-group-children",
        action="store_true",
        help="Only include group leaders, not the five visible older documents inside each group.",
    )
    parser.add_argument(
        "--no-folder-contents",
        action="store_true",
        help="Do not open top-level folders; annotate only visible top-level document rows.",
    )
    args = parser.parse_args(argv)

    qrels = load_qrels(args.qrels) if args.qrels else None
    service = FinPortfolioWebService(documents_path=Path(args.documents), search_index_path=Path(args.search_index))
    rows = build_search_quality_pool(
        service,
        load_search_quality_queries(args.queries),
        top_k=args.top_k,
        include_group_children=not args.no_group_children,
        include_folder_contents=not args.no_folder_contents,
        qrels=qrels,
    )
    write_csv(args.output, rows, POOL_FIELDS)
    if args.run_output:
        write_run(args.run_output, rows, method=args.method)
    print(f"wrote_annotation_rows={len(rows)} output={args.output}")
    if args.run_output:
        print(f"wrote_run_rows={len(rows)} run_output={args.run_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
