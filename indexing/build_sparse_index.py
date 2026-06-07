"""Pure-Python BM25 sparse index for the v1 retrieval prototype."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finportfolio_ir.io_utils import read_jsonl
from finportfolio_ir.schema import FinancialDocument, load_documents
from finportfolio_ir.text_utils import tokenize


@dataclass
class BM25Index:
    documents: list[FinancialDocument]
    term_frequencies: dict[str, Counter[str]]
    document_frequencies: Counter[str]
    document_lengths: dict[str, int]
    average_document_length: float
    k1: float = 1.5
    b: float = 0.75

    @classmethod
    def from_documents(cls, documents: list[FinancialDocument]) -> "BM25Index":
        term_frequencies: dict[str, Counter[str]] = {}
        document_frequencies: Counter[str] = Counter()
        document_lengths: dict[str, int] = {}

        for document in documents:
            tokens = tokenize(document.text_for_indexing())
            counts = Counter(tokens)
            term_frequencies[document.doc_id] = counts
            document_lengths[document.doc_id] = sum(counts.values())
            document_frequencies.update(counts.keys())

        avgdl = (
            sum(document_lengths.values()) / len(document_lengths)
            if document_lengths
            else 0.0
        )
        return cls(
            documents=documents,
            term_frequencies=term_frequencies,
            document_frequencies=document_frequencies,
            document_lengths=document_lengths,
            average_document_length=avgdl,
        )

    def _idf(self, term: str) -> float:
        n_docs = max(len(self.documents), 1)
        df = self.document_frequencies.get(term, 0)
        return math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))

    def score_query(self, query_text: str) -> dict[str, float]:
        query_terms = tokenize(query_text)
        scores = {document.doc_id: 0.0 for document in self.documents}
        if not query_terms or self.average_document_length <= 0:
            return scores

        for term in query_terms:
            idf = self._idf(term)
            for document in self.documents:
                doc_id = document.doc_id
                tf = self.term_frequencies[doc_id].get(term, 0)
                if tf <= 0:
                    continue
                dl = self.document_lengths[doc_id]
                denominator = tf + self.k1 * (1.0 - self.b + self.b * dl / self.average_document_length)
                scores[doc_id] += idf * (tf * (self.k1 + 1.0)) / denominator
        return scores

    def to_artifact(self) -> dict[str, object]:
        return {
            "k1": self.k1,
            "b": self.b,
            "document_ids": [document.doc_id for document in self.documents],
            "document_lengths": self.document_lengths,
            "average_document_length": self.average_document_length,
            "document_frequencies": dict(self.document_frequencies),
        }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build a BM25 sparse index artifact.")
    parser.add_argument("--documents", required=True, help="Processed documents JSONL.")
    parser.add_argument("--output", required=True, help="Index artifact JSON.")
    args = parser.parse_args(argv)

    documents = load_documents(read_jsonl(args.documents))
    index = BM25Index.from_documents(documents)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(index.to_artifact(), indent=2), encoding="utf-8")
    print(f"Wrote sparse index artifact for {len(documents)} documents to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
