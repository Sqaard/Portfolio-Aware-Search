"""Build a compact trusted-source package for FinPortfolio IR.

The package contains the best currently validated retrieval units, not every
official document. Noisy official units are listed in manifests but kept out of
the core extraction bundle.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from features.render_methodology_pdf import render_pdf  # noqa: E402


DEFAULT_DOCUMENTS = "data/processed_documents/sec_macro_company_ir_ppo_2010_2023_documents.jsonl"
DEFAULT_SOURCE_QUALITY_DIR = "data/exports/daily_retrieval_ppo_full_company_ir/source_quality_audit_macro_rules"
DEFAULT_MISTRAL_DIR = "data/exports/daily_retrieval_ppo_full_company_ir/mistral_source_quality_eval"
DEFAULT_MACRO_FEATURES_DIR = "data/exports/daily_retrieval_ppo_full_company_ir/codex_rule_text_features_macro_rules"
DEFAULT_OUTPUT_DIR = "data/exports/trusted_source_data_package_2026_05_14"
DEFAULT_ZIP = "data/exports/trusted_source_data_package_2026_05_14.zip"


CORE_BUCKETS: dict[str, tuple[str, str]] = {
    "official_macro_release": ("01_official_macro", "Official macro observations; impact direction must come from macro-rule engine."),
    "sec_filing_exhibit": ("02_sec_edgar_exhibits", "SEC 8-K exhibits and attached investor materials, usually EX-99.1."),
    "company_earnings_release": ("03_company_ir_core", "Official company earnings releases."),
    "company_press_release": ("03_company_ir_core", "Official company press releases."),
    "company_financial_report": ("03_company_ir_core", "Official company annual/financial reports."),
    "company_presentation": ("03_company_ir_core", "Official company investor/presentation documents."),
}

REVIEW_BUCKETS: dict[str, tuple[str, str]] = {
    "company_official_archive": ("04_company_ir_review", "Official company archive pages; trusted source but mixed extraction quality."),
}

EXCLUDED_BUCKETS: dict[str, tuple[str, str]] = {
    "sec_filing_section": ("05_not_core_manifest", "Official SEC sections; broad and useful for search, but noisy for LLM/source-quality extraction."),
    "company_sec_filing_hub": ("05_not_core_manifest", "Company-hosted SEC filing hub pages; trusted URL but poor document unit."),
}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _domain(row: dict[str, Any]) -> str:
    url = str(row.get("canonical_url") or row.get("url") or "")
    return urlparse(url).netloc.lower()


def _source_family(source_type: str) -> str:
    if source_type.startswith("official_macro"):
        return "official_macro"
    if source_type.startswith("sec_filing"):
        return "sec_edgar"
    if source_type.startswith("company"):
        return "company_ir"
    return "other"


def _ticker(row: dict[str, Any]) -> str:
    tickers = row.get("matched_tickers") or row.get("tickers_detected") or []
    if isinstance(tickers, list) and tickers:
        return str(tickers[0])
    return ""


def _readme(folder: str, description: str, rows: list[dict[str, Any]]) -> str:
    source_type_counts = Counter(str(row.get("source_type", "")) for row in rows)
    domain_counts = Counter(_domain(row) for row in rows if _domain(row))
    ticker_counts = Counter(_ticker(row) for row in rows if _ticker(row))
    lines = [
        f"# {folder}",
        "",
        description,
        "",
        f"- documents: `{len(rows)}`",
        f"- unique domains: `{len(domain_counts)}`",
        f"- unique tickers/market labels: `{len(ticker_counts)}`",
        "",
        "## Source Types",
        "",
    ]
    lines.extend(f"- `{key}`: `{value}`" for key, value in sorted(source_type_counts.items()))
    lines.extend(["", "## Top Domains", ""])
    lines.extend(f"- `{key}`: `{value}`" for key, value in domain_counts.most_common(20))
    return "\n".join(lines) + "\n"


def _copy_if_exists(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def build_package(
    *,
    documents: Path,
    source_quality_dir: Path,
    mistral_dir: Path,
    macro_features_dir: Path,
    output_dir: Path,
    zip_output: Path,
    include_internal_artifacts: bool = False,
) -> dict[str, Any]:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = _read_jsonl(documents)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    bucket_descriptions: dict[str, str] = {}
    excluded_rows: list[dict[str, Any]] = []

    for row in rows:
        source_type = str(row.get("source_type", ""))
        if source_type in CORE_BUCKETS:
            bucket, description = CORE_BUCKETS[source_type]
            buckets[bucket].append(row)
            bucket_descriptions[bucket] = description if bucket not in bucket_descriptions else bucket_descriptions[bucket]
        elif source_type in REVIEW_BUCKETS:
            bucket, description = REVIEW_BUCKETS[source_type]
            buckets[bucket].append(row)
            bucket_descriptions[bucket] = description
        elif source_type in EXCLUDED_BUCKETS:
            excluded_rows.append(row)

    for bucket, bucket_rows in sorted(buckets.items()):
        filename = {
            "01_official_macro": "official_macro_documents.jsonl",
            "02_sec_edgar_exhibits": "sec_filing_exhibits_documents.jsonl",
            "03_company_ir_core": "company_ir_core_documents.jsonl",
            "04_company_ir_review": "company_ir_review_documents.jsonl",
        }.get(bucket, "documents.jsonl")
        _write_jsonl(output_dir / bucket / filename, bucket_rows)
        (output_dir / bucket / "README.md").write_text(
            _readme(bucket, bucket_descriptions.get(bucket, ""), bucket_rows),
            encoding="utf-8",
        )

    excluded_manifest = [
        {
            "doc_id": row.get("doc_id", ""),
            "source_type": row.get("source_type", ""),
            "source": row.get("source", ""),
            "source_registry_id": row.get("source_registry_id", ""),
            "domain": _domain(row),
            "ticker": _ticker(row),
            "title": row.get("title", ""),
            "reason": EXCLUDED_BUCKETS.get(str(row.get("source_type", "")), ("", "Not in core package."))[1],
        }
        for row in excluded_rows
    ]
    if include_internal_artifacts:
        _write_csv(
            output_dir / "05_not_core_manifest" / "not_core_documents_manifest.csv",
            excluded_manifest,
            ["doc_id", "source_type", "source", "source_registry_id", "domain", "ticker", "title", "reason"],
        )
        (output_dir / "05_not_core_manifest" / "README.md").write_text(
            _readme("05_not_core_manifest", "Documents intentionally excluded from the quality-core JSONL package.", excluded_rows),
            encoding="utf-8",
        )

    included_rows = [row for bucket_rows in buckets.values() for row in bucket_rows]
    source_rows: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in included_rows:
        source_type = str(row.get("source_type", ""))
        domain = _domain(row)
        key = (domain, str(row.get("source", "")), str(row.get("source_registry_id", "")), _source_family(source_type))
        item = source_rows.setdefault(
            key,
            {
                "domain": domain,
                "source": row.get("source", ""),
                "source_registry_id": row.get("source_registry_id", ""),
                "source_family": _source_family(source_type),
                "source_reliability_tier": row.get("source_reliability_tier", ""),
                "document_count": 0,
                "source_types": set(),
                "tickers": set(),
            },
        )
        item["document_count"] += 1
        item["source_types"].add(source_type)
        ticker = _ticker(row)
        if ticker:
            item["tickers"].add(ticker)

    trusted_sources = []
    for item in source_rows.values():
        trusted_sources.append(
            {
                "domain": item["domain"],
                "source": item["source"],
                "source_registry_id": item["source_registry_id"],
                "source_family": item["source_family"],
                "source_reliability_tier": item["source_reliability_tier"],
                "document_count": item["document_count"],
                "source_types": "|".join(sorted(item["source_types"])),
                "tickers": "|".join(sorted(item["tickers"])),
            }
        )
    trusted_sources.sort(key=lambda row: (str(row["source_family"]), str(row["domain"]), str(row["source"])))
    _write_csv(
        output_dir / "00_manifests" / "trusted_sources.csv",
        trusted_sources,
        ["domain", "source", "source_registry_id", "source_family", "source_reliability_tier", "document_count", "source_types", "tickers"],
    )

    quality_summary_rows = []
    for bucket, bucket_rows in sorted(buckets.items()):
        quality_summary_rows.append(
            {
                "bucket": bucket,
                "document_count": len(bucket_rows),
                "unique_domains": len({_domain(row) for row in bucket_rows if _domain(row)}),
                "unique_sources": len({str(row.get("source", "")) for row in bucket_rows}),
                "unique_tickers": len({_ticker(row) for row in bucket_rows if _ticker(row)}),
                "source_types": "|".join(sorted({str(row.get("source_type", "")) for row in bucket_rows})),
            }
        )
    if include_internal_artifacts:
        quality_summary_rows.append(
            {
                "bucket": "05_not_core_manifest",
                "document_count": len(excluded_rows),
                "unique_domains": len({_domain(row) for row in excluded_rows if _domain(row)}),
                "unique_sources": len({str(row.get("source", "")) for row in excluded_rows}),
                "unique_tickers": len({_ticker(row) for row in excluded_rows if _ticker(row)}),
                "source_types": "|".join(sorted({str(row.get("source_type", "")) for row in excluded_rows})),
            }
        )
    _write_csv(
        output_dir / "00_manifests" / "quality_document_summary.csv",
        quality_summary_rows,
        ["bucket", "document_count", "unique_domains", "unique_sources", "unique_tickers", "source_types"],
    )

    if include_internal_artifacts:
        audit_dir = output_dir / "06_evaluation_audits"
        for name in [
            "SOURCE_QUALITY_AUDIT.md",
            "source_quality_by_source_family.csv",
            "source_quality_by_source_type.csv",
            "source_quality_by_source.csv",
            "source_quality_by_source_registry_id.csv",
            "source_quality_summary.json",
            "mistral_vs_codex_by_source_type.csv",
        ]:
            _copy_if_exists(source_quality_dir / name, audit_dir / "source_quality_audit" / name)
        for name in [
            "comparison_summary.json",
            "comparison_rows.csv",
            "human_adjudication_source_quality_balanced_top50.csv",
            "human_adjudication_source_quality_balanced_guide.md",
        ]:
            _copy_if_exists(mistral_dir / name, audit_dir / "mistral_source_quality_eval" / name)
        (audit_dir / "README.md").write_text(
            "# Evaluation Audits\n\nSource quality proxy audits, Mistral-vs-Codex comparison outputs, and human adjudication working files.\n",
            encoding="utf-8",
        )

        macro_dir = output_dir / "07_macro_rule_engine"
        _copy_if_exists(ROOT / "finportfolio_ir" / "macro_rule_engine.py", macro_dir / "macro_rule_engine.py")
        _copy_if_exists(macro_features_dir / "text_feature_diagnostics.json", macro_dir / "text_feature_diagnostics.json")
        (macro_dir / "README.md").write_text(
            "# Macro Rule Engine\n\n"
            "Official macro observations are directionally labeled by `macro_rule_engine.py`, "
            "not by generic LLM extraction. The included diagnostics summarize the rebuilt "
            "`codex_rule_teacher_v2_macro_rules` text-feature pass.\n",
            encoding="utf-8",
        )

    included_by_source_type = Counter(str(row.get("source_type", "")) for row in included_rows)
    summary = {
        "documents_input": str(documents),
        "output_dir": str(output_dir),
        "zip_output": str(zip_output),
        "trusted_domain_count": len({row["domain"] for row in trusted_sources if row["domain"]}),
        "trusted_source_label_count": len({row["source"] for row in trusted_sources if row["source"]}),
        "quality_document_count": len(included_rows),
        "review_or_core_document_count": len(included_rows),
        "not_core_document_count": len(excluded_rows),
        "quality_source_type_counts": dict(included_by_source_type),
        "bucket_summary": quality_summary_rows,
        "include_internal_artifacts": include_internal_artifacts,
    }
    (output_dir / "00_manifests" / "package_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "README.md").write_text(_top_readme(summary), encoding="utf-8")
    methodology_md = output_dir / "METHODOLOGY_BRIEF.md"
    methodology_md.write_text(_methodology_brief(), encoding="utf-8")
    render_pdf(methodology_md, output_dir / "METHODOLOGY_BRIEF.pdf")

    if zip_output.exists():
        zip_output.unlink()
    zip_output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(output_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(output_dir.parent))

    summary["zip_size_bytes"] = zip_output.stat().st_size
    (output_dir / "00_manifests" / "package_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def _top_readme(summary: dict[str, Any]) -> str:
    bucket_lines = [
        f"- `{row['bucket']}`: `{row['document_count']}` docs, `{row['unique_domains']}` domains, source types `{row['source_types']}`"
        for row in summary["bucket_summary"]
    ]
    source_type_lines = [
        f"- `{source_type}`: `{count}`"
        for source_type, count in sorted(summary["quality_source_type_counts"].items())
    ]
    return "\n".join(
        [
            "# Trusted Source Package",
            "",
            "This package contains the current high-quality FinPortfolio IR document universe for FinGPT/PPO experiments.",
            "",
            "Quality-core criteria:",
            "",
            "- Official macro observations are included, but directional impact must be produced by the macro-rule engine.",
            "- SEC exhibits are included as high-quality company evidence, especially EX-99.1 earnings/investor materials.",
            "- Company IR earnings releases, press releases, reports, and presentations are included as core company evidence.",
            "- Company official archive pages are included as review-grade trusted documents.",
            "- Raw SEC filing sections and company-hosted SEC filing hub pages are excluded from the data handoff package.",
            "",
            f"- trusted domains: `{summary['trusted_domain_count']}`",
            f"- trusted source labels: `{summary['trusted_source_label_count']}`",
            f"- quality/review document units: `{summary['quality_document_count']}`",
            f"- not-core official/noisy document units excluded from this handoff: `{summary['not_core_document_count']}`",
            "",
            "## Bucket Summary",
            "",
            *bucket_lines,
            "",
            "## Quality Source Type Counts",
            "",
            *source_type_lines,
            "",
            "## Folder Guide",
            "",
            "- `00_manifests`: package summary and trusted source/domain inventory.",
            "- `METHODOLOGY_BRIEF.pdf`: short plain-English method note for the next LLM/PPO stage.",
            "- `METHODOLOGY_BRIEF.md`: editable source for the PDF methodology note.",
            "- `01_official_macro`: official FRED/Fed/BLS/Treasury/EIA/Census-via-FRED observations.",
            "- `02_sec_edgar_exhibits`: SEC attached exhibits selected as high-quality retrieval units.",
            "- `03_company_ir_core`: company official earnings releases, press releases, reports, and presentations.",
            "- `04_company_ir_review`: official company archive pages that are trusted but need review filters.",
            "- Internal QA folders such as not-core manifests, evaluation audits, and macro-rule code are intentionally not included in the default handoff archive.",
            "",
        ]
    )


def _methodology_brief() -> str:
    return """# Methodology Brief: Text Features for PPO

