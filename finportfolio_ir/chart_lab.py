"""Analyst-style chart helpers for the local FinPortfolio IR dashboard."""

from __future__ import annotations

import csv
import math
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


MAX_POINTS_PER_SERIES = 180
TEXT_ROLLING_WINDOW = 63
TEXT_ZERO_DROP_THRESHOLD = 0.985
CHART_WINDOWS = {
    "1y": {"label": "1Y", "days": 365},
    "5y": {"label": "5Y", "days": 365 * 5 + 2},
    "all": {"label": "All", "days": None},
}


@dataclass(frozen=True)
class ChartSeries:
    key: str
    label: str
    kind: str
    unit: str = ""


@dataclass(frozen=True)
class ChartDefinition:
    chart_id: str
    scope: str
    title: str
    description: str
    series: tuple[ChartSeries, ...]


CHART_DEFINITIONS: tuple[ChartDefinition, ...] = (
    ChartDefinition(
        "company_revenue_eps",
        "company",
        "Revenue / EPS Fact Check",
        "Current business momentum: revenue, shareholder economics, and guidance evidence.",
        (
            ChartSeries("rev_q", "Revenue", "structured", "USD"),
            ChartSeries("EPS", "EPS", "structured", "USD/share"),
            ChartSeries("stock_signal_earnings_guidance_count", "Guidance Mentions", "text", "docs"),
        ),
    ),
    ChartDefinition(
        "company_margins",
        "company",
        "Margin And Cost Pressure",
        "Gross-to-net pressure: margins plus cost, inflation, and supply-chain evidence.",
        (
            ChartSeries("OPM", "Operating Margin", "structured", "ratio"),
            ChartSeries("NPM", "Net Margin", "structured", "ratio"),
            ChartSeries("stock_signal_margin_pressure_count", "Margin Warnings", "text", "docs"),
        ),
    ),
    ChartDefinition(
        "company_balance_stress",
        "company",
        "Debt And Liquidity Watch",
        "Leverage, current liquidity, and credit-risk evidence.",
        (
            ChartSeries("debt_ratio", "Debt Ratio", "structured", "ratio"),
            ChartSeries("cur_ratio", "Current Ratio", "structured", "ratio"),
            ChartSeries("stock_signal_credit_count", "Credit Warnings", "text", "docs"),
        ),
    ),
    ChartDefinition(
        "company_filing_risk",
        "company",
        "Filing Red Flags",
        "Risk-factor pressure, legal/regulatory signals, and high-severity filing evidence.",
        (
            ChartSeries("stock_text_max_event_severity", "Event Severity", "text", "score"),
            ChartSeries("stock_signal_company_risk_count", "Company Risk Docs", "text", "docs"),
            ChartSeries("stock_signal_legal_regulatory_count", "Legal / Regulatory Docs", "text", "docs"),
        ),
    ),
    ChartDefinition(
        "company_guidance_events",
        "company",
        "Guidance And Revision Map",
        "Forward-looking statements, sentiment proxy, and profitability revisions.",
        (
            ChartSeries("stock_signal_earnings_guidance_count", "Guidance Mentions", "text", "docs"),
            ChartSeries("stock_text_avg_sentiment_proxy", "Sentiment", "text", "score"),
            ChartSeries("fundrev_profitability_revision_score_lag1", "Profitability Revision", "structured", "score"),
        ),
    ),
    ChartDefinition(
        "macro_rates_pressure",
        "macro",
        "Rates vs Valuation",
        "Fed pressure, Treasury yield level, and rates evidence for the portfolio.",
        (
            ChartSeries("rates_lsc_policy_pressure_score", "Policy Pressure", "structured", "score"),
            ChartSeries("rates_lsc_level_lag1", "Yield Level", "structured", "percent"),
            ChartSeries("portfolio_signal_macro_rates_count", "Rates Evidence", "text", "docs"),
        ),
    ),
    ChartDefinition(
        "macro_credit_stress",
        "macro",
        "Credit Stress Map",
        "Credit spreads, financial conditions, and credit-risk evidence.",
        (
            ChartSeries("credit_stress_regime_score", "Credit Stress", "structured", "score"),
            ChartSeries("credit_baa_spread_lag1", "BAA Spread", "structured", "percent"),
            ChartSeries("portfolio_signal_credit_count", "Credit Evidence", "text", "docs"),
        ),
    ),
    ChartDefinition(
        "macro_volatility",
        "macro",
        "Risk Appetite Gauge",
        "VIX stress and market-risk evidence for the portfolio.",
        (
            ChartSeries("vol_implied_stress_regime_score", "Vol Stress", "structured", "score"),
            ChartSeries("vol_vix_lag1", "VIX", "structured", "index"),
            ChartSeries("portfolio_signal_market_volatility_count", "Volatility Evidence", "text", "docs"),
        ),
    ),
    ChartDefinition(
        "macro_curve_shape",
        "macro",
        "Yield Curve Warning",
        "Treasury curve shape, inversion, and policy-transmission pressure.",
        (
            ChartSeries("rates_lsc_slope_10y_2y_lag1", "2Y/10Y Slope", "structured", "percentage points"),
            ChartSeries("rates_lsc_slope_10y_3mo_lag1", "3M/10Y Slope", "structured", "percentage points"),
            ChartSeries("rates_lsc_curve_inversion_flag_lag1", "Inversion", "structured", "flag"),
        ),
    ),
    ChartDefinition(
        "macro_financial_conditions",
        "macro",
        "Financial Conditions Dashboard",
        "Credit tightness, quality spread, and volatility term structure.",
        (
            ChartSeries("credit_nfci_lag5", "NFCI", "structured", "score"),
            ChartSeries("credit_baa_aaa_quality_spread_lag1", "Quality Spread", "structured", "percent"),
            ChartSeries("vol_term_slope_vxv_vix_lag1", "Vol Term Slope", "structured", "index"),
        ),
    ),
)


