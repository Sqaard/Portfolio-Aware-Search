"""Dense retrieval placeholder.

Dense models are intentionally optional in v1. This module provides a stable
CLI surface so future sentence-transformer or FinBERT embeddings can be added
without changing the rest of the project.
"""

from __future__ import annotations

import argparse
from typing import Optional


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Dense indexing placeholder for future work.")
    parser.add_argument("--documents", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    raise SystemExit(
        "Dense indexing is intentionally disabled in v1. "
        "Use indexing/build_sparse_index.py for the working retrieval path."
    )


if __name__ == "__main__":
    raise SystemExit(main())