This package supports one simple research question:

> Can a PPO trading agent improve when we add causal, high-quality text features to the normal market and macro features?

The key idea is not to ask an LLM to trade. The LLM should only read trusted financial documents and convert them into structured signals. PPO then decides whether these signals help. The methodology combines the AI4Finance project logic with the CERB-PPO reference: `PIT-safe retrieval -> schema-constrained extraction -> evidence atoms -> z_text + text_quality -> PPO ablation / risk gate`.

## 1. What Changes Compared With A Simple News Pipeline

A simple pipeline would scrape many headlines, run sentiment, and merge the result into PPO. That is risky because financial text is noisy, duplicated, biased, and often not point-in-time safe.

The improved method is stricter:

1. Build a trusted evidence set first.
2. Keep `available_at` timestamps so the model cannot see the future.
3. Prefer official sources before open web/news noise.
4. Extract features, not trading actions.
5. Test whether the new features improve out-of-sample PPO.

This follows the AI4Finance direction: literature review -> causal text dataset -> LLM feature generation -> PPO ablation. The CERB-PPO upgrade makes it more conservative: every text signal must pass through a fixed schema and every promoted feature must beat `base_macro`, not just look interesting.

## 2. Data Layers In This Package

- `01_official_macro`: official macro observations from FRED/Fed/BLS/Treasury/EIA/Census-via-FRED. These are structured macro facts, not normal prose.
- `02_sec_edgar_exhibits`: SEC attached exhibits, especially earnings releases and investor materials.
- `03_company_ir_core`: official company earnings releases, press releases, financial reports, and presentations.
- `04_company_ir_review`: trusted company archive pages that are useful but need more filtering.
- `00_manifests`: source/domain inventory and document counts.

