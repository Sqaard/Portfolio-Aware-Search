"""Замер ДО-Big-Data архитектуры: indexing/build_sparse_index.py на том же корпусе.

Сравнивается с кластером по одинаковым границам. На кластере таймер (Timer в
bigdata/run_inverted_index.py) обёрнут вокруг build_bm25_index, а --native-read
делает чтение ленивым, поэтому ВНУТРЬ таймера попадают: чтение файла, разбор JSON,
сборка FinancialDocument, токенизация и агрегация.

Одноклассовый эквивалент в старом коде — те же три шага подряд:

    records   = read_jsonl(path)              # чтение + JSON
    documents = load_documents(records)       # валидация + сборка объектов
    index     = BM25Index.from_documents(...) # токенизация + счётчики

Все три и составляют timed window. to_artifact() и запись на диск — вне окна,
как и запись CSV на стороне кластера; они измеряются отдельно.

    python deploy/spark_cluster/bench_pre_bigdata.py --repeats 3
    python deploy/spark_cluster/bench_pre_bigdata.py --limit 12   # проба накладных
"""

from __future__ import annotations

import argparse
import gc
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_CORPUS = "C:/tmp/finportfolio_bigdata/sec_full_text_heavy_documents.jsonl"


def peak_rss_mb() -> float:
    """Пиковая память процесса, МБ. 0.0 если измерить нечем."""
    try:
        import psutil  # noqa: WPS433
        return psutil.Process().memory_info().rss / 1024 / 1024
    except Exception:
        try:  # Windows-специфичный путь без сторонних пакетов
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = PMC()
            counters.cb = ctypes.sizeof(PMC)
            ctypes.windll.psapi.GetProcessMemoryInfo(
                ctypes.windll.kernel32.GetCurrentProcess(),
                ctypes.byref(counters),
                counters.cb,
            )
            return counters.PeakWorkingSetSize / 1024 / 1024
        except Exception:
            return 0.0


def one_run(corpus: str, limit: int) -> dict:
    """Один прогон старой реализации с поэтапной разбивкой."""
    from finportfolio_ir.io_utils import read_jsonl  # noqa: WPS433
    from finportfolio_ir.schema import load_documents  # noqa: WPS433
    from indexing.build_sparse_index import BM25Index  # noqa: WPS433

    gc.collect()
    t0 = time.perf_counter()

    if limit:
        # Зеркалит bigdata/cli_common.py::load_dataset: при --limit файл читается
        # только до N строк. Иначе проба накладных включила бы чтение всех 364 МиБ
        # и перестала быть пробой.
        records = []
        with open(corpus, "r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    records.append(json.loads(line))
                if len(records) >= limit:
                    break
    else:
        records = read_jsonl(corpus)
    t_read = time.perf_counter()

    documents = load_documents(records)
    del records
    t_load = time.perf_counter()

    index = BM25Index.from_documents(documents)
    t_index = time.perf_counter()

    # вне timed window: сериализация артефакта, как и запись CSV на кластере
    artifact = index.to_artifact()
    blob = json.dumps(artifact, indent=2)
    t_dump = time.perf_counter()

    return {
        "n_docs": len(index.documents),
        "vocabulary_size": len(index.document_frequencies),
        "total_tokens": sum(index.document_lengths.values()),
        "avg_doc_len": round(index.average_document_length, 3),
        "read_seconds": round(t_read - t0, 3),
        "load_seconds": round(t_load - t_read, 3),
        "index_seconds": round(t_index - t_load, 3),
        "seconds": round(t_index - t0, 3),          # <- сопоставимо с run.seconds кластера
        "dump_seconds": round(t_dump - t_index, 3),  # вне окна
        "artifact_mb": round(len(blob.encode("utf-8")) / 1024 / 1024, 2),
        "peak_rss_mb": round(peak_rss_mb(), 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", default=DEFAULT_CORPUS)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0,
                        help="Проба накладных расходов: столько же документов, сколько у --limit кластера.")
    parser.add_argument("--warmup", action="store_true", default=True)
    args = parser.parse_args()

    kind = f"скелет ({args.limit} док.)" if args.limit else "полный корпус"
    print(f"[pre-bigdata] {kind} | {sys.version.split()[0]} | {args.corpus}", flush=True)

    runs = []
    total = args.repeats + (1 if args.warmup else 0)
    for i in range(total):
        result = one_run(args.corpus, args.limit)
        tag = "разогрев" if (args.warmup and i == 0) else f"прогон {i if args.warmup else i + 1}"
        print(f"  {tag:<10} {result['seconds']:>8.2f} s   "
              f"(чтение {result['read_seconds']:.2f} / сборка {result['load_seconds']:.2f} / "
              f"индекс {result['index_seconds']:.2f})   пик RSS {result['peak_rss_mb']:.0f} МБ",
              flush=True)
        if not (args.warmup and i == 0):
            runs.append(result)

    med = statistics.median(r["seconds"] for r in runs)
    print()
    print(json.dumps({
        "python": sys.version.split()[0],
        "kind": kind,
        "median_seconds": med,
        "runs": [r["seconds"] for r in runs],
        "n_docs": runs[0]["n_docs"],
        "vocabulary_size": runs[0]["vocabulary_size"],
        "total_tokens": runs[0]["total_tokens"],
        "median_read": statistics.median(r["read_seconds"] for r in runs),
        "median_load": statistics.median(r["load_seconds"] for r in runs),
        "median_index": statistics.median(r["index_seconds"] for r in runs),
        "median_dump_outside_window": statistics.median(r["dump_seconds"] for r in runs),
        "artifact_mb": runs[0]["artifact_mb"],
        "peak_rss_mb": max(r["peak_rss_mb"] for r in runs),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
