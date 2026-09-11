"""PySpark-backed engine -- the genuine Big Data framework deliverable.

``SparkDataset`` wraps a PySpark ``RDD`` and maps the shared :class:`Dataset`
contract onto native RDD operations (``map``, ``reduceByKey``, ``groupByKey``,
...). All heavy compute (tokenization, inverted index, aggregation) therefore
runs distributed across Spark executors/cores.

Windows/OneDrive robustness notes (this repo lives under a Cyrillic path with
spaces, which Hadoop's local ``Path`` parser mishandles):

* File reads are performed *driver-side* with plain Python IO and then
  ``sc.parallelize``-d, so the non-ASCII source path never reaches Hadoop.
  ``text_file_native`` is offered for clean/ASCII/HDFS paths that want true
  distributed reads (used by the Structured Streaming job).
* ``JAVA_HOME`` is auto-repaired (the machine's is often set to ``...\\jdk\\bin``
  instead of the JDK root), and Spark's scratch/warehouse dirs are pointed at
  an ASCII temp directory. Results are ``collect``-ed and written with Python,
  avoiding the Hadoop output committer (which needs ``winutils.exe``).
"""

from __future__ import annotations

import glob
import os
import re
import tempfile
from typing import Any, Callable, Iterable, Iterator

from .base import Dataset, Engine

# Spark 3.5.x officially supports Java 8/11/17. Java 21 works for the driver JVM
# but is not a supported target, so we rank a compatible runtime ahead of it.
_JAVA_PREFERENCE = (17, 11, 8, 21)


def _java_major(root: str) -> int:
    """Best-effort major version parsed from a JDK/JRE directory name."""

    name = os.path.basename(os.path.normpath(root)).lower()
    # e.g. jre1.8.0_431 / jdk1.8.0_411 -> 8
    legacy = re.search(r"1\.(\d+)\.\d", name)
    if legacy:
        return int(legacy.group(1))
    # e.g. jdk-21 / jdk-17.0.9 / jdk17 -> 21 / 17
    modern = re.search(r"(?:jdk|jre|java|temurin|corretto|zulu)[-_]?(\d+)", name)
    if modern:
        return int(modern.group(1))
    return 0


def _has_java(root: str) -> bool:
    if not root:
        return False
    return any(
        os.path.isfile(os.path.join(root, "bin", exe))
        for exe in ("java.exe", "java")
    )


def resolve_java_home() -> str | None:
    """Return a JDK/JRE home such that ``<home>/bin/java(.exe)`` exists, or None.

    An explicit ``FINPORTFOLIO_SPARK_JAVA_HOME`` wins. Otherwise the machine's
    ``JAVA_HOME`` is honoured (repairing the common ``...\\bin`` misconfig), and
    finally well-known install roots are scanned and ranked so a Spark-3.5
    compatible runtime (Java 17/11/8) is preferred over Java 21.
    """

    override = os.environ.get("FINPORTFOLIO_SPARK_JAVA_HOME", "").strip().strip('"')
    if override and _has_java(override):
        return os.path.normpath(override)

    scanned: list[str] = []
    for pattern in (
        r"C:\Program Files\Java\*",
        r"C:\Program Files\Eclipse Adoptium\*",
        r"C:\Program Files\Microsoft\jdk*",
        r"C:\Program Files\Amazon Corretto\*",
        r"C:\Program Files\Zulu\*",
        r"/usr/lib/jvm/*",
        r"/opt/java/*",
        r"/Library/Java/JavaVirtualMachines/*/Contents/Home",
    ):
        scanned.extend(glob.glob(pattern))
    valid = [os.path.normpath(p) for p in scanned if _has_java(p)]

    # Honour the machine JAVA_HOME (or its parent when it points at .../bin).
    env_home = os.environ.get("JAVA_HOME", "").strip().strip('"')
    if env_home:
        norm = os.path.normpath(env_home)
        if _has_java(norm):
            valid.insert(0, norm)
        elif os.path.basename(norm).lower() == "bin" and _has_java(os.path.dirname(norm)):
            valid.insert(0, os.path.dirname(norm))

    if not valid:
        return None

    def rank(path: str) -> tuple:
        major = _java_major(path)
        try:
            pref = _JAVA_PREFERENCE.index(major)
        except ValueError:
            pref = len(_JAVA_PREFERENCE)  # unknown majors after known ones
        return (pref, -major)

    return sorted(dict.fromkeys(valid), key=rank)[0]