The package intentionally excludes noisy internal QA folders and broad raw filing sections. Those are useful for engineering, but too early for a clean LLM/PPO handoff dataset.

## 3. Feature Extraction Target

The next extraction step should produce a frozen evidence-atom table, not free-form analysis. Each row should be tied to a decision time, document, route, source tier, extractor version, duplicate cluster, age, score, confidence, and fixed schema version.

The LLM or rule extractor should output structured fields such as:

- impact direction: positive / negative / neutral / mixed;
- risk intensity;
- uncertainty intensity;
- sentiment or sentiment surprise;
- event type;
- affected ticker or sector;
- temporal decay;
- confidence;
- sector spillover;
- short-term vs long-term effect.

Macro rows should be handled carefully: a macro observation is often a fact, not a text opinion. For example, a yield-curve value, VIX value, or credit-spread value should be interpreted by macro rules or a macro-specific prompt, not by generic sentiment.

## 4. CERB-PPO Feature Ladder

We should not jump directly from raw text features to a large PPO state vector. The staged ladder is:

- `E0 = base_macro`: current numerical/macro PPO baseline.
- `E1 = base_macro + text_lean_v1`: simple direct text features.
- `E2 = base_macro + text_action_primitive_v1`: direct text features tied to action primitives.
- `E6 = base_macro + z_text_continuous`: compact deconfounded text bottleneck, around 8 dimensions.
- `E8 = base_macro + z_text_continuous + risk_gate`: final conservative version, only if previous gates pass.

