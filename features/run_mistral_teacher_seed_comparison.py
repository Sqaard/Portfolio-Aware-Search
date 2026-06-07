"""Run Mistral extraction on the Codex teacher seed and compare labels."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from features.build_text_feature_baseline import SIGNAL_COLUMNS  # noqa: E402
from finportfolio_ir.io_utils import read_jsonl, write_jsonl  # noqa: E402


DEFAULT_SEED = "data/exports/daily_retrieval_ppo_full_dis_legacy/codex_rule_text_features/codex_teacher_seed.jsonl"
DEFAULT_OUTPUT_DIR = "data/exports/mistral_vs_codex_seed"
DEFAULT_BASE_URL = "https://api.mistral.ai/v1/chat/completions"
DEFAULT_MODEL = "mistral-small-latest"
PROMPT_VERSION = "mistral_finir_extraction_v2"
DIRECTION_VALUES = {"positive", "negative", "neutral", "mixed"}
NUMERIC_LABELS = (
    "risk_intensity",
    "uncertainty_intensity",
    "sentiment_proxy",
    "portfolio_action_relevance",
)


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _api_key(explicit: str = "") -> str:
    return (
        explicit.strip()
        or os.environ.get("MISTRAL_API_KEY", "").strip()
        or os.environ.get("LLM_API_KEY", "").strip()
    )


def _base_url(explicit: str = "") -> str:
    return (
        explicit.strip()
        or os.environ.get("MISTRAL_BASE_URL", "").strip()
        or os.environ.get("LLM_BASE_URL", "").strip()
        or DEFAULT_BASE_URL
    )


def _model(explicit: str = "") -> str:
    return explicit.strip() or os.environ.get("MISTRAL_MODEL", "").strip() or os.environ.get("LLM_MODEL", "").strip() or DEFAULT_MODEL


def build_prompt_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "teacher_id": row.get("teacher_id", ""),
        "doc_id": row.get("doc_id", ""),
        "decision_date": row.get("decision_date", ""),
        "document_split": row.get("document_split", ""),
        "regime": row.get("regime", ""),
        "retrieval_layer": row.get("retrieval_layer", ""),
        "target_ticker": row.get("target_ticker", ""),
        "source_family": row.get("source_family", ""),
        "source_type": row.get("source_type", ""),
        "source": row.get("source", ""),
        "source_registry_id": row.get("source_registry_id", ""),
        "source_reliability_tier": row.get("source_reliability_tier", ""),
        "query_intent_primary": row.get("query_intent_primary", ""),
        "title": row.get("title", ""),
        "excerpt": row.get("excerpt", ""),
    }


def system_prompt() -> str:
    signal_definitions = {
        "signal_earnings_guidance": "earnings releases, financial results, outlook, guidance, forecast, revenue/EPS commentary",
        "signal_company_risk": "company-specific risk factors, operating risk, financial risk, business-model risk, risk-factor sections",
        "signal_macro_rates": "interest rates, Fed policy, Treasury yields, yield curve, real yields, financing rates",
        "signal_inflation": "inflation, CPI/PCE/PPI, input costs, pricing pressure",
        "signal_credit": "credit, lending, funding costs, defaults, credit cards, loan demand, spreads, liquidity",
        "signal_labor_growth": "labor market, jobs, employment, wages, growth, industrial activity, macro growth",
        "signal_market_volatility": "market volatility, VIX, risk appetite, selloffs, broad market stress",
        "signal_energy": "oil, gas, crude, WTI/Brent, energy prices, fuel costs",
        "signal_housing": "housing, mortgages, homebuilding, real estate, construction, housing starts",
        "signal_legal_regulatory": "legal proceedings, litigation, investigations, regulation, compliance, antitrust",
        "signal_supply_chain": "supply chain, shortages, logistics, inventory, components, procurement",
        "signal_consumer_demand": "consumer demand, spending, retail traffic, card volume, subscribers, customer activity",
        "signal_margin_pressure": "gross/operating margins, input costs, wage costs, pricing, expense pressure",
        "signal_capital_return": "dividends, buybacks, repurchases, shareholder return, capital distributions",
        "signal_mna": "mergers, acquisitions, divestitures, spin-offs, takeover, transaction closing/integration",
    }
    definitions = "\n".join(f"- {key}: {value}" for key, value in signal_definitions.items())
    return (
        f"Prompt version: {PROMPT_VERSION}.\n"
        "You extract financial text feature labels for a US equity retrieval QA dataset. "
        "Use only the provided title, excerpt, and metadata. Do not assume facts that are not present. "
        "Treat the query intent, source_type, retrieval_layer, target_ticker, and regime as metadata cues. "
        "For SEC risk-factor or MD&A sections, tag company-specific signal families even when the excerpt is descriptive. "
        "For official macro releases, tag the relevant macro signal family even when the text is observational. "
        "For company IR earnings releases, press releases, presentations, and annual reports, tag the company-level "
        "business signals that are actually stated: earnings/guidance, demand, margins, credit, capital return, "
        "legal/regulatory, supply chain, M&A, energy, or housing when applicable. "
        "Prefer recall for active_signals: include a signal when it is clearly mentioned or strongly implied by metadata, "
        "but do not invent ticker-specific events from broad macro text.\n"
        "Signal definitions:\n"
        f"{definitions}\n"
        "Impact direction calibration: positive means the text explicitly suggests support/tailwind/opportunity; "
        "negative means risk/headwind/stress; mixed means both; neutral means descriptive with no clear directional implication. "
        "Numerical calibration: risk_intensity 0.0 none, 0.3 mild, 0.6 material, 0.9 severe; "
        "uncertainty_intensity 0.0 none, 0.3 some forward-looking uncertainty, 0.6 meaningful uncertainty, 0.9 high uncertainty; "
        "portfolio_action_relevance 0.2 background, 0.5 useful context, 0.8 likely important for portfolio features, 1.0 highly actionable. "
        "Return one JSON object only, with this schema: "
        "{impact_direction: one of positive|negative|neutral|mixed, "
        "risk_intensity: number 0..1, uncertainty_intensity: number 0..1, "
        "sentiment_proxy: number -1..1, portfolio_action_relevance: number 0..1, "
        "active_signals: array of allowed signal names, confidence: low|medium|high, rationale: short string}. "
        "Use mixed when both risk and opportunity are material."
    )


def _request_mistral(
    *,
    row: dict[str, Any],
    api_key: str,
    base_url: str,
    model: str,
    timeout_seconds: int,
    max_retries: int,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt()},
            {"role": "user", "content": json.dumps(build_prompt_payload(row), ensure_ascii=False)},
        ],
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    last_error = ""
    for attempt in range(1, max_retries + 1):
        request = urllib.request.Request(base_url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                response_json = json.loads(response.read().decode("utf-8"))
            content = str(response_json["choices"][0]["message"]["content"])
            return {
                "teacher_id": row.get("teacher_id", ""),
                "doc_id": row.get("doc_id", ""),
                "prompt_version": PROMPT_VERSION,
                "mistral_model": model,
                "mistral_raw_content": content,
                "mistral_labels": normalize_prediction(_parse_json_object(content)),
                "usage": response_json.get("usage", {}),
                "error": "",
            }
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            last_error = f"HTTP {exc.code}: {detail}"
            if exc.code not in {408, 409, 429, 500, 502, 503, 504} or attempt >= max_retries:
                break
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            if attempt >= max_retries:
                break
        time.sleep(min(8.0, 1.5 * (2 ** (attempt - 1))))
    return {
        "teacher_id": row.get("teacher_id", ""),
        "doc_id": row.get("doc_id", ""),
        "prompt_version": PROMPT_VERSION,
        "mistral_model": model,
        "mistral_raw_content": "",
        "mistral_labels": {},
        "usage": {},
        "error": last_error,
    }


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped, flags=re.IGNORECASE).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in model output.")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("Model output JSON is not an object.")
    return parsed


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def normalize_prediction(parsed: dict[str, Any]) -> dict[str, Any]:
    direction = str(parsed.get("impact_direction", "neutral")).lower().strip()
    if direction not in DIRECTION_VALUES:
        direction = "neutral"
    raw_signals = parsed.get("active_signals", [])
    if not isinstance(raw_signals, list):
        raw_signals = []
    active_signals = sorted({str(item).strip() for item in raw_signals if str(item).strip() in SIGNAL_COLUMNS})
    return {
        "impact_direction": direction,
        "risk_intensity": round(_clip(_safe_float(parsed.get("risk_intensity")), 0.0, 1.0), 6),
        "uncertainty_intensity": round(_clip(_safe_float(parsed.get("uncertainty_intensity")), 0.0, 1.0), 6),
        "sentiment_proxy": round(_clip(_safe_float(parsed.get("sentiment_proxy")), -1.0, 1.0), 6),
        "portfolio_action_relevance": round(_clip(_safe_float(parsed.get("portfolio_action_relevance")), 0.0, 1.0), 6),
        "active_signals": active_signals,
        "confidence": str(parsed.get("confidence", "medium")).lower().strip(),
        "rationale": str(parsed.get("rationale", ""))[:800],
    }


def _signal_metrics(gold: set[str], predicted: set[str]) -> dict[str, Any]:
    tp = len(gold & predicted)
    fp = len(predicted - gold)
    fn = len(gold - predicted)
    precision = tp / (tp + fp) if tp + fp else 1.0 if not gold else 0.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "signal_tp": tp,
        "signal_fp": fp,
        "signal_fn": fn,
        "signal_precision": round(precision, 6),
        "signal_recall": round(recall, 6),
        "signal_f1": round(f1, 6),
        "missing_signals": sorted(gold - predicted),
        "extra_signals": sorted(predicted - gold),
    }


def compare_prediction(teacher: dict[str, Any], prediction: dict[str, Any]) -> dict[str, Any]:
    labels = teacher.get("labels", {}) if isinstance(teacher.get("labels"), dict) else {}
    predicted = prediction.get("mistral_labels", {}) if isinstance(prediction.get("mistral_labels"), dict) else {}
    gold_signals = set(str(item) for item in labels.get("active_signals", []) if str(item) in SIGNAL_COLUMNS)
    pred_signals = set(str(item) for item in predicted.get("active_signals", []) if str(item) in SIGNAL_COLUMNS)
    signal = _signal_metrics(gold_signals, pred_signals)
    row: dict[str, Any] = {
        "teacher_id": teacher.get("teacher_id", ""),
        "doc_id": teacher.get("doc_id", ""),
        "document_split": teacher.get("document_split", ""),
        "regime": teacher.get("regime", ""),
        "retrieval_layer": teacher.get("retrieval_layer", ""),
        "source_family": teacher.get("source_family", ""),
        "source_type": teacher.get("source_type", ""),
        "source": teacher.get("source", ""),
        "source_registry_id": teacher.get("source_registry_id", ""),
        "source_reliability_tier": teacher.get("source_reliability_tier", ""),
        "query_intent_primary": teacher.get("query_intent_primary", ""),
        "target_ticker": teacher.get("target_ticker", ""),
        "error": prediction.get("error", ""),
        "gold_impact_direction": labels.get("impact_direction", ""),
        "mistral_impact_direction": predicted.get("impact_direction", ""),
        "impact_direction_match": int(labels.get("impact_direction", "") == predicted.get("impact_direction", "")),
        "gold_active_signals": "|".join(sorted(gold_signals)),
        "mistral_active_signals": "|".join(sorted(pred_signals)),
        "missing_signals": "|".join(signal["missing_signals"]),
        "extra_signals": "|".join(signal["extra_signals"]),
        **{key: value for key, value in signal.items() if not isinstance(value, list)},
    }
    for label in NUMERIC_LABELS:
        gold_value = _safe_float(labels.get(label))
        pred_value = _safe_float(predicted.get(label))
        row[f"gold_{label}"] = gold_value
        row[f"mistral_{label}"] = pred_value
        row[f"abs_error_{label}"] = round(abs(gold_value - pred_value), 6)
    return row


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_summary(rows: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [row for row in rows if not row.get("error")]
    def avg(column: str, subset: list[dict[str, Any]]) -> float:
        if not subset:
            return 0.0
        return round(sum(_safe_float(row.get(column)) for row in subset) / len(subset), 6)

    by_group: dict[str, dict[str, Any]] = {}
    for group_col in ["document_split", "retrieval_layer", "source_family", "source_type", "source_registry_id", "query_intent_primary", "regime"]:
        group_summary: dict[str, Any] = {}
        values = defaultdict(list)
        for row in rows:
            values[str(row.get(group_col, ""))].append(row)
        for key, subset in sorted(values.items()):
            good = [row for row in subset if not row.get("error")]
            group_summary[key or "missing"] = {
                "rows": len(subset),
                "successful_rows": len(good),
                "impact_direction_accuracy": avg("impact_direction_match", good),
                "signal_f1": avg("signal_f1", good),
                "risk_mae": avg("abs_error_risk_intensity", good),
                "action_relevance_mae": avg("abs_error_portfolio_action_relevance", good),
            }
        by_group[group_col] = group_summary

    return {
        "prediction_rows": len(predictions),
        "comparison_rows": len(rows),
        "successful_rows": len(successful),
        "failed_rows": len(rows) - len(successful),
        "impact_direction_accuracy": avg("impact_direction_match", successful),
        "signal_precision": avg("signal_precision", successful),
        "signal_recall": avg("signal_recall", successful),
        "signal_f1": avg("signal_f1", successful),
        "numeric_mae": {label: avg(f"abs_error_{label}", successful) for label in NUMERIC_LABELS},
        "document_split_counts": dict(Counter(str(row.get("document_split", "")) or "missing" for row in rows)),
        "regime_counts": dict(Counter(str(row.get("regime", "")) or "missing" for row in rows)),
        "by_group": by_group,
    }


def load_predictions(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    predictions = {}
    for row in read_jsonl(path):
        teacher_id = str(row.get("teacher_id", ""))
        if teacher_id:
            predictions[teacher_id] = row
    return predictions


def run_comparison(
    *,
    seed_path: Path,
    output_dir: Path,
    api_key: str,
    base_url: str,
    model: str,
    limit: int,
    sleep_seconds: float,
    timeout_seconds: int,
    max_retries: int,
    dry_run: bool,
    resume: bool,
) -> dict[str, Any]:
    seed_rows = read_jsonl(seed_path)
    if limit > 0:
        seed_rows = seed_rows[:limit]
    output_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = output_dir / "mistral_predictions.jsonl"
    comparison_path = output_dir / "comparison_rows.csv"
    summary_path = output_dir / "comparison_summary.json"
    failed_path = output_dir / "failed_rows.jsonl"
    prompt_preview_path = output_dir / "prompt_preview.json"

    if dry_run:
        preview = {
            "system_prompt": system_prompt(),
            "user_payload": build_prompt_payload(seed_rows[0]) if seed_rows else {},
            "seed_rows": len(seed_rows),
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "base_url": base_url,
            "has_api_key": bool(api_key),
        }
        prompt_preview_path.write_text(json.dumps(preview, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"status": "dry_run", "prompt_preview": str(prompt_preview_path), "seed_rows": len(seed_rows)}

    if not api_key:
        raise RuntimeError("Missing Mistral API key. Set MISTRAL_API_KEY or LLM_API_KEY, or pass --api-key.")

    existing = load_predictions(predictions_path) if resume else {}
    with predictions_path.open("a" if resume else "w", encoding="utf-8") as handle:
        for index, row in enumerate(seed_rows, start=1):
            teacher_id = str(row.get("teacher_id", ""))
            if teacher_id in existing:
                continue
            prediction = _request_mistral(
                row=row,
                api_key=api_key,
                base_url=base_url,
                model=model,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
            )
            handle.write(json.dumps(prediction, ensure_ascii=False) + "\n")
            handle.flush()
            if sleep_seconds > 0 and index < len(seed_rows):
                time.sleep(sleep_seconds)

    predictions_by_id = load_predictions(predictions_path)
    predictions = [predictions_by_id[str(row.get("teacher_id", ""))] for row in seed_rows if str(row.get("teacher_id", "")) in predictions_by_id]
    comparison_rows = [
        compare_prediction(row, predictions_by_id[str(row.get("teacher_id", ""))])
        for row in seed_rows
        if str(row.get("teacher_id", "")) in predictions_by_id
    ]
    failed_rows = [row for row in predictions if row.get("error")]
    _write_csv(comparison_path, comparison_rows)
    write_jsonl(failed_path, failed_rows)
    summary = build_summary(comparison_rows, predictions)
    summary.update(
        {
            "status": "completed",
            "seed_path": str(seed_path),
            "model": model,
            "base_url": base_url,
            "outputs": {
                "predictions": str(predictions_path),
                "comparison_rows": str(comparison_path),
                "summary": str(summary_path),
                "failed_rows": str(failed_path),
            },
        }
    )
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    _load_dotenv(Path(".env"))
    parser = argparse.ArgumentParser(description="Compare Mistral extraction against Codex teacher seed labels.")
    parser.add_argument("--seed", default=DEFAULT_SEED)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--sleep-seconds", type=float, default=0.15)
    parser.add_argument("--timeout-seconds", type=int, default=90)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args(argv)

    try:
        summary = run_comparison(
            seed_path=Path(args.seed),
            output_dir=Path(args.output_dir),
            api_key=_api_key(args.api_key),
            base_url=_base_url(args.base_url),
            model=_model(args.model),
            limit=args.limit,
            sleep_seconds=args.sleep_seconds,
            timeout_seconds=args.timeout_seconds,
            max_retries=args.max_retries,
            dry_run=args.dry_run,
            resume=not args.no_resume,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
