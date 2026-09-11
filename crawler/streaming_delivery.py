"""Hand live document batches to the streaming consumers -- as NEW files.

The live fetcher keeps one growing JSONL (``data/live_ir/live_processed_documents.jsonl``)
that it rewrites on every run. That is fine for the search layer and for
:mod:`bigdata.streaming.incremental_update`, which tracks per-file byte offsets,
but it is invisible to Spark Structured Streaming: the file source records each
input file by path and never re-reads one it has consumed.

So each fetch's new documents are *also* dropped into a streaming inbox as one
new file, written atomically: a hidden temp name first (Hadoop's default path
filter skips ``.``-prefixed files, and it does not match the incremental
updater's ``*.jsonl`` glob either), then ``os.replace`` into its final name.
Neither consumer can therefore pick up half a batch.

``simulate_live_fetch`` stands in for the network collectors: it draws a random,
reproducible sample from an existing corpus and pushes it through the same
append + deliver path, so a benchmark measures the consumers, not the SEC API.
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finportfolio_ir.io_utils import read_jsonl, write_jsonl  # noqa: E402
from finportfolio_ir.text_utils import stable_document_hash  # noqa: E402


def append_unique_records(path: Path, new_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Append records whose ``doc_id`` / ``document_hash`` is unseen; return the appended ones."""

    path = Path(path)
    existing = read_jsonl(path) if path.exists() else []
    seen_doc_ids = {str(row.get("doc_id", "")) for row in existing if str(row.get("doc_id", ""))}
    seen_hashes = {str(row.get("document_hash", "")) for row in existing if str(row.get("document_hash", ""))}
    appended: list[dict[str, Any]] = []
    for record in new_records:
        doc_id = str(record.get("doc_id", "") or "")
        document_hash = str(record.get("document_hash", "") or "")
        if doc_id and doc_id in seen_doc_ids:
            continue
        if document_hash and document_hash in seen_hashes:
            continue
        appended.append(record)
        if doc_id:
            seen_doc_ids.add(doc_id)
        if document_hash:
            seen_hashes.add(document_hash)
    if appended:
        write_jsonl(path, [*existing, *appended])
    elif not path.exists():
        write_jsonl(path, [])
    return appended


def assert_outside_inbox(inbox: Path, *paths: Path) -> None:
    """Refuse a live output file inside the streaming inbox.

    The live JSONL is rewritten on every fetch. Inside the inbox, Spark would read
    it once as one more input file and every document in it would be counted a
    second time next to its own batch file.
    """

    inbox = Path(inbox).resolve()
    for path in paths:
        parent = Path(path).resolve().parent
        if parent == inbox or inbox in parent.parents:
            raise ValueError(f"{path} is inside the streaming inbox {inbox}; keep live outputs outside it")


def emit_streaming_batch(inbox: Path, records: list[dict[str, Any]], *, prefix: str = "live") -> Path | None:
    """Write ``records`` into ``inbox`` as one new, atomically-published JSONL file."""

    if not records:
        return None
    inbox = Path(inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    final = inbox / f"{prefix}_{stamp}_{len(records)}.jsonl"
    tmp = inbox / f".{final.name}.tmp"
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, final)
    return final


def sample_documents(corpus: Path, count: int, seed: int) -> list[dict[str, Any]]:
    """Draw ``count`` random documents from ``corpus``, re-identified as new arrivals.

    A sampled document is by construction already in the corpus, and two samples
    drawn with different seeds overlap. Each draw therefore gets a
    simulation-scoped ``doc_id`` and a matching recomputed ``document_hash`` --
    exactly what normalization would give a genuinely new document -- so no
    consumer's de-duplication can swallow part of the batch.
    """

    # "\n" framing, as Spark and read_jsonl use: str.splitlines() would also
    # split on U+2028 / U+2029 / U+0085 that JSON strings may carry raw.
    lines = [line for line in Path(corpus).read_text(encoding="utf-8").split("\n") if line.strip()]
    if count > len(lines):
        raise ValueError(f"asked for {count} documents but {corpus} holds only {len(lines)}")
    rng = random.Random(seed)
    records: list[dict[str, Any]] = []
    for position, index in enumerate(rng.sample(range(len(lines)), count)):
        record = json.loads(lines[index])
        record["doc_id"] = f"{record.get('doc_id', 'doc')}#sim-{seed}-{position}"
        record["document_hash"] = stable_document_hash(record)
        records.append(record)
    return records


def simulate_live_fetch(
    *,
    corpus: Path,
    count: int,
    seed: int,
    processed_output: Path,
    streaming_inbox: Path | None,
    prefix: str = "live",
) -> dict[str, Any]:
    """Stand-in for one live fetch: sample, append to the live file, deliver downstream."""

    if streaming_inbox:
        assert_outside_inbox(streaming_inbox, processed_output)
    started = time.perf_counter()
    records = sample_documents(corpus, count, seed)
    appended = append_unique_records(processed_output, records)
    batch_path = emit_streaming_batch(streaming_inbox, appended, prefix=prefix) if streaming_inbox else None
    emitted_at = time.time()  # wall clock right after the atomic rename: the arrival instant
    return {
        "status": "simulated",
        "corpus": str(corpus),
        "seed": seed,
        "sampled": len(records),
        "processed_appended": len(appended),
        "streaming_batch": str(batch_path) if batch_path else None,
        "emitted_at": emitted_at,
        "producer_seconds": round(time.perf_counter() - started, 3),
    }