The `z_text` vector should come from evidence atoms, not raw LLM output. A separate `text_quality` vector should track whether the evidence is trustworthy:

- coverage rate;
- mean source reliability;
- mean extractor confidence;
- weak evidence rate;
- duplicate rate;
- route entropy.

This separation matters: PPO should know the difference between "high risk signal" and "bad/noisy evidence".

## 5. Baselines And Evaluation

The text pipeline should be compared against simple baselines before being trusted:

- FinBERT-style sentiment: useful but too narrow;
- generic financial LLM extraction;
- deterministic rule-based extraction;
- no-text PPO baseline.

The final test is PPO ablation:

- base PPO with numerical/market/macro features;
- PPO plus text features;
- compare out-of-sample performance.

Important metrics:

- cumulative return;
- Sharpe ratio;
- annualized volatility;
- maximum drawdown;
- statistical comparison such as Diebold-Mariano / HLN-style tests when applicable.
- same-turnover controls;
- seed stability;
- future-text placebo and lag-decay checks.

## 6. Why This Is Better

This methodology treats text as evidence, not magic. The pipeline asks:

- Was the document available before the decision time?
- Is the source reliable?
- Is the document specific enough to extract a real signal?
- Does the extracted signal survive PPO/out-of-sample testing?
- Does it still help after controlling for the existing `base_macro` signal?

