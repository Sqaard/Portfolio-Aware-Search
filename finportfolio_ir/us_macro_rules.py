"""Deterministic US macro dashboard rules for the FinPortfolio IR UI layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


Tone = str


@dataclass(frozen=True)
class MacroCard:
    card_id: str
    title: str
    tone: Tone
    summary: str
    key_metrics: dict[str, float | None]
    collapsed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "card_id": self.card_id,
            "title": self.title,
            "tone": self.tone,
            "summary": self.summary,
            "key_metrics": self.key_metrics,
            "collapsed": self.collapsed,
        }


def _num(snapshot: Mapping[str, Any], key: str, default: float | None = None) -> float | None:
    value = snapshot.get(key, default)
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _tone(score: float, positive_at: float = 0.35, negative_at: float = -0.35) -> Tone:
    if score >= positive_at:
        return "positive"
    if score <= negative_at:
        return "negative"
    return "neutral"


def _real_yield_proxy(snapshot: Mapping[str, Any]) -> float | None:
    explicit = _num(snapshot, "real_10y_yield")
    if explicit is not None:
        return explicit
    nominal = _num(snapshot, "ten_year_treasury_yield")
    breakeven = _num(snapshot, "ten_year_breakeven")
    if nominal is not None and breakeven is not None:
        return nominal - breakeven
    return None


def fed_real_yields_card(snapshot: Mapping[str, Any]) -> MacroCard:
    real_yield = _real_yield_proxy(snapshot)
    credit_spread = _num(snapshot, "investment_grade_credit_spread")
    fed_funds = _num(snapshot, "fed_funds_rate")

    score = 0.0
    if real_yield is not None:
        score += -0.45 if real_yield > 1.75 else 0.25 if real_yield < 0.75 else -0.10
    if credit_spread is not None:
        score += -0.35 if credit_spread > 1.65 else 0.20 if credit_spread < 1.10 else 0.0
    if fed_funds is not None:
        score += -0.15 if fed_funds > 5.0 else 0.10 if fed_funds < 3.5 else 0.0

    tone = _tone(score)
    summary = {
        "positive": "Easier real yields and contained credit spreads support duration-sensitive equities and credit creation.",
        "neutral": "Rates and credit are mixed, so valuation discipline matters more than broad beta exposure.",
        "negative": "Restrictive real yields or wider credit spreads pressure equity multiples, banks, and leveraged balance sheets.",
    }[tone]
    return MacroCard(
        card_id="fed_real_yields_credit",
        title="Fed, Real Yields And Credit",
        tone=tone,
        summary=summary,
        key_metrics={
            "fed_funds_rate": fed_funds,
            "real_10y_yield": real_yield,
            "investment_grade_credit_spread": credit_spread,
        },
    )


def growth_jobs_consumer_card(snapshot: Mapping[str, Any]) -> MacroCard:
    payrolls = _num(snapshot, "payrolls_3m_avg")
    unemployment_change = _num(snapshot, "unemployment_3m_change")
    retail_sales = _num(snapshot, "retail_sales_yoy")
    ism_new_orders = _num(snapshot, "ism_new_orders")

    score = 0.0
    if payrolls is not None:
        score += 0.30 if payrolls >= 175_000 else -0.30 if payrolls < 75_000 else 0.05
    if unemployment_change is not None:
        score += -0.35 if unemployment_change >= 0.35 else 0.20 if unemployment_change <= 0.05 else -0.05
    if retail_sales is not None:
        score += 0.25 if retail_sales > 2.0 else -0.25 if retail_sales < 0.0 else 0.0
    if ism_new_orders is not None:
        score += 0.20 if ism_new_orders >= 52.0 else -0.20 if ism_new_orders < 48.0 else 0.0

    tone = _tone(score)
    summary = {
        "positive": "Labor income and demand still support revenues for consumer, industrial, and financial cyclicals.",
        "neutral": "Growth signals are not decisive, so the portfolio should lean on company-specific evidence.",
        "negative": "Weak labor or demand momentum raises revenue risk for cyclicals and credit-sensitive businesses.",
    }[tone]
    return MacroCard(
        card_id="growth_jobs_consumer",
        title="Growth, Jobs And Consumer Demand",
        tone=tone,
        summary=summary,
        key_metrics={
            "payrolls_3m_avg": payrolls,
            "unemployment_3m_change": unemployment_change,
            "retail_sales_yoy": retail_sales,
            "ism_new_orders": ism_new_orders,
        },
    )


def earnings_dollar_risk_card(snapshot: Mapping[str, Any]) -> MacroCard:
    earnings_revision = _num(snapshot, "sp500_earnings_revision_3m")
    dxy_yoy = _num(snapshot, "dxy_yoy")
    vix = _num(snapshot, "vix")
    oil_yoy = _num(snapshot, "wti_yoy")

    score = 0.0
    if earnings_revision is not None:
        score += 0.35 if earnings_revision > 0.02 else -0.35 if earnings_revision < -0.02 else 0.0
    if dxy_yoy is not None:
        score += -0.20 if dxy_yoy > 5.0 else 0.10 if dxy_yoy < -3.0 else 0.0
    if vix is not None:
        score += -0.30 if vix > 25.0 else 0.20 if vix < 16.0 else 0.0
    if oil_yoy is not None:
        score += -0.10 if oil_yoy > 35.0 else 0.05 if -20.0 < oil_yoy < 20.0 else 0.0

    tone = _tone(score)
    summary = {
        "positive": "Earnings revisions and risk appetite support equity exposure, while dollar pressure is manageable.",
        "neutral": "Earnings, FX, and volatility do not point in one direction; diversify evidence before acting.",
        "negative": "Falling revisions, a strong dollar, or high volatility increase risk for broad US equity exposure.",
    }[tone]
    return MacroCard(
        card_id="earnings_dollar_risk",
        title="Earnings, Dollar And Risk Appetite",
        tone=tone,
        summary=summary,
        key_metrics={
            "sp500_earnings_revision_3m": earnings_revision,
            "dxy_yoy": dxy_yoy,
            "vix": vix,
            "wti_yoy": oil_yoy,
        },
    )


def build_us_macro_dashboard(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    what_matters = [
        fed_real_yields_card(snapshot),
        growth_jobs_consumer_card(snapshot),
        earnings_dollar_risk_card(snapshot),
    ]
    return {
        "language": "en",
        "what_matters_cards": [card.to_dict() for card in what_matters],
        "market_regime": infer_market_regime(snapshot),
    }


def infer_market_regime(snapshot: Mapping[str, Any]) -> str:
    cards = [
        fed_real_yields_card(snapshot),
        growth_jobs_consumer_card(snapshot),
        earnings_dollar_risk_card(snapshot),
    ]
    score = sum({"positive": 1, "neutral": 0, "negative": -1}[card.tone] for card in cards)
    if score >= 2:
        return "risk_on"
    if score <= -2:
        return "risk_off"
    return "transition"


def build_macro_portfolio_translation(
    snapshot: Mapping[str, Any],
    sector_weights: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    sector_weights = {str(key).lower(): float(value) for key, value in (sector_weights or {}).items()}
    rates = fed_real_yields_card(snapshot)
    growth = growth_jobs_consumer_card(snapshot)
    earnings = earnings_dollar_risk_card(snapshot)
    quality_growth_weight = (
        sector_weights.get("technology", 0.0)
        + sector_weights.get("information technology", 0.0)
        + sector_weights.get("communication services", 0.0)
    )
    banks_weight = sector_weights.get("financials", 0.0)
    cyclicals_weight = (
        sector_weights.get("industrials", 0.0)
        + sector_weights.get("consumer discretionary", 0.0)
        + sector_weights.get("energy", 0.0)
    )
    cards = [
        {
            "card_id": "quality_growth_real_yields",
            "title": "Quality Growth",
            "tone": "positive" if rates.tone == "positive" else "negative" if rates.tone == "negative" and quality_growth_weight > 0.20 else "neutral",
            "summary": rates.summary,
            "portfolio_weight": round(quality_growth_weight, 6),
            "collapsed": True,
        },
        {
            "card_id": "banks_credit_cycle",
            "title": "Banks",
            "tone": "positive" if rates.tone == "positive" and growth.tone != "negative" else "negative" if rates.tone == "negative" and banks_weight > 0.10 else "neutral",
            "summary": "Banks benefit from healthy credit demand but suffer when funding costs, losses, or spreads deteriorate.",
            "portfolio_weight": round(banks_weight, 6),
            "collapsed": True,
        },
        {
            "card_id": "cyclicals_energy_consumer",
            "title": "Cyclicals",
            "tone": "positive" if growth.tone == "positive" and earnings.tone != "negative" else "negative" if growth.tone == "negative" and cyclicals_weight > 0.15 else "neutral",
            "summary": growth.summary,
            "portfolio_weight": round(cyclicals_weight, 6),
            "collapsed": True,
        },
    ]
    return {
        "language": "en",
        "legend": {
            "positive": "positive portfolio impact",
            "neutral": "neutral or mixed portfolio impact",
            "negative": "negative portfolio impact",
        },
        "cards": cards,
    }
