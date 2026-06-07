"""Portfolio settings and summary helpers for the future dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from finportfolio_ir.dow30 import DOW30_SECTOR_BY_TICKER, DOW30_TICKER_SET


DEFAULT_SECTOR_BY_TICKER = dict(DOW30_SECTOR_BY_TICKER)


@dataclass(frozen=True)
class PortfolioPosition:
    ticker: str
    purchase_price: float
    quantity: float
    current_price: float | None = None
    sector: str = "Unknown"

    @property
    def market_price(self) -> float:
        return self.current_price if self.current_price is not None else self.purchase_price

    @property
    def market_value(self) -> float:
        return max(0.0, self.market_price * self.quantity)


def parse_portfolio_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    price_lookup: Mapping[str, float] | None = None,
    sector_lookup: Mapping[str, str] | None = None,
) -> list[PortfolioPosition]:
    price_lookup = {str(k).upper(): float(v) for k, v in (price_lookup or {}).items()}
    sector_lookup = {**DEFAULT_SECTOR_BY_TICKER, **{str(k).upper(): str(v) for k, v in (sector_lookup or {}).items()}}
    positions: list[PortfolioPosition] = []
    for row in rows:
        ticker = str(row.get("ticker", "") or "").strip().upper()
        if not ticker or ticker not in DOW30_TICKER_SET:
            continue
        try:
            purchase_price = float(row.get("purchase_price", row.get("price", 0.0)) or 0.0)
            quantity = float(row.get("quantity", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if purchase_price <= 0 or quantity <= 0:
            continue
        current_price = row.get("current_price")
        if current_price is None and ticker in price_lookup:
            current_price = price_lookup[ticker]
        positions.append(
            PortfolioPosition(
                ticker=ticker,
                purchase_price=purchase_price,
                quantity=quantity,
                current_price=float(current_price) if current_price is not None else None,
                sector=sector_lookup.get(ticker, "Unknown"),
            )
        )
    return positions


def summarize_portfolio(
    rows: Sequence[Mapping[str, Any]],
    *,
    price_lookup: Mapping[str, float] | None = None,
    sector_lookup: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    positions = parse_portfolio_rows(rows, price_lookup=price_lookup, sector_lookup=sector_lookup)
    total_value = sum(position.market_value for position in positions)
    weighted = [
        {
            "ticker": position.ticker,
            "sector": position.sector,
            "purchase_price": position.purchase_price,
            "quantity": position.quantity,
            "current_price": position.current_price,
            "market_value": position.market_value,
            "weight": (position.market_value / total_value) if total_value else 0.0,
        }
        for position in positions
    ]
    weighted.sort(key=lambda item: (-float(item["weight"]), str(item["ticker"])))
    sector_weights: dict[str, float] = {}
    for item in weighted:
        sector = str(item["sector"])
        sector_weights[sector] = sector_weights.get(sector, 0.0) + float(item["weight"])
    top1 = float(weighted[0]["weight"]) if weighted else 0.0
    top2 = sum(float(item["weight"]) for item in weighted[:2])
    dominant_sector = max(sector_weights.items(), key=lambda item: item[1])[0] if sector_weights else "None"
    concentration = "high" if top1 >= 0.30 or top2 >= 0.50 else "moderate" if top1 >= 0.20 else "balanced"
    narrative = _portfolio_narrative(concentration, dominant_sector, sector_weights)
    summary_points = _portfolio_summary_points(concentration, dominant_sector, sector_weights)
    return {
        "language": "en",
        "total_value": round(total_value, 2),
        "positions": len(weighted),
        "top1_weight": round(top1, 6),
        "top2_weight": round(top2, 6),
        "dominant_block": dominant_sector,
        "concentration": concentration,
        "sector_weights": {key: round(value, 6) for key, value in sorted(sector_weights.items())},
        "holdings": weighted,
        "narrative": narrative,
        "summary_points": summary_points,
    }


def _portfolio_narrative(concentration: str, dominant_sector: str, sector_weights: Mapping[str, float]) -> str:
    if dominant_sector == "None":
        return "Add Dow 30 positions to activate portfolio-aware retrieval."
    return f"{concentration.capitalize()} concentration; dominant exposure is {dominant_sector}."


def _portfolio_summary_points(concentration: str, dominant_sector: str, sector_weights: Mapping[str, float]) -> list[str]:
    if dominant_sector == "None":
        return ["Add Dow 30 positions to activate portfolio-aware retrieval."]
    points = []
    if concentration == "high":
        points.append("High concentration: single-name evidence deserves extra weight.")
    elif concentration == "moderate":
        points.append("Moderate concentration: watch top holdings, but no single name dominates.")
    else:
        points.append("Balanced position sizing by current market value.")
    points.append(f"Dominant exposure: {dominant_sector}.")
    if sector_weights.get("Information Technology", 0.0) + sector_weights.get("Communication Services", 0.0) > 0.35:
        points.append("Watch real yields and earnings revisions.")
    if sector_weights.get("Financials", 0.0) > 0.20:
        points.append("Watch credit spreads, funding costs, and loan demand.")
    if sector_weights.get("Energy", 0.0) > 0.15:
        points.append("Watch oil, the dollar, and global demand.")
    return points[:3]