That makes the system closer to a financial evidence engine than a sentiment demo.

## Useful Links

- FinRL: https://github.com/AI4Finance-Foundation/FinRL
- FinRL-Trading / FinRL-X: https://github.com/AI4Finance-Foundation/FinRL-Trading
- FRED: https://fred.stlouisfed.org/
- SEC EDGAR: https://www.sec.gov/edgar
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build trusted FinIR source package.")
    parser.add_argument("--documents", default=DEFAULT_DOCUMENTS)
    parser.add_argument("--source-quality-dir", default=DEFAULT_SOURCE_QUALITY_DIR)
    parser.add_argument("--mistral-dir", default=DEFAULT_MISTRAL_DIR)
    parser.add_argument("--macro-features-dir", default=DEFAULT_MACRO_FEATURES_DIR)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--zip-output", default=DEFAULT_ZIP)
    parser.add_argument("--include-internal-artifacts", action="store_true", help="Include not-core manifests, evaluation audits, and macro-rule code.")
    args = parser.parse_args(argv)

    summary = build_package(
        documents=Path(args.documents),
        source_quality_dir=Path(args.source_quality_dir),
        mistral_dir=Path(args.mistral_dir),
        macro_features_dir=Path(args.macro_features_dir),
        output_dir=Path(args.output_dir),
        zip_output=Path(args.zip_output),
        include_internal_artifacts=args.include_internal_artifacts,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