FUNDAMENTAL_CHART_DEFINITIONS: tuple[ChartDefinition, ...] = tuple(
    definition
    for definition in CHART_DEFINITIONS
    if sum(1 for series in definition.series if series.kind == "structured") >= 2
)
FUNDAMENTAL_CHART_BY_ID = {definition.chart_id: definition for definition in FUNDAMENTAL_CHART_DEFINITIONS}
CHART_BY_ID = {definition.chart_id: definition for definition in CHART_DEFINITIONS}
CHART_LAB_COLUMNS = sorted({series.key for definition in CHART_DEFINITIONS for series in definition.series})


def chart_lab_options() -> dict[str, Any]:
    return {
        "windows": [
            {"id": key, "label": value["label"]}
            for key, value in CHART_WINDOWS.items()
        ],
        "modes": [
            {"id": "structured", "label": "Fundamentals only"},
        ],
        "charts": [
            {
                "id": definition.chart_id,
                "scope": definition.scope,
                "title": definition.title,
                "description": definition.description,
                "series": [
                    {"key": series.key, "label": series.label, "kind": series.kind, "unit": series.unit}
                    for series in definition.series
                ],
            }
            for definition in FUNDAMENTAL_CHART_DEFINITIONS
        ],
    }


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "inf", "-inf"}:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _downsample(points: list[dict[str, Any]], max_points: int = MAX_POINTS_PER_SERIES) -> list[dict[str, Any]]:
    if len(points) <= max_points:
        return points
    step = max(1, math.ceil(len(points) / max_points))
    sampled = points[::step]
    if sampled[-1]["date"] != points[-1]["date"]:
        sampled.append(points[-1])
    return sampled


