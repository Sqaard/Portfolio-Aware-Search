"""Poll-based incremental micro-batch updater (the verifiable auto-update path).

This is a small stream processor built on the shared engine abstraction. It
models the same idea as a Spark Structured Streaming *file source*: an inbox
directory is watched, and on each tick only **unprocessed bytes** are read. A
per-file byte offset is tracked, so both brand-new files and **appends to an
existing file** (how ``crawler/live_incremental_fetch.py`` grows its live JSONL)
contribute each line exactly once -- never double-counted. Their aggregates are
merged additively into append-only state, so the corpus-analytics report stays
current as the crawler drops fresh evidence in, without recomputing the corpus.

It runs on Spark (distributed micro-batch compute) or the local engine, and,
unlike Structured Streaming, needs no Hadoop checkpoint, so it is fully runnable
and verifiable on Windows.

Typical wiring for the course's 'automatic updates' requirement::

    crawler/live_incremental_fetch.py  ->  writes new *.jsonl into the inbox
    bigdata.streaming.incremental_update --interval 300   (or a scheduled task)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bigdata.config import ROOT as PROJECT_ROOT  # noqa: E402
from bigdata.engine import get_engine  # noqa: E402
from bigdata.jobs import corpus_analytics  # noqa: E402

DEFAULT_INBOX = PROJECT_ROOT / "data" / "streaming_inbox"
DEFAULT_STATE_DIR = PROJECT_ROOT / "data" / "exports" / "bigdata" / "streaming_state"


def _default_state() -> dict:
    return {
        "metrics": {},
        "min_available_at": "",
        "max_available_at": "",
        "processed_files": {},
        "batches": [],
        "total_documents": 0,
    }


def _load_state(state_path: Path) -> dict:
    """Load state, always returning a dict with every required key present.

    A valid-but-incomplete state file (older schema, hand-edited) is merged over
    the defaults rather than returned as-is, so callers can index keys safely.
    """

    state = _default_state()
    if state_path.exists():
        try:
            loaded = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state.update(loaded)
        except (ValueError, OSError):
            pass
    return state


def _save_state(state_path: Path, state: dict) -> None:
    """Write state atomically (temp file + replace) so a crash cannot truncate it."""

    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(state_path)


def _discover_new_data(inbox: Path, glob: str, processed: dict) -> list:
    """Return ``(path, start_byte, end_byte)`` for files with unprocessed bytes.

    Tracks a per-file byte offset so that **appending** to an existing file
    (exactly how ``crawler/live_incremental_fetch.py`` grows its live JSONL) only
    processes the newly-appended lines -- never the ones already counted. A file
    that shrank (was truncated/rewritten below its processed offset) is skipped,
    because additive aggregates cannot subtract a prior contribution safely.
    """

    work: list = []
    for path in sorted(inbox.glob(glob)):
        if not path.is_file():
            continue
        size = path.stat().st_size
        prior = processed.get(str(path))
        start = 0
        if prior is not None:
            start = int(prior.get("offset", 0))
            if size == start:
                continue  # no new bytes
            if size < start:
                continue  # file shrank/rewritten -> cannot reconcile; skip
        work.append((path, start, size))
    return work


def _read_new_lines(work: list) -> list:
    """Read only the appended bytes of each file (binary seek at a line boundary)."""

    lines: list = []
    for path, start, _size in work:
        with path.open("rb") as handle:
            handle.seek(start)
            chunk = handle.read()
        for line in chunk.decode("utf-8", errors="replace").splitlines():
            if line.strip():
                lines.append(line)
    return lines


def _merge_dates(current: str, incoming: str, *, take_min: bool) -> str:
    if not incoming:
        return current
    if not current:
        return incoming
    if take_min:
        return current if current <= incoming else incoming
    return current if current >= incoming else incoming


def run_tick(
    *,
    inbox: Path,
    glob: str,
    state_dir: Path,
    engine_name: str,
    master: str | None = None,
    partitions: int | None = None,
    top_n: int = 25,
) -> dict:
    """Process one micro-batch of newly-arrived files and refresh the report."""

    state_path = state_dir / "state.json"
    state = _load_state(state_path)
    inbox.mkdir(parents=True, exist_ok=True)
    work = _discover_new_data(inbox, glob, state["processed_files"])

    if not work:
        report = corpus_analytics.finalize_report(
            state["metrics"], state["min_available_at"], state["max_available_at"], top_n=top_n
        )
        return {"new_files": 0, "new_documents": 0, "total_documents": report.get("total_documents", 0)}

    new_lines = _read_new_lines(work)
    engine = get_engine(engine_name, master=master, default_partitions=partitions)
    try:
        dataset = engine.parallelize(new_lines, num_partitions=partitions)
        metrics, min_available, max_available, new_docs = corpus_analytics.raw_metrics(dataset)
    finally:
        engine.stop()

    state["metrics"] = corpus_analytics.merge_metrics(state["metrics"], metrics)
    state["min_available_at"] = _merge_dates(state["min_available_at"], min_available, take_min=True)
    state["max_available_at"] = _merge_dates(state["max_available_at"], max_available, take_min=False)
    for path, _start, size in work:
        state["processed_files"][str(path)] = {"offset": size, "mtime_ns": path.stat().st_mtime_ns}
    state["total_documents"] = state.get("total_documents", 0) + new_docs
    state["batches"].append({
        "epoch": time.time(),
        "engine": engine.name,
        "files": [p.name for p, _s, _e in work],
        "new_documents": new_docs,
    })
    state["batches"] = state["batches"][-100:]  # keep the log bounded
    _save_state(state_path, state)

    report = corpus_analytics.finalize_report(
        state["metrics"], state["min_available_at"], state["max_available_at"], top_n=top_n
    )
    report["streaming"] = {
        "batches_processed": len(state["batches"]),
        "last_batch_files": [p.name for p, _s, _e in work],
        "last_batch_new_documents": new_docs,
    }
    (state_dir / "analytics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "new_files": len(work),
        "new_documents": new_docs,
        "total_documents": report.get("total_documents", 0),
        "engine": engine.name,
    }


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Incremental micro-batch corpus updater (auto-updates).")
    parser.add_argument("--inbox", default=str(DEFAULT_INBOX), help="Directory watched for new *.jsonl files.")
    parser.add_argument("--glob", default="*.jsonl", help="Glob for input files inside the inbox.")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR), help="Where streaming state + report live.")
    parser.add_argument("--engine", default="auto", choices=["auto", "spark", "local"])
    parser.add_argument("--master", default=None)
    parser.add_argument("--partitions", type=int, default=None)
    parser.add_argument("--top-n", type=int, default=25)
    parser.add_argument("--interval", type=float, default=0.0, help="Seconds between ticks (0 = single tick, then exit).")
    parser.add_argument("--max-ticks", type=int, default=0, help="Stop after N ticks when looping (0 = unbounded).")
    args = parser.parse_args(argv)

    inbox = Path(args.inbox)
    state_dir = Path(args.state_dir)
    tick = 0
    while True:
        tick += 1
        result = run_tick(
            inbox=inbox,
            glob=args.glob,
            state_dir=state_dir,
            engine_name=args.engine,
            master=args.master,
            partitions=args.partitions,
            top_n=args.top_n,
        )
        print(json.dumps({"tick": tick, **result}, ensure_ascii=False), flush=True)
        if args.interval <= 0:
            break
        if args.max_ticks and tick >= args.max_ticks:
            break
        time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
