"""Shared configuration for the Big Data layer: paths, corpora, BM25 constants."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# BM25 constants -- kept identical to the single-machine reference implementation
# in ``indexing/build_sparse_index.py`` so distributed output is comparable.
BM25_K1 = 1.5
BM25_B = 0.75

# Default output location for Big Data artifacts (derived data; the JSONL corpora
# remain the source of truth).
BIGDATA_OUTPUT_DIR = ROOT / "data" / "exports" / "bigdata"

PROCESSED_DIR = ROOT / "data" / "processed_documents"
RAW_DIR = ROOT / "data" / "raw_documents"

# Named corpora spanning three orders of magnitude, so the same jobs can be run
# from a laptop smoke test up to the full multi-hundred-MB corpus.
NAMED_CORPORA: dict[str, Path] = {
    # 24 docs -- used by the parity tests and quick smoke runs.
    "sample": PROCESSED_DIR / "documents.jsonl",
    # ~300 SEC Dow-30 filings.
    "sec300": PROCESSED_DIR / "sec_dow30_2010_2023_300_documents.jsonl",
    # ~18k official US macro releases (~39 MB) -- the default demo scale.
    "macro": PROCESSED_DIR / "official_macro_2010_2023_documents.jsonl",
    # ~1.7k SEC filings.
    "sec_ppo": PROCESSED_DIR / "sec_dow30_ppo_2010_2023_1740_documents.jsonl",
    # Section/exhibit level SEC corpus (~294 MB) -- full scale.
    "sec_sections": PROCESSED_DIR / "sec_dow30_ppo_2010_2023_1800_with_dis_legacy_sections_documents.jsonl",
    # Combined macro + company IR + SEC (~352 MB) -- largest single corpus.
    "all_ppo": PROCESSED_DIR / "sec_macro_company_ir_ppo_2010_2023_documents.jsonl",
}

DEFAULT_CORPUS = "macro"


def resolve_corpus(name_or_path: str) -> Path:
    """Resolve a named corpus or a filesystem path to an existing file."""

    if name_or_path in NAMED_CORPORA:
        return NAMED_CORPORA[name_or_path]
    return Path(name_or_path)


def env_engine_default() -> str:
    """Engine to use when the CLI does not specify one (``FINPORTFOLIO_BIGDATA_ENGINE``)."""

    return os.environ.get("FINPORTFOLIO_BIGDATA_ENGINE", "auto").lower()
