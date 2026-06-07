"""My Vibe feed contracts and privacy-safe LLM prompt helpers."""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence


def _portfolio_terms(portfolio: Mapping[str, Any]) -> set[str]:
    terms: set[str] = set()
    for holding in portfolio.get("holdings", []) or []:
        if isinstance(holding, Mapping):
            for key in ("ticker", "name", "sector"):
                value = str(holding.get(key, "") or "").strip()
                if value:
                    terms.add(value.lower())
    return terms


def post_portfolio_relevance(post: Mapping[str, Any], portfolio: Mapping[str, Any]) -> float:
    text = " ".join(str(post.get(key, "") or "") for key in ("title", "summary", "author", "text", "body")).lower()
    if not text:
        return 0.0
    score = 0.0
    for term in _portfolio_terms(portfolio):
        if re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text):
            score += 1.0 if len(term) <= 5 else 0.5
    event_terms = ("earnings", "guidance", "rates", "inflation", "credit", "lawsuit", "regulation", "margin", "demand")
    score += 0.15 * sum(1 for term in event_terms if term in text)
    return round(score, 6)


def sort_posts_for_my_vibe(
    posts: Sequence[Mapping[str, Any]],
    portfolio: Mapping[str, Any],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for index, post in enumerate(posts):
        row = post_for_ui(post)
        row["portfolio_relevance_score"] = post_portfolio_relevance(post, portfolio)
        row["_original_index"] = index
        enriched.append(row)
    enriched.sort(key=lambda row: (-float(row["portfolio_relevance_score"]), str(row.get("published_at", "")), row["_original_index"]))
    for row in enriched:
        row.pop("_original_index", None)
    return enriched


def post_for_ui(post: Mapping[str, Any]) -> dict[str, Any]:
    """Return a UI-safe post without the full text body."""
    return {
        "id": str(post.get("id", "") or post.get("url", "")),
        "site": str(post.get("site", "") or post.get("source", "")),
        "title": str(post.get("title", "")),
        "author": str(post.get("author", "")),
        "published_at": str(post.get("published_at", "")),
        "url": str(post.get("url", "")),
        "summary": str(post.get("summary", ""))[:500],
        "text_char_count": len(str(post.get("text", post.get("body", "")) or "")),
    }


def build_portfolio_impact_prompt(
    post: Mapping[str, Any],
    portfolio: Mapping[str, Any],
    macro_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    post_text = str(post.get("text", post.get("body", "")) or "")
    compact_holdings = [
        {
            "ticker": str(holding.get("ticker", "")).upper(),
            "weight": holding.get("weight"),
            "sector": holding.get("sector"),
        }
        for holding in portfolio.get("holdings", []) or []
        if isinstance(holding, Mapping)
    ]
    instruction = (
        "Analyze how the selected post may affect the current US equity portfolio. "
        "Do not recommend trades. Return: short conclusion, affected holdings table, "
        "what changed, checks before action, confidence, and reasons that could falsify the view."
    )
    return {
        "instruction": instruction,
        "post": {
            "title": str(post.get("title", "")),
            "url": str(post.get("url", "")),
            "text": post_text,
            "text_char_count": len(post_text),
        },
        "portfolio": {
            "summary": portfolio.get("summary", {}),
            "holdings": compact_holdings[:40],
        },
        "macro_snapshot": dict(macro_snapshot or {}),
    }
