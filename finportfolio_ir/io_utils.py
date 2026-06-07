"""Small file IO helpers used across the FinPortfolio IR prototype."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Union


def local_project_path(path: Union[str, Path]) -> Path:
    """Return a path that can be opened reliably from this workspace.

    Some Windows/OneDrive setups report absolute Cyrillic paths as existing but
    fail on open(). When the path points inside FinPortfolio_IR, prefer an
    equivalent relative path from the current working directory.
    """

    candidate = Path(path)
    if not candidate.is_absolute():
        return candidate
    try:
        relative_to_cwd = candidate.relative_to(Path.cwd().resolve())
    except ValueError:
        relative_to_cwd = None
    if relative_to_cwd is not None and (
        relative_to_cwd.exists() or relative_to_cwd.parent.exists()
    ):
        return relative_to_cwd
    parts = candidate.parts
    if "FinPortfolio_IR" not in parts:
        return candidate
    index = parts.index("FinPortfolio_IR")
    for rel_parts in (parts[index + 1 :], parts[index:]):
        if not rel_parts:
            continue
        relative = Path(*rel_parts)
        if relative.exists() or relative.parent.exists():
            return relative
    return candidate


def read_jsonl(path: Union[str, Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    path = local_project_path(path)
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
    return records


def write_jsonl(path: Union[str, Path], records: Iterable[dict[str, Any]]) -> None:
    path = local_project_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=False) + "\n")


def load_yaml(path: Union[str, Path]) -> dict[str, Any]:
    path = local_project_path(path)
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "PyYAML is required for YAML configs. Install requirements.txt or use JSON input."
        ) from exc

    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded or {}
