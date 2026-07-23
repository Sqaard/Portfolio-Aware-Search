"""Shared CLI plumbing for the Big Data runners.

Outputs are always collected to the driver and written with plain Python
(``json`` / ``csv``), never through the Hadoop output committer -- this keeps the
jobs runnable on Windows without ``winutils.exe`` while still doing all the
heavy compute distributed.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from typing import Any, Iterable

from .config import (
    BIGDATA_OUTPUT_DIR,
    DEFAULT_CORPUS,
    NAMED_CORPORA,
    env_engine_default,
    resolve_corpus,
)
from .engine import Dataset, Engine, describe_backends, get_engine


def add_common_args(parser: argparse.ArgumentParser) -> None:
    """Register the flags every batch runner shares."""

    parser.add_argument(
        "--corpus",
        default=DEFAULT_CORPUS,
        help=(
            "Named corpus or path to a processed JSONL file. "
            f"Named: {', '.join(sorted(NAMED_CORPORA))}."
        ),
    )
    parser.add_argument(
        "--engine",
        default=env_engine_default(),
        choices=["auto", "spark", "local"],
        help="Compute backend (default: auto -> Spark if available, else local).",
    )
    parser.add_argument("--master", default=None, help="Spark master URL (e.g. local[*], spark://host:7077).")
    parser.add_argument("--partitions", type=int, default=None, help="Number of partitions / parallelism.")
    parser.add_argument("--num-workers", type=int, default=None, help="Local engine worker processes.")
    parser.add_argument("--limit", type=int, default=0, help="Only process the first N lines (quick smoke).")
    parser.add_argument(
        "--native-read",
        action="store_true",
        help="Spark only: read the corpus with sc.textFile (distributed IO). "
             "Use on the cluster / ASCII paths; ignored for the local engine.",
    )
    parser.add_argument("--output-dir", default=None, help="Directory for artifacts (default: data/exports/bigdata/<job>).")


def build_engine(args: argparse.Namespace) -> Engine:
    """Construct the requested engine from parsed args."""

    return get_engine(
        args.engine,
        master=args.master,
        default_partitions=args.partitions,
        num_workers=args.num_workers,
    )


def load_dataset(engine: Engine, args: argparse.Namespace) -> tuple[Dataset, Path]:
    """Return ``(dataset_of_lines, resolved_corpus_path)`` honouring ``--limit``."""

    corpus_path = resolve_corpus(args.corpus)
    if not corpus_path.exists():
        raise FileNotFoundError(
            f"Corpus not found: {corpus_path}. Provide --corpus <name|path>. "
            f"Known names: {', '.join(sorted(NAMED_CORPORA))}."
        )
    if args.limit and args.limit > 0:
        lines: list[str] = []
        with corpus_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    lines.append(line.rstrip("\n"))
                if len(lines) >= args.limit:
                    break
        dataset = engine.parallelize(lines, num_partitions=args.partitions)
    elif getattr(args, "native_read", False) and hasattr(engine, "text_file_native"):
        dataset = engine.text_file_native(str(corpus_path), num_partitions=args.partitions)
    else:
        dataset = engine.text_file(str(corpus_path), num_partitions=args.partitions)
    return dataset, corpus_path


def resolve_output_dir(args: argparse.Namespace, job_name: str) -> Path:
    if args.output_dir:
        return Path(args.output_dir)
    return BIGDATA_OUTPUT_DIR / job_name


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")


def write_csv(path: Path, header: Iterable[str], rows: Iterable[Iterable[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(list(header))
        for row in rows:
            writer.writerow(list(row))


def run_context(args: argparse.Namespace, job_name: str) -> dict:
    """Standard run metadata for artifact manifests."""

    return {
        "job": job_name,
        "corpus": args.corpus,
        "engine_requested": args.engine,
        "backends_available": describe_backends(),
        "limit": args.limit,
        "started_epoch": time.time(),
    }


class Timer:
    """Minimal wall-clock timer for reporting job duration."""

    def __enter__(self) -> "Timer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        self.seconds = time.perf_counter() - self._start
