"""Favorite website ranking semantics for search and My Vibe."""

from __future__ import annotations

import urllib.parse
from typing import Any, Iterable, Mapping, Sequence

from crawler.source_registry import canonicalize_url


def favorite_key(url: str) -> str:
    canonical = canonicalize_url(url)
    parsed = urllib.parse.urlparse(canonical)
    return parsed.netloc.lower().removeprefix("www.")


def normalize_favorite_websites(urls: Iterable[str]) -> list[str]:
    keys = {favorite_key(url) for url in urls if favorite_key(url)}
    return sorted(keys)


def _result_key(result: Mapping[str, Any]) -> str:
    return favorite_key(str(result.get("url") or result.get("canonical_url") or ""))


def _site_name(result: Mapping[str, Any]) -> str:
    explicit = str(result.get("site_name") or result.get("source") or "").strip()
    return explicit.lower() if explicit else _result_key(result)


def annotate_results_with_favorites(
    results: Sequence[Mapping[str, Any]],
    favorite_urls: Iterable[str],
    *,
    pending_removed: Iterable[str] = (),
    pending_added: Iterable[str] = (),
) -> list[dict[str, Any]]:
    favorites = set(normalize_favorite_websites(favorite_urls))
    removed = set(normalize_favorite_websites(pending_removed))
    added = set(normalize_favorite_websites(pending_added))
    annotated: list[dict[str, Any]] = []
    for index, result in enumerate(results):
        row = dict(result)
        key = _result_key(row)
        is_favorite = key in favorites
        row["favorite_site_key"] = key
        row["favorite_sort_name"] = _site_name(row)
        row["favorite_status"] = (
            "pending_removed" if key in removed else "pending_added" if key in added else "favorite" if is_favorite else "not_favorite"
        )
        row["favorite_icon"] = "filled" if is_favorite and key not in removed else "empty"
        row["favorite_highlight"] = is_favorite and key not in removed
        row["_original_index"] = index
        annotated.append(row)
    return annotated


def sort_results_for_refresh(
    results: Sequence[Mapping[str, Any]],
    favorite_urls: Iterable[str],
) -> list[dict[str, Any]]:
    annotated = annotate_results_with_favorites(results, favorite_urls)
    scores = []
    for row in annotated:
        try:
            scores.append(float(row.get("score", 0.0) or 0.0))
        except (TypeError, ValueError):
            scores.append(0.0)
    max_score = max(scores or [0.0])
    promotion_threshold = max_score * 0.60 if max_score > 0.5 else -1.0
    promotable_site_keys = {
        str(row.get("favorite_site_key") or "")
        for row, score in zip(annotated, scores)
        if row.get("favorite_status") == "favorite" and score >= promotion_threshold
    }

    def favorite_can_promote(row: Mapping[str, Any]) -> bool:
        if row.get("favorite_status") != "favorite":
            return False
        return str(row.get("favorite_site_key") or "") in promotable_site_keys

    annotated.sort(
        key=lambda row: (
            0 if favorite_can_promote(row) else 1,
            row["favorite_sort_name"] if favorite_can_promote(row) else "",
            int(row.get("rank", row["_original_index"] + 1)),
            row["_original_index"],
        )
    )
    for rank, row in enumerate(annotated, start=1):
        row["display_rank"] = rank
        row.pop("_original_index", None)
    return annotated


def toggle_favorite_in_place(
    results: Sequence[Mapping[str, Any]],
    favorite_urls: Iterable[str],
    target_url: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    favorites = set(normalize_favorite_websites(favorite_urls))
    target_key = favorite_key(target_url)
    if not target_key:
        return sorted(favorites), annotate_results_with_favorites(results, favorites)

    if target_key in favorites:
        favorites.remove(target_key)
        annotated = annotate_results_with_favorites(results, favorites | {target_key}, pending_removed=[target_key])
    else:
        favorites.add(target_key)
        annotated = annotate_results_with_favorites(results, favorites, pending_added=[target_key])

    for rank, row in enumerate(annotated, start=1):
        row["display_rank"] = rank
        row.pop("_original_index", None)
    return sorted(favorites), annotated
