"""Select and construct a compute engine by name (``spark`` / ``local`` / ``auto``)."""

from __future__ import annotations

import importlib.util
from typing import Any

from .base import Engine
from .local_mapreduce import LocalEngine


class EngineUnavailableError(RuntimeError):
    """Raised when a specifically-requested engine cannot be constructed."""


def spark_available() -> bool:
    """True if the ``pyspark`` package is importable (does not launch a JVM)."""

    return importlib.util.find_spec("pyspark") is not None


def get_engine(engine: str = "auto", **kwargs: Any) -> Engine:
    """Return a ready compute engine.

    Parameters
    ----------
    engine:
        ``"spark"`` forces PySpark (raising :class:`EngineUnavailableError` if it
        cannot start), ``"local"`` forces the pure-Python engine, and ``"auto"``
        prefers Spark when importable and transparently falls back to local.
    kwargs:
        Backend constructor arguments. Recognised keys are filtered per backend
        so the same call works for either engine.
    """

    engine = (engine or "auto").lower()
    if engine == "local":
        return LocalEngine(**_filter_kwargs(LocalEngine, kwargs))
    if engine == "spark":
        if not spark_available():
            raise EngineUnavailableError(
                "engine='spark' requested but pyspark is not installed "
                "(pip install -r requirements-bigdata.txt, which pins "
                "pyspark>=3.5,<4.0 -- 4.0.0 has a Windows worker bug)."
            )
        from .spark_engine import SparkEngine

        try:
            return SparkEngine(**_filter_kwargs(SparkEngine, kwargs))
        except Exception as exc:  # pragma: no cover - environment dependent
            raise EngineUnavailableError(f"Spark failed to start: {exc}") from exc
    if engine == "auto":
        if spark_available():
            try:
                from .spark_engine import SparkEngine

                return SparkEngine(**_filter_kwargs(SparkEngine, kwargs))
            except Exception:
                pass
        return LocalEngine(**_filter_kwargs(LocalEngine, kwargs))
    raise ValueError(f"Unknown engine {engine!r}; expected spark, local, or auto.")


def describe_backends() -> dict:
    """Report which backends are available (for CLIs and the report)."""

    return {
        "local": True,
        "spark": spark_available(),
    }


def _filter_kwargs(cls: type, kwargs: dict) -> dict:
    """Keep only constructor kwargs a backend understands (with sane shared aliases)."""

    import inspect

    accepted = set(inspect.signature(cls.__init__).parameters) - {"self"}
    return {key: value for key, value in kwargs.items() if key in accepted}