class SparkEngine(Engine):
    """Engine backed by a PySpark ``SparkContext``/``SparkSession``."""

    name = "spark"

    def __init__(
        self,
        master: str | None = None,
        app_name: str = "finportfolio-ir-bigdata",
        default_partitions: int | None = None,
        log_level: str = "WARN",
        extra_conf: dict | None = None,
    ) -> None:
        self._prepare_environment()
        try:
            from pyspark import SparkConf  # noqa: WPS433 - lazy import by design
            from pyspark.sql import SparkSession
        except Exception as exc:  # pragma: no cover - exercised only without pyspark
            raise RuntimeError(
                "PySpark is not importable. Install it with "
                "`pip install -r requirements-bigdata.txt` (pins pyspark>=3.5,<4.0 -- "
                "4.0.0 has a Windows worker bug) or use --engine local."
            ) from exc

        scratch = _ascii_scratch_dir()
        resolved_master = master or os.environ.get("SPARK_MASTER", "local[*]")
        # Tag the application with where it actually ran, so the History Server
        # list distinguishes a cluster run from a local[*] run at a glance
        # (both otherwise show the same name and look identical).
        if resolved_master.startswith("local"):
            run_tag = f"local({resolved_master})"
        else:
            run_tag = "CLUSTER"
        conf = SparkConf()
        conf.setAppName(f"{app_name} [{run_tag}]")
        conf.setMaster(resolved_master)
        conf.set("spark.ui.showConsoleProgress", "false")
        conf.set("spark.sql.warehouse.dir", _as_uri(os.path.join(scratch, "warehouse")))
        conf.set("spark.local.dir", os.path.join(scratch, "local"))
        conf.set("spark.hadoop.fs.permissions.umask-mode", "000")
        # Event logging: lets the Spark History Server replay a finished job, so the
        # DAG, per-task Event Timeline (scheduler delay / deserialise / compute /
        # shuffle read+write) and executor metrics stay inspectable AFTER the run.
        # The driver UI on :4040 dies with the application; the history UI does not.
        event_log_dir = os.environ.get("FINPORTFOLIO_SPARK_EVENTLOG_DIR", "").strip()
        if event_log_dir:
            os.makedirs(event_log_dir, exist_ok=True)
            conf.set("spark.eventLog.enabled", "true")
            conf.set("spark.eventLog.dir", _as_uri(event_log_dir))
        # Pin the loopback interface only in local mode; on a real spark:// cluster
        # the driver must be reachable by executors, so let Spark/SPARK_LOCAL_IP decide.
        if resolved_master.startswith("local"):
            conf.set("spark.driver.host", "127.0.0.1")
            conf.set("spark.driver.bindAddress", "127.0.0.1")
        for key, value in (extra_conf or {}).items():
            conf.set(key, str(value))

        self.spark = SparkSession.builder.config(conf=conf).getOrCreate()
        self.sc = self.spark.sparkContext
        self.sc.setLogLevel(log_level)
        cores = self.sc.defaultParallelism or (os.cpu_count() or 1)
        self.default_partitions = max(1, int(default_partitions or cores))

    @staticmethod
    def _prepare_environment() -> None:
        import sys

        java_home = resolve_java_home()
        if java_home:
            os.environ["JAVA_HOME"] = java_home
        # Workers and driver must use the same interpreter that imported pyspark.
        os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
        os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)

    # -- sources -----------------------------------------------------------
    def parallelize(self, items: Iterable[Any], num_partitions: int | None = None) -> "SparkDataset":
        data = list(items)
        slices = max(1, min(num_partitions or self.default_partitions, len(data) or 1))
        return SparkDataset(self, self.sc.parallelize(data, slices))

    def text_file(self, paths: Any, num_partitions: int | None = None) -> "SparkDataset":
        """Driver-side, path-robust line reader (see module docstring)."""

        lines: list[str] = []
        for path in _as_path_list(paths):
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.rstrip("\n")
                    if line.strip():
                        lines.append(line)
        slices = max(1, min(num_partitions or self.default_partitions, len(lines) or 1))
        return SparkDataset(self, self.sc.parallelize(lines, slices))

    def text_file_native(self, paths: Any, num_partitions: int | None = None) -> "SparkDataset":
        """True distributed read via ``sc.textFile`` (ASCII/HDFS paths only)."""

        path_arg = ",".join(_as_uri(p) for p in _as_path_list(paths))
        rdd = self.sc.textFile(path_arg, minPartitions=num_partitions or self.default_partitions)
        rdd = rdd.filter(lambda line: bool(line and line.strip()))
        return SparkDataset(self, rdd)

    def stop(self) -> None:
        session = getattr(self, "spark", None)
        if session is not None:
            session.stop()
            self.spark = None  # type: ignore[assignment]


