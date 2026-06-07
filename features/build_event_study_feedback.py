"""Build event-study feedback for FinIR text extraction labels.

This is an offline validation layer. It must not be merged into PPO features:
post-event returns are future information. The goal is to compare extractor
labels against realized market reaction and to prioritize human/model review.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


DIRECTION_VALUES = {"positive", "negative", "neutral", "mixed"}


def _safe_float(value: Any, default: float | None = 0.0) -> float | None:
    try:
        if value in ("", None):
            return default
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(parsed) or math.isinf(parsed):
        return default
    return parsed


def _safe_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_direction(value: Any) -> str:
    direction = str(value or "").strip().lower()
    return direction if direction in DIRECTION_VALUES else "neutral"


def _direction_from_return(value: float | None, threshold: float) -> str:
    if value is None:
        return "missing"
    if value >= threshold:
        return "positive"
    if value <= -threshold:
        return "negative"
    return "neutral"


def _direction_match(predicted: str, realized: str) -> int | None:
    predicted = _normalize_direction(predicted)
    if realized == "missing":
        return None
    if predicted == "mixed":
        return int(realized != "neutral")
    return int(predicted == realized)


def _cum_return_from_daily(values: Iterable[float]) -> float | None:
    product = 1.0
    seen = False
    for value in values:
        if value is None or math.isnan(value):
            continue
        product *= 1.0 + value
        seen = True
    if not seen:
        return None
    return product - 1.0


class Panel:
    def __init__(self, rows: list[dict[str, str]]) -> None:
        by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_date_returns: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            ticker = str(row.get("tic", "")).strip().upper()
            row_date = str(row.get("date", "")).strip()
            close = _safe_float(row.get("close"), None)
            daily_return = _safe_float(row.get("daily_return"), None)
            if not ticker or not row_date or close is None:
                continue
            item = {
                "date": row_date,
                "close": close,
                "daily_return": daily_return if daily_return is not None else 0.0,
                "volume": _safe_float(row.get("volume"), None),
            }
            by_ticker[ticker].append(item)
            if daily_return is not None:
                by_date_returns[row_date].append(daily_return)
        self.by_ticker = {ticker: sorted(items, key=lambda item: item["date"]) for ticker, items in by_ticker.items()}
        self.index_by_ticker = {
            ticker: {item["date"]: idx for idx, item in enumerate(items)}
            for ticker, items in self.by_ticker.items()
        }
        dates = sorted(by_date_returns)
        self.market_returns = {
            row_date: sum(values) / len(values)
            for row_date, values in by_date_returns.items()
            if values
        }
        self.dates = dates

    def first_trading_date_on_or_after(self, target: date) -> str | None:
        target_text = target.isoformat()
        for row_date in self.dates:
            if row_date >= target_text:
                return row_date
        return None

    def event_index(self, ticker: str, event_date: str) -> tuple[list[dict[str, Any]], int] | None:
        items = self.by_ticker.get(ticker.upper())
        if not items:
            return None
        indices = self.index_by_ticker.get(ticker.upper(), {})
        if event_date in indices:
            return items, indices[event_date]
        parsed = _safe_date(event_date)
        if parsed is None:
            return None
        actual = self.first_trading_date_on_or_after(parsed)
        if actual is None or actual not in indices:
            return None
        return items, indices[actual]

    def ticker_return(self, ticker: str, event_date: str, start_offset: int, end_offset: int) -> float | None:
        located = self.event_index(ticker, event_date)
        if not located:
            return None
        items, event_idx = located
        start_idx = event_idx + start_offset
        end_idx = event_idx + end_offset
        if start_idx < 0 or end_idx < 0 or start_idx >= len(items) or end_idx >= len(items):
            return None
        if start_offset == end_offset:
            return _safe_float(items[end_idx].get("daily_return"), None)
        start_close = _safe_float(items[start_idx].get("close"), None)
        end_close = _safe_float(items[end_idx].get("close"), None)
        if start_close in (None, 0.0) or end_close is None:
            return None
        return end_close / start_close - 1.0

    def market_return(self, event_date: str, start_offset: int, end_offset: int) -> float | None:
        parsed = _safe_date(event_date)
        if parsed is None:
            return None
        actual = self.first_trading_date_on_or_after(parsed)
        if actual is None:
            return None
        try:
            event_idx = self.dates.index(actual)
        except ValueError:
            return None
        start_idx = event_idx + start_offset
        end_idx = event_idx + end_offset
        if start_idx < 0 or end_idx < 0 or start_idx >= len(self.dates) or end_idx >= len(self.dates):
            return None
        if start_offset == end_offset:
            return self.market_returns.get(self.dates[end_idx])
        # Close-to-close from event+start to event+end equals compounded daily
        # returns over start+1..end for positive windows and start+1..end for
        # pre windows where end is before the event.
        lo, hi = sorted((start_idx + 1, end_idx))
        if start_idx > end_idx:
            lo, hi = end_idx + 1, start_idx
        values = [self.market_returns.get(self.dates[idx]) for idx in range(lo, hi + 1)]
        values = [value for value in values if value is not None]
        if not values:
            return None
        result = _cum_return_from_daily(values)
        if result is None:
            return None
        return -result if start_idx > end_idx else result


def _labels_from_seed(row: dict[str, Any]) -> dict[str, Any]:
    labels = row.get("labels") if isinstance(row.get("labels"), dict) else {}
    return {
        "impact_direction": labels.get("impact_direction", ""),
        "risk_intensity": labels.get("risk_intensity", ""),
        "uncertainty_intensity": labels.get("uncertainty_intensity", ""),
        "sentiment_proxy": labels.get("sentiment_proxy", ""),
        "portfolio_action_relevance": labels.get("portfolio_action_relevance", ""),
        "active_signals": "|".join(labels.get("active_signals", [])) if isinstance(labels.get("active_signals"), list) else labels.get("active_signals", ""),
    }


def _labels_from_comparison(row: dict[str, str], prefix: str) -> dict[str, Any]:
    if prefix == "codex":
        return {
            "impact_direction": row.get("gold_impact_direction", ""),
            "risk_intensity": row.get("gold_risk_intensity", ""),
            "uncertainty_intensity": row.get("gold_uncertainty_intensity", ""),
            "sentiment_proxy": row.get("gold_sentiment_proxy", ""),
            "portfolio_action_relevance": row.get("gold_portfolio_action_relevance", ""),
            "active_signals": row.get("gold_active_signals", ""),
        }
    return {
        "impact_direction": row.get("mistral_impact_direction", ""),
        "risk_intensity": row.get("mistral_risk_intensity", ""),
        "uncertainty_intensity": row.get("mistral_uncertainty_intensity", ""),
        "sentiment_proxy": row.get("mistral_sentiment_proxy", ""),
        "portfolio_action_relevance": row.get("mistral_portfolio_action_relevance", ""),
        "active_signals": row.get("mistral_active_signals", ""),
    }


def _event_date(row: dict[str, Any], panel: Panel, policy: str) -> str | None:
    if policy == "available_at_first_trading_day":
        parsed = _safe_date(row.get("available_at"))
        return panel.first_trading_date_on_or_after(parsed) if parsed else None
    decision = str(row.get("decision_date") or "").strip()
    if decision:
        parsed = _safe_date(decision)
        return panel.first_trading_date_on_or_after(parsed) if parsed else decision
    parsed = _safe_date(row.get("available_at"))
    return panel.first_trading_date_on_or_after(parsed) if parsed else None


def _add_return_fields(output: dict[str, Any], panel: Panel, ticker: str, event_date: str, threshold: float) -> None:
    windows = {
        "pre_10d": (-10, -1),
        "pre_5d": (-5, -1),
        "event_day": (0, 0),
        "post_1d": (0, 1),
        "post_3d": (0, 3),
        "post_5d": (0, 5),
        "post_10d": (0, 10),
        "post_21d": (0, 21),
    }
    target_is_portfolio = ticker.upper() in {"PORTFOLIO", "MARKET", ""}
    for name, (start, end) in windows.items():
        market_ret = panel.market_return(event_date, start, end)
        stock_ret = market_ret if target_is_portfolio else panel.ticker_return(ticker, event_date, start, end)
        abnormal = None if target_is_portfolio or stock_ret is None or market_ret is None else stock_ret - market_ret
        output[f"{name}_return"] = "" if stock_ret is None else round(stock_ret, 8)
        output[f"{name}_market_return"] = "" if market_ret is None else round(market_ret, 8)
        output[f"{name}_abnormal_return"] = "" if abnormal is None else round(abnormal, 8)
        label_value = market_ret if target_is_portfolio else abnormal
        output[f"{name}_reaction_label"] = _direction_from_return(label_value, threshold)


def build_event_study(
    *,
    base_panel: Path,
    seed_path: Path,
    comparison_path: Path | None,
    output_dir: Path,
    event_date_policy: str,
    reaction_threshold: float,
) -> dict[str, Any]:
    panel = Panel(_read_csv(base_panel))
    seed_rows = _read_jsonl(seed_path)
    comparison_by_id = {
        str(row.get("teacher_id", "")): row
        for row in (_read_csv(comparison_path) if comparison_path and comparison_path.exists() else [])
    }

    output_rows: list[dict[str, Any]] = []
    skipped = Counter()
    for row in seed_rows:
        teacher_id = str(row.get("teacher_id", ""))
        ticker = str(row.get("target_ticker") or row.get("tic") or "").strip().upper()
        if not ticker:
            skipped["missing_ticker"] += 1
            continue
        event_date = _event_date(row, panel, event_date_policy)
        if not event_date:
            skipped["missing_event_date"] += 1
            continue
        comparison = comparison_by_id.get(teacher_id, {})
        codex = _labels_from_comparison(comparison, "codex") if comparison else _labels_from_seed(row)
        mistral = _labels_from_comparison(comparison, "mistral") if comparison else {}
        output = {
            "teacher_id": teacher_id,
            "doc_id": row.get("doc_id", ""),
            "daily_context_id": row.get("daily_context_id", ""),
            "target_ticker": ticker,
            "event_date": event_date,
            "decision_date": row.get("decision_date", ""),
            "available_at": row.get("available_at", ""),
            "document_split": row.get("document_split", row.get("split", "")),
            "regime": row.get("regime", ""),
            "retrieval_layer": row.get("retrieval_layer", ""),
            "source_family": row.get("source_family", comparison.get("source_family", "")),
            "source_type": row.get("source_type", comparison.get("source_type", "")),
            "source": row.get("source", comparison.get("source", "")),
            "query_intent_primary": row.get("query_intent_primary", ""),
            "title": row.get("title", ""),
            "codex_impact_direction": _normalize_direction(codex.get("impact_direction")),
            "codex_risk_intensity": codex.get("risk_intensity", ""),
            "codex_uncertainty_intensity": codex.get("uncertainty_intensity", ""),
            "codex_sentiment_proxy": codex.get("sentiment_proxy", ""),
            "codex_action_relevance": codex.get("portfolio_action_relevance", ""),
            "codex_active_signals": codex.get("active_signals", ""),
        }
        if mistral:
            output.update(
                {
                    "mistral_impact_direction": _normalize_direction(mistral.get("impact_direction")),
                    "mistral_risk_intensity": mistral.get("risk_intensity", ""),
                    "mistral_uncertainty_intensity": mistral.get("uncertainty_intensity", ""),
                    "mistral_sentiment_proxy": mistral.get("sentiment_proxy", ""),
                    "mistral_action_relevance": mistral.get("portfolio_action_relevance", ""),
                    "mistral_active_signals": mistral.get("active_signals", ""),
                }
            )
        _add_return_fields(output, panel, ticker, event_date, reaction_threshold)
        for horizon in ["event_day", "post_1d", "post_3d", "post_10d", "post_21d"]:
            realized = str(output.get(f"{horizon}_reaction_label", "missing"))
            output[f"codex_match_{horizon}"] = _direction_match(str(output["codex_impact_direction"]), realized)
            if mistral:
                output[f"mistral_match_{horizon}"] = _direction_match(str(output["mistral_impact_direction"]), realized)
        output_rows.append(output)

    output_dir.mkdir(parents=True, exist_ok=True)
    rows_path = output_dir / "event_study_feedback_rows.csv"
    _write_csv(rows_path, output_rows)
    summary = summarize(output_rows, skipped=skipped, reaction_threshold=reaction_threshold, event_date_policy=event_date_policy)
    summary["outputs"] = {"rows": str(rows_path), "summary": str(output_dir / "event_study_summary.json"), "report": str(output_dir / "event_study_report.md")}
    _write_json(output_dir / "event_study_summary.json", summary)
    (output_dir / "event_study_report.md").write_text(render_report(summary), encoding="utf-8")
    return summary


def _mean(values: Iterable[Any]) -> float | None:
    parsed = [_safe_float(value, None) for value in values]
    nums = [value for value in parsed if value is not None]
    if not nums:
        return None
    return sum(nums) / len(nums)


def _corr(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(vx * vy)


def _group_summary(rows: list[dict[str, Any]], column: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(column, ""))].append(row)
    out: dict[str, Any] = {}
    for key, group in sorted(groups.items()):
        out[key or "missing"] = {
            "rows": len(group),
            "codex_post3_accuracy": _mean(row.get("codex_match_post_3d") for row in group),
            "mistral_post3_accuracy": _mean(row.get("mistral_match_post_3d") for row in group),
            "avg_abs_post3_abnormal": _mean(abs(_safe_float(row.get("post_3d_abnormal_return"), 0.0) or 0.0) for row in group if row.get("post_3d_abnormal_return") != ""),
            "avg_post3_abnormal": _mean(row.get("post_3d_abnormal_return") for row in group),
            "reaction_counts_post3": dict(Counter(str(row.get("post_3d_reaction_label", "")) for row in group)),
        }
    return out


def summarize(rows: list[dict[str, Any]], *, skipped: Counter, reaction_threshold: float, event_date_policy: str) -> dict[str, Any]:
    has_mistral = any("mistral_impact_direction" in row for row in rows)
    horizons = ["event_day", "post_1d", "post_3d", "post_10d", "post_21d"]
    metrics: dict[str, Any] = {}
    for horizon in horizons:
        metrics[f"codex_{horizon}_accuracy"] = _mean(row.get(f"codex_match_{horizon}") for row in rows)
        if has_mistral:
            metrics[f"mistral_{horizon}_accuracy"] = _mean(row.get(f"mistral_match_{horizon}") for row in rows)
        metrics[f"{horizon}_reaction_counts"] = dict(Counter(str(row.get(f"{horizon}_reaction_label", "")) for row in rows))
        metrics[f"avg_{horizon}_abnormal"] = _mean(row.get(f"{horizon}_abnormal_return") for row in rows)
        metrics[f"avg_abs_{horizon}_abnormal"] = _mean(abs(_safe_float(row.get(f"{horizon}_abnormal_return"), 0.0) or 0.0) for row in rows if row.get(f"{horizon}_abnormal_return") != "")

    for extractor in ["codex", "mistral"] if has_mistral else ["codex"]:
        for label in ["sentiment_proxy", "risk_intensity", "action_relevance"]:
            xs: list[float] = []
            ys: list[float] = []
            ys_abs: list[float] = []
            for row in rows:
                x = _safe_float(row.get(f"{extractor}_{label}"), None)
                y = _safe_float(row.get("post_3d_abnormal_return"), None)
                if x is None or y is None:
                    continue
                xs.append(x)
                ys.append(y)
                ys_abs.append(abs(y))
            metrics[f"{extractor}_{label}_corr_post3_abnormal"] = _corr(xs, ys)
            metrics[f"{extractor}_{label}_corr_abs_post3_abnormal"] = _corr(xs, ys_abs)

    return {
        "row_count": len(rows),
        "skipped": dict(skipped),
        "event_date_policy": event_date_policy,
        "reaction_threshold": reaction_threshold,
        "has_mistral": has_mistral,
        "metrics": metrics,
        "by_source_type": _group_summary(rows, "source_type"),
        "by_source_family": _group_summary(rows, "source_family"),
        "by_query_intent": _group_summary(rows, "query_intent_primary"),
        "by_document_split": _group_summary(rows, "document_split"),
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Event Study Feedback",
        "",
        "This is an offline validation report. Post-event returns must not be used as PPO input features.",
        "",
        f"- rows: `{summary['row_count']}`",
        f"- event date policy: `{summary['event_date_policy']}`",
        f"- reaction threshold: `{summary['reaction_threshold']}`",
        f"- has Mistral labels: `{summary['has_mistral']}`",
        "",
        "## Direction Accuracy",
        "",
        "| Horizon | Codex | Mistral | Reaction counts |",
        "|---|---:|---:|---|",
    ]
    metrics = summary["metrics"]
    for horizon in ["event_day", "post_1d", "post_3d", "post_10d", "post_21d"]:
        lines.append(
            f"| {horizon} | {_fmt(metrics.get(f'codex_{horizon}_accuracy'))} | "
            f"{_fmt(metrics.get(f'mistral_{horizon}_accuracy'))} | "
            f"`{metrics.get(f'{horizon}_reaction_counts')}` |"
        )
    lines.extend(["", "## Source Type Snapshot", "", "| Source type | Rows | Codex +3d acc | Mistral +3d acc | Avg +3d abnormal |", "|---|---:|---:|---:|---:|"])
    for source_type, row in summary["by_source_type"].items():
        lines.append(
            f"| {source_type} | {row['rows']} | {_fmt(row['codex_post3_accuracy'])} | "
            f"{_fmt(row['mistral_post3_accuracy'])} | {_fmt(row['avg_post3_abnormal'])} |"
        )
    lines.extend(["", "## Numeric Correlations", ""])
    for key, value in sorted(metrics.items()):
        if "corr" in key:
            lines.append(f"- `{key}`: `{_fmt(value)}`")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-panel", required=True, type=Path)
    parser.add_argument("--seed", required=True, type=Path)
    parser.add_argument("--comparison", type=Path, default=None)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--event-date-policy", choices=["decision_date", "available_at_first_trading_day"], default="decision_date")
    parser.add_argument("--reaction-threshold", type=float, default=0.005)
    args = parser.parse_args(argv)
    summary = build_event_study(
        base_panel=args.base_panel,
        seed_path=args.seed,
        comparison_path=args.comparison,
        output_dir=args.output_dir,
        event_date_policy=args.event_date_policy,
        reaction_threshold=args.reaction_threshold,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
