"""CLI: build the distributed inverted index + BM25 statistics over a corpus.

Example (auto engine -> Spark if available):

    python -m bigdata.run_inverted_index --corpus macro
    python -m bigdata.run_inverted_index --corpus sample --engine local --with-postings
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bigdata.cli_common import (  # noqa: E402
    Timer,
    add_common_args,
    build_engine,
    load_dataset,
    resolve_output_dir,
    run_context,
    write_csv,
    write_json,
)
from bigdata.jobs import inverted_index  # noqa: E402


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Distributed inverted index + BM25 statistics.")
    add_common_args(parser)
    parser.add_argument("--top-terms", type=int, default=50, help="Top terms by document frequency to report.")
    parser.add_argument("--with-postings", action="store_true", help="Also write the full (term -> postings) index as JSONL.")
    parser.add_argument("--query", default=None, help="Optional BM25 query to score distributed as a demo.")
    parser.add_argument("--query-top-k", type=int, default=10)
    args = parser.parse_args(argv)

    output_dir = resolve_output_dir(args, "inverted_index")
    context = run_context(args, "inverted_index")

    engine = build_engine(args)
    context["engine_used"] = engine.name
    print(f"[bigdata] inverted index | engine={engine.name} corpus={args.corpus}", flush=True)
    try:
        dataset, corpus_path = load_dataset(engine, args)
        with Timer() as timer:
            artifact = inverted_index.build_bm25_index(dataset, top_terms=args.top_terms)
        context["corpus_path"] = str(corpus_path)
        context["seconds"] = round(timer.seconds, 3)

        # Human-readable summary (no giant dicts inline).
        summary = {k: v for k, v in artifact.items() if k not in ("document_frequencies", "document_lengths")}
        summary["run"] = context
        write_json(output_dir / "bm25_stats.json", summary)

        # The index tables (the scalable artifact) as CSV.
        write_csv(
            output_dir / "document_frequencies.csv",
            ["term", "document_frequency"],
            sorted(artifact["document_frequencies"].items(), key=lambda kv: (-kv[1], kv[0])),
        )
        write_csv(
            output_dir / "document_lengths.csv",
            ["doc_id", "length"],
            sorted(artifact["document_lengths"].items()),
        )

        if args.with_postings:
            postings = inverted_index.build_postings(dataset).collect()
            postings_path = output_dir / "postings.jsonl"
            with postings_path.open("w", encoding="utf-8", newline="\n") as handle:
                for term, docs in sorted(postings, key=lambda kv: kv[0]):
                    handle.write(json.dumps({"term": term, "df": len(docs), "postings": docs}, ensure_ascii=False) + "\n")

        query_result = None
        if args.query:
            scored = inverted_index.query_bm25(dataset, args.query, artifact, top_k=args.query_top_k)
            query_result = [{"doc_id": d, "bm25_score": round(s, 6)} for d, s in scored]
            write_json(output_dir / "query_demo.json", {"query": args.query, "results": query_result})
    finally:
        engine.stop()

    print(json.dumps({
        "engine": engine.name,
        "corpus": str(corpus_path),
        "n_docs": artifact["n_docs"],
        "vocabulary_size": artifact["vocabulary_size"],
        "average_document_length": round(artifact["average_document_length"], 3),
        "seconds": context["seconds"],
        "output_dir": str(output_dir),
        "top_terms": artifact["top_terms_by_document_frequency"][:10],
        "query": args.query,
        "query_top": (query_result or [])[:5],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