class SparkDataset(Dataset):
    """A :class:`Dataset` backed by a PySpark ``RDD``."""

    def __init__(self, engine: SparkEngine, rdd: Any) -> None:
        self._engine = engine
        self._rdd = rdd

    @property
    def rdd(self) -> Any:
        return self._rdd

    def map(self, fn: Callable[[Any], Any]) -> "SparkDataset":
        return SparkDataset(self._engine, self._rdd.map(fn))

    def flat_map(self, fn: Callable[[Any], Iterable[Any]]) -> "SparkDataset":
        return SparkDataset(self._engine, self._rdd.flatMap(fn))

    def filter(self, fn: Callable[[Any], bool]) -> "SparkDataset":
        return SparkDataset(self._engine, self._rdd.filter(fn))

    def map_partitions(self, fn: Callable[[Iterator[Any]], Iterable[Any]]) -> "SparkDataset":
        return SparkDataset(self._engine, self._rdd.mapPartitions(fn))

    def reduce_by_key(self, fn: Callable[[Any, Any], Any]) -> "SparkDataset":
        return SparkDataset(self._engine, self._rdd.reduceByKey(fn))

    def group_by_key(self) -> "SparkDataset":
        grouped = self._rdd.groupByKey().mapValues(list)
        return SparkDataset(self._engine, grouped)

    def distinct(self) -> "SparkDataset":
        return SparkDataset(self._engine, self._rdd.distinct())

    def cache(self) -> "SparkDataset":
        return SparkDataset(self._engine, self._rdd.cache())

    def unpersist(self) -> "SparkDataset":
        """Release cached blocks. A long-lived session (Structured Streaming reuses
        one context for every micro-batch) would otherwise keep one per batch."""

        self._rdd.unpersist()
        return self

    def collect(self) -> list:
        return self._rdd.collect()

    def count(self) -> int:
        return self._rdd.count()

    def reduce(self, fn: Callable[[Any, Any], Any]) -> Any:
        return self._rdd.reduce(fn)

    def count_by_value(self) -> dict:
        return dict(self._rdd.countByValue())

    def take(self, n: int) -> list:
        return self._rdd.take(n) if n > 0 else []


# -- helpers ---------------------------------------------------------------
def _as_path_list(paths: Any) -> list:
    if isinstance(paths, (str, bytes, os.PathLike)):
        return [os.fspath(paths)]
    return [os.fspath(p) for p in paths]


def _ascii_scratch_dir() -> str:
    base = os.path.join(tempfile.gettempdir(), "finportfolio_spark")
    os.makedirs(base, exist_ok=True)
    return base


def _as_uri(path: str) -> str:
    """Return a ``file:///`` URI with forward slashes for Hadoop on Windows."""

    resolved = os.path.abspath(os.fspath(path))
    normalized = resolved.replace("\\", "/")
    if normalized.startswith("/"):
        return "file://" + normalized
    return "file:///" + normalized
