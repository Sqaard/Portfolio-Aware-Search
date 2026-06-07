"""Merge deterministic FinIR text features into a PPO base panel CSV."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from features.build_daily_retrieval_contexts import DATE_COLUMNS, TICKER_COLUMNS, _first_present  # noqa: E402


DROP_FEATURE_COLUMNS = {"date", "decision_date", "tic", "retrieval_layer"}
DROP_CONSTANT_TEXT_COLUMNS = {
    "portfolio_signal_capital_return_count",
    "portfolio_signal_capital_return_flag",
    "portfolio_signal_company_risk_count",
    "portfolio_signal_company_risk_flag",
    "portfolio_signal_earnings_guidance_count",
    "portfolio_signal_earnings_guidance_flag",
    "portfolio_signal_housing_count",
    "portfolio_signal_housing_flag",
    "portfolio_signal_legal_regulatory_count",
    "portfolio_signal_legal_regulatory_flag",
    "portfolio_signal_margin_pressure_count",
    "portfolio_signal_margin_pressure_flag",
    "portfolio_signal_mna_count",
    "portfolio_signal_mna_flag",
    "portfolio_signal_supply_chain_count",
    "portfolio_signal_supply_chain_flag",
}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _feature_columns(rows: list[dict[str, str]]) -> list[str]:
    if not rows:
        return []
    return [
        column
        for column in rows[0].keys()
        if column not in DROP_FEATURE_COLUMNS and column not in DROP_CONSTANT_TEXT_COLUMNS
    ]


def _is_numeric_feature(column: str) -> bool:
    return (
        column.endswith("_count")
        or column.endswith("_flag")
        or column.endswith("_score")
        or column.endswith("_days")
        or column.startswith("stock_text_")
        or column.startswith("portfolio_text_")
        or "_avg_" in column
        or "_max_" in column
    )


def merge_text_features(
    *,
    base_panel: Path,
    stock_features: Path,
    portfolio_features: Path,
    output: Path,
    manifest_output: Path,
    train_end: str,
) -> dict[str, Any]:
    stock_rows = _read_csv(stock_features)
    portfolio_rows = _read_csv(portfolio_features)
    stock_cols = _feature_columns(stock_rows)
    portfolio_cols = _feature_columns(portfolio_rows)
    stock_by_key = {
        (str(row.get("date") or row.get("decision_date")), str(row.get("tic", "")).upper()): row
        for row in stock_rows
    }
    portfolio_by_date = {str(row.get("date") or row.get("decision_date")): row for row in portfolio_rows}

    output.parent.mkdir(parents=True, exist_ok=True)
    base_row_count = 0
    stock_match_count = 0
    portfolio_match_count = 0
    unique_dates: set[str] = set()
    unique_tickers: set[str] = set()

    with base_panel.open("r", encoding="utf-8-sig", newline="") as input_handle:
        reader = csv.DictReader(input_handle)
        if not reader.fieldnames:
            raise ValueError(f"Base panel is empty or has no header: {base_panel}")
        columns = set(reader.fieldnames)
        date_col = _first_present(columns, DATE_COLUMNS)
        ticker_col = _first_present(columns, TICKER_COLUMNS)
        if not date_col or not ticker_col:
            raise ValueError("Base panel must have supported date and ticker columns.")
        feature_cols = [column for column in stock_cols + portfolio_cols if column not in reader.fieldnames]
        fieldnames = list(reader.fieldnames) + feature_cols + [
            "stock_text_has_evidence",
            "portfolio_text_has_evidence",
            "text_feature_split",
        ]
        with output.open("w", encoding="utf-8", newline="") as output_handle:
            writer = csv.DictWriter(output_handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in reader:
                base_row_count += 1
                day = str(row.get(date_col, ""))[:10]
                ticker = str(row.get(ticker_col, "")).upper().strip()
                unique_dates.add(day)
                unique_tickers.add(ticker)
                stock = stock_by_key.get((day, ticker), {})
                portfolio = portfolio_by_date.get(day, {})
                if stock:
                    stock_match_count += 1
                if portfolio:
                    portfolio_match_count += 1
                for column in stock_cols:
                    row[column] = stock.get(column, "0" if _is_numeric_feature(column) else "")
                for column in portfolio_cols:
                    row[column] = portfolio.get(column, "0" if _is_numeric_feature(column) else "")
                row["stock_text_has_evidence"] = "1" if stock else "0"
                row["portfolio_text_has_evidence"] = "1" if portfolio else "0"
                row["text_feature_split"] = "train" if day < train_end else "test"
                writer.writerow(row)

    manifest = {
        "base_panel": str(base_panel),
        "stock_features": str(stock_features),
        "portfolio_features": str(portfolio_features),
        "output": str(output),
        "base_rows": base_row_count,
        "unique_dates": len(unique_dates),
        "unique_tickers": len(unique_tickers),
        "stock_feature_rows": len(stock_rows),
        "portfolio_feature_rows": len(portfolio_rows),
        "stock_matched_rows": stock_match_count,
        "portfolio_matched_rows": portfolio_match_count,
        "stock_coverage_rate": round(stock_match_count / base_row_count, 6) if base_row_count else 0.0,
        "portfolio_coverage_rate": round(portfolio_match_count / base_row_count, 6) if base_row_count else 0.0,
        "train_end": train_end,
        "dropped_constant_text_columns": sorted(DROP_CONSTANT_TEXT_COLUMNS),
    }
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    manifest_output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge Codex-rule text features into a PPO base panel.")
    parser.add_argument("--base-panel", required=True)
    parser.add_argument("--stock-features", required=True)
    parser.add_argument("--portfolio-features", required=True)
    parser.add_argument("--output", default="data/exports/daily_retrieval_ppo/rl_panel_codex_rule_text_features.csv")
    parser.add_argument("--manifest-output", default="data/exports/daily_retrieval_ppo/rl_panel_codex_rule_text_features_manifest.json")
    parser.add_argument("--train-end", default="2021-10-01")
    args = parser.parse_args(argv)

    manifest = merge_text_features(
        base_panel=Path(args.base_panel),
        stock_features=Path(args.stock_features),
        portfolio_features=Path(args.portfolio_features),
        output=Path(args.output),
        manifest_output=Path(args.manifest_output),
        train_end=args.train_end,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
