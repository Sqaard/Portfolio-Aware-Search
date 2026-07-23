"""Distributed inverted index + BM25 global statistics (a MapReduce classic).

This is the scalable counterpart of ``indexing/build_sparse_index.py``. The
single-machine version loads the whole corpus into RAM and builds the index in
one process; here the same computation is a map/shuffle/reduce that runs across
Spark executors (or local worker processes):

    map:     JSONL line  -> (doc_id, {term: tf}, length)      [tokenise]
    map:     document     -> (doc_id, length)                 [doc lengths]
    flatMap: document     -> (term, 1) per distinct term       [df postings]
    reduce:  (term, 1)*   -> (term, df)                        [document freq]

The resulting ``document_frequencies`` / ``document_lengths`` /
``average_document_length`` are, by construction, identical to
``BM25Index.from_documents(...).to_artifact()`` for a corpus of unique
``doc_id``s -- which the verification tests assert.

The math (idf, tf-saturation) is also replicated so a full BM25 query can be
executed distributed (:func:`query_bm25`), matching ``BM25Index.score_query``.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Iterator, Optional

from ..config import BM25_B, BM25_K1
from ..engine.base import Dataset
from .mapping import build_document, indexing_tokens, parse_json_line

# --------------------------------------------------------------------------
# Map / reduce primitives (module-level -> picklable for process backends).
# --------------------------------------------------------------------------
def doc_term_counts(line: str) -> Optional[tuple]:
    """``JSONL line -> (doc_id, {term: tf}, length)`` or ``None`` if dropped."""

    record = parse_json_line(line)
    if record is None:
        return None
    document = build_document(record)
    if document is None:
        return None
    counts: dict = {}
    for token in indexing_tokens(document):
        counts[token] = counts.get(token, 0) + 1
    length = sum(counts.values())
    return (document.doc_id, counts, length)


def is_not_none(value: Any) -> bool:
    return value is not None


def doc_length_pair(entry: tuple) -> tuple:
    """``(doc_id, counts, length) -> (doc_id, length)``."""

    return (entry[0], entry[2])


def distinct_term_pairs(entry: tuple) -> Iterator[tuple]:
    """``(doc_id, counts, length) -> (term, 1)`` for each distinct term."""

    for term in entry[1].keys():
        yield (term, 1)


def term_posting_pairs(entry: tuple) -> Iterator[tuple]:
    """``(doc_id, counts, length) -> (term, (doc_id, tf))`` for the full index."""

    doc_id = entry[0]
    for term, tf in entry[1].items():
        yield (term, (doc_id, tf))


def add_ints(a: int, b: int) -> int:
    return a + b


# --------------------------------------------------------------------------
# Orchestration.
# --------------------------------------------------------------------------
def build_bm25_index(dataset: Dataset, *, top_terms: int = 50) -> dict:
    """Build the BM25 global-statistics artifact from a dataset of JSONL lines.

    Returns a dict with the same core fields as
    ``BM25Index.to_artifact`` plus corpus-level summaries used by the report.
    """

    base = dataset.map(doc_term_counts).filter(is_not_none).cache()

    n_docs = base.count()
    document_lengths = base.map(doc_length_pair).collect_as_map()
    document_frequencies = (
        base.flat_map(distinct_term_pairs).reduce_by_key(add_ints).collect_as_map()
    )

    unique_docs = len(document_lengths)
    total_tokens = sum(document_lengths.values())
    avgdl = (total_tokens / unique_docs) if unique_docs else 0.0
    leaders = sorted(document_frequencies.items(), key=lambda kv: (-kv[1], kv[0]))[:top_terms]

    return {
        "k1": BM25_K1,
        "b": BM25_B,
        "n_docs": n_docs,
        "unique_doc_ids": unique_docs,
        "vocabulary_size": len(document_frequencies),
        "total_tokens": total_tokens,
        "average_document_length": avgdl,
        "document_frequencies": document_frequencies,
        "document_lengths": document_lengths,
        "top_terms_by_document_frequency": [
            {"term": term, "document_frequency": df} for term, df in leaders
        ],
    }


def build_postings(dataset: Dataset) -> Dataset:
    """Return the full inverted index as ``(term, [(doc_id, tf), ...])`` records."""

    base = dataset.map(doc_term_counts).filter(is_not_none)
    return base.flat_map(term_posting_pairs).group_by_key()


# --------------------------------------------------------------------------
# Distributed BM25 query (parity with BM25Index.score_query).
# --------------------------------------------------------------------------
def idf(term_df: int, n_docs: int) -> float:
    """BM25 idf, identical to ``BM25Index._idf``."""

    n_docs = max(n_docs, 1)
    return math.log(1.0 + (n_docs - term_df + 0.5) / (term_df + 0.5))


class _QueryScorer:
    """Picklable per-document BM25 scorer for a fixed query."""

    __slots__ = ("query_terms", "idf_map", "avgdl", "k1", "b")

    def __init__(self, query_terms, idf_map, avgdl, k1, b) -> None:
        self.query_terms = query_terms
        self.idf_map = idf_map
        self.avgdl = avgdl
        self.k1 = k1
        self.b = b

    def __call__(self, entry: tuple) -> tuple:
        doc_id, counts, length = entry
        score = 0.0
        if self.avgdl > 0:
            for term in self.query_terms:
                tf = counts.get(term, 0)
                if tf <= 0:
                    continue
                denom = tf + self.k1 * (1.0 - self.b + self.b * length / self.avgdl)
                score += self.idf_map.get(term, 0.0) * (tf * (self.k1 + 1.0)) / denom
        return (doc_id, score)


def query_bm25(dataset: Dataset, query_text: str, artifact: dict, *, top_k: int = 10) -> list:
    """Score a query over the corpus distributed, returning ``[(doc_id, score)]``.

    Uses the precomputed ``artifact`` (df table + avgdl + n_docs) so scoring is a
    single distributed map. Matches ``BM25Index.score_query`` for equal inputs.
    """

    from finportfolio_ir.text_utils import tokenize

    query_terms = tokenize(query_text)
    document_frequencies = artifact["document_frequencies"]
    n_docs = artifact["n_docs"]
    idf_map = {term: idf(document_frequencies.get(term, 0), n_docs) for term in set(query_terms)}
    scorer = _QueryScorer(
        query_terms=query_terms,
        idf_map=idf_map,
        avgdl=artifact["average_document_length"],
        k1=artifact["k1"],
        b=artifact["b"],
    )
    scored = dataset.map(doc_term_counts).filter(is_not_none).map(scorer).collect()
    scored.sort(key=lambda kv: (-kv[1], kv[0]))
    return scored[:top_k] if top_k > 0 else scored