def _compress_series(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not points:
        return []
    changed: list[dict[str, Any]] = []
    last_value: float | None = None
    for point in points:
        value = float(point["value"])
        if last_value is None or abs(value - last_value) > 1e-12:
            changed.append(point)
            last_value = value
    if len(changed) >= 8:
        if changed[-1]["date"] != points[-1]["date"]:
            # Fundamentals are often carried forward between filings. The UI
            # and LLM analysis must still see the latest available date, not
            # only the latest date where the value changed.
            changed.append(points[-1])
        return _downsample(changed)
    return _downsample(points)


def _percentile(values: list[float], percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    raw_index = (len(ordered) - 1) * percent
    lower = math.floor(raw_index)
    upper = math.ceil(raw_index)
    if lower == upper:
        return ordered[int(raw_index)]
    weight = raw_index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _scale_value_for_chart(value: float, unit: str) -> float:
    if unit in {"docs", "terms"}:
        return math.log1p(max(0.0, value))
    return value


def _normalize_points(points: list[dict[str, Any]], *, unit: str = "") -> list[dict[str, Any]]:
    if not points:
        return []
    if unit == "flag":
        normalized = []
        for point in points:
            value = 1.0 if float(point["value"]) > 0 else 0.0
            normalized.append({**point, "y": value})
        return normalized
    scaled_values = [_scale_value_for_chart(float(point["value"]), unit) for point in points]
    if len(scaled_values) >= 12:
        minimum = _percentile(scaled_values, 0.03)
        maximum = _percentile(scaled_values, 0.97)
        if maximum - minimum <= 1e-12:
            minimum = min(scaled_values)
            maximum = max(scaled_values)
    else:
        minimum = min(scaled_values)
        maximum = max(scaled_values)
    span = maximum - minimum
    normalized = []
    for point, scaled_value in zip(points, scaled_values):
        clipped = scaled_value < minimum or scaled_value > maximum
        y_value = 0.5 if span <= 1e-12 else (scaled_value - minimum) / span
        normalized.append(
            {
                **point,
                "y": round(max(0.0, min(1.0, y_value)), 6),
                "clipped": clipped,
            }
        )
    return normalized


def _rolling_text_points(points: list[dict[str, Any]], *, sum_mode: bool) -> list[dict[str, Any]]:
    if not points:
        return []
    rolled: list[dict[str, Any]] = []
    window: list[float] = []
    running_sum = 0.0
    for point in points:
        value = float(point["value"])
        window.append(value)
        running_sum += value
        if len(window) > TEXT_ROLLING_WINDOW:
            running_sum -= window.pop(0)
        rolled_value = running_sum if sum_mode else running_sum / max(1, len(window))
        rolled.append({"date": point["date"], "value": round(rolled_value, 6)})
    return rolled


def _monthly_average_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for point in points:
        date = str(point["date"])
        key = date[:7]
        bucket = buckets.setdefault(key, {"date": date, "total": 0.0, "count": 0})
        if date > bucket["date"]:
            bucket["date"] = date
        bucket["total"] += float(point["value"])
        bucket["count"] += 1
    return [
        {"date": bucket["date"], "value": round(bucket["total"] / max(1, bucket["count"]), 6)}
        for key, bucket in sorted(buckets.items())
    ]


def _prepare_series_points(series: ChartSeries, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    raw_points = [
        {"date": str(row["date"]), "value": float(row[series.key])}
        for row in rows
        if series.key in row
    ]
    if not raw_points:
        return [], series.label
    values = [abs(float(point["value"])) for point in raw_points]
    non_zero = sum(1 for value in values if value > 1e-12)
    if non_zero == 0:
        return [], series.label
    label = series.label
    points = raw_points
    if series.kind == "text":
        zero_share = 1.0 - (non_zero / max(1, len(raw_points)))
        if zero_share >= TEXT_ZERO_DROP_THRESHOLD:
            return [], series.label
        count_like = series.unit == "docs" or series.key.endswith("_count")
        points = _rolling_text_points(raw_points, sum_mode=count_like)
        points = _monthly_average_points(points)
        label = f"{series.label} trend" if count_like else f"{series.label} trend"
    return _normalize_points(_compress_series(points), unit=series.unit), label


def _parse_chart_date(value: Any) -> datetime | None:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _normalize_window(window: str) -> str:
    key = str(window or "").lower()
    return key if key in CHART_WINDOWS else "all"


def _filter_rows_by_window(rows: list[dict[str, Any]], window: str) -> list[dict[str, Any]]:
    key = _normalize_window(window)
    days = CHART_WINDOWS[key]["days"]
    if days is None or len(rows) < 2:
        return rows
    latest = None
    for row in reversed(rows):
        latest = _parse_chart_date(row.get("date"))
        if latest is not None:
            break
    if latest is None:
        return rows
    cutoff = latest - timedelta(days=int(days))
    filtered = [row for row in rows if (_parse_chart_date(row.get("date")) or latest) >= cutoff]
    return filtered or rows[-1:]


class ChartLabStore:
    def __init__(self, panel_path: Path) -> None:
        self.panel_path = Path(panel_path)
        self._lock = threading.RLock()
        self._mtime_ns = -1
        self._ticker_rows: dict[str, list[dict[str, Any]]] = {}
        self._market_rows: list[dict[str, Any]] = []
        self._available_columns: set[str] = set()

    def _load_if_needed(self) -> None:
        with self._lock:
            try:
                stat = self.panel_path.stat()
            except FileNotFoundError:
                self._mtime_ns = -1
                self._ticker_rows = {}
                self._market_rows = []
                self._available_columns = set()
                return
            if self._mtime_ns == stat.st_mtime_ns and self._ticker_rows:
                return
            ticker_rows: dict[str, list[dict[str, Any]]] = {}
            market_by_date: dict[str, dict[str, Any]] = {}
            available_columns: set[str] = set()
            with self.panel_path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames:
                    available_columns = set(reader.fieldnames)
                wanted = [column for column in CHART_LAB_COLUMNS if column in available_columns]
                for row in reader:
                    date = str(row.get("date", "") or "").strip()
                    ticker = str(row.get("tic", "") or "").strip().upper()
                    if not date or not ticker:
                        continue
                    record: dict[str, Any] = {"date": date, "ticker": ticker}
                    has_value = False
                    for column in wanted:
                        value = _float_or_none(row.get(column))
                        if value is not None:
                            record[column] = value
                            has_value = True
                    if not has_value:
                        continue
                    ticker_rows.setdefault(ticker, []).append(record)
                    market_by_date.setdefault(date, record)
            for rows in ticker_rows.values():
                rows.sort(key=lambda item: str(item["date"]))
            self._ticker_rows = ticker_rows
            self._market_rows = [market_by_date[date] for date in sorted(market_by_date)]
            self._available_columns = available_columns
            self._mtime_ns = stat.st_mtime_ns

    def warmup(self) -> None:
        try:
            self._load_if_needed()
        except Exception:
            return

    def payload(self, *, ticker: str, chart_id: str, mode: str = "structured", window: str = "all") -> dict[str, Any]:
        self._load_if_needed()
        definition = FUNDAMENTAL_CHART_BY_ID.get(chart_id) or FUNDAMENTAL_CHART_DEFINITIONS[0]
        ticker = str(ticker or "").upper()
        mode = "structured"
        window = _normalize_window(window)
        source_rows = self._market_rows if definition.scope == "macro" else self._ticker_rows.get(ticker, [])
        windowed_rows = _filter_rows_by_window(source_rows, window)
        selected_series = [
            series
            for series in definition.series
            if series.kind == "structured"
        ]
        series_payload: list[dict[str, Any]] = []
        for series in selected_series:
            if series.key not in self._available_columns:
                continue
            points, display_label = _prepare_series_points(series, windowed_rows)
            if not points:
                continue
            series_payload.append(
                {
                    "key": series.key,
                    "label": display_label,
                    "base_label": series.label,
                    "kind": series.kind,
                    "unit": series.unit,
                    "latest": points[-1],
                    "points": points,
                }
            )
        return {
            "ticker": ticker,
            "chart_id": definition.chart_id,
            "mode": mode,
            "window": window,
            "window_label": str(CHART_WINDOWS[window]["label"]),
            "scope": definition.scope,
            "title": definition.title,
            "description": definition.description,
            "series": series_payload,
            "available": bool(series_payload),
            "source": {
                "panel_path": str(self.panel_path),
                "available_columns": sorted(self._available_columns & set(CHART_LAB_COLUMNS)),
                "ticker_row_count": len(source_rows),
                "window_row_count": len(windowed_rows),
            },
        }
