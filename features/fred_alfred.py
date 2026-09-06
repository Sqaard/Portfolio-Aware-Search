"""ALFRED (ArchivaL FRED) client: true point-in-time vintages for macro series.

Why this module exists
----------------------
``build_official_macro_documents`` originally derived ``available_at`` from a
hardcoded ``release_lag_days`` estimate and took values from
``fredgraph.csv``, which always serves *today's revised* numbers. Two residual
lookahead leaks follow from that:

1. **Timing leak** -- the estimated lag can be shorter than the real one. The
   daily Treasury series were assumed to publish 1 day after the observation;
   ALFRED shows the true first release lands 2-6 days later.
2. **Revision leak** -- a 2010 CPI print retrieved today is the value *after*
   a decade of seasonal-adjustment revisions, which was not knowable in 2010.

ALFRED fixes both: every observation carries the ``realtime_start`` on which it
was first published, together with the value as first published.

API notes (hard-won)
--------------------
* **User-Agent matters.** FRED stalls browser-like UA strings on these
  endpoints -- a read simply hangs until timeout (~90s). A plain tool UA is
  answered in ~1.5s. Do not "improve" :data:`DEFAULT_USER_AGENT` into
  something Chrome-shaped.
* ``output_type=4`` ("observations, initial release only") is exactly the
  wanted projection, but FRED rejects any request whose real-time window spans
  more than 2000 vintage dates. Daily series are re-published every business
  day (DGS10 has 5103 vintages), so requests are **chunked** by observation
  window and split adaptively when a chunk is still too wide.
* Coverage is partial. Some series are absent from ALFRED entirely
  (``T10Y2Y``, ``BAMLH0A0HYM2`` -- both computed series), and some have vintage
  history that starts later than the requested window. Callers must therefore
  treat the returned mapping as *sparse* and fall back to the estimated lag for
  observations it does not cover.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"

# Byte-order mark, stripped from the first key of a UTF-8-BOM .env file.
_BOM = chr(0xFEFF)

# Deliberately NOT browser-shaped -- see module docstring.
DEFAULT_USER_AGENT = "FinPortfolio-IR/1.0 (academic research)"

# FRED's documented cap on vintage dates addressable by a single output_type=4
# request. Kept as a constant so the adaptive splitter can explain itself.
MAX_VINTAGE_DATES = 2000

#: Extra real-time window appended after the observation window so that the
#: first release of a late-reported observation still falls inside it (the
#: slowest series here, CPI/INDPRO/HOUST, publish within ~45 days).
DEFAULT_SLACK_DAYS = 150


class AlfredUnavailable(RuntimeError):
    """Raised when a series has no ALFRED vintage history at all."""


@dataclass(frozen=True)
class FirstRelease:
    """The first published state of a single observation."""

    observation_date: date
    release_date: date
    value: float | None

    @property
    def lag_days(self) -> int:
        return (self.release_date - self.observation_date).days


def load_env_file(path: Path | str) -> None:
    """Populate ``os.environ`` from a ``.env`` file (same rules as ``web_app``).

    Existing environment variables win, so an explicitly exported key is never
    silently overridden by the file.
    """

    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lstrip(_BOM)
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def resolve_api_key(explicit: str | None = None, env_file: Path | str | None = None) -> str:
    """Return the FRED API key from an explicit value, the env, or ``.env``.

    Returns an empty string when no key is configured; callers decide whether
    that is fatal or simply means "use the estimated-lag path".
    """

    if explicit and explicit.strip():
        return explicit.strip()
    if env_file is not None:
        load_env_file(env_file)
    return os.environ.get("FRED_API_KEY", "").strip()


#: Transient HTTP statuses worth retrying (rate limit + server-side faults).
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


def _request_json(
    params: dict[str, object],
    user_agent: str,
    timeout: int,
    attempts: int = 4,
    backoff_seconds: float = 2.0,
) -> dict:
    """GET the observations endpoint, retrying transient failures.

    Without this a single timeout on the last of ~84 calls would drop a whole
    series back to the estimated-lag regime, which is exactly what this module
    exists to avoid.
    """

    request = urllib.request.Request(
        f"{OBSERVATIONS_URL}?{urlencode(params)}",
        headers={"User-Agent": user_agent, "Accept": "application/json"},
    )
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # 400s are deterministic (bad series, window too wide) -- the caller
            # inspects them, so never burn retries on one.
            if exc.code not in _RETRY_STATUSES:
                raise
            last_error = exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(backoff_seconds * (2 ** attempt))
    raise last_error if last_error else RuntimeError("request failed without an error")


def _error_message(exc: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8", "replace"))
        return str(payload.get("error_message", ""))
    except Exception:  # noqa: BLE001 - diagnostics only, never fatal
        return ""


def _parse_value(raw: str) -> float | None:
    text = str(raw).strip()
    if not text or text == ".":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _chunk_bounds(start: date, end: date, years: int) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        try:
            stop = date(cursor.year + years, cursor.month, cursor.day)
        except ValueError:  # 29 Feb in a non-leap target year
            stop = date(cursor.year + years, cursor.month, 28)
        stop = min(stop, end)
        chunks.append((cursor, stop))
        if stop >= end:
            break
        cursor = date.fromordinal(stop.toordinal() + 1)
    return chunks


def _fetch_chunk(
    series_id: str,
    obs_start: date,
    obs_end: date,
    api_key: str,
    *,
    user_agent: str,
    slack_days: int,
    timeout: int,
    sleep_seconds: float,
    depth: int = 0,
) -> list[dict]:
    """Fetch initial releases for one observation window, splitting if too wide.

    The real-time window starts at ``obs_start`` because an observation can
    never be released before it happened -- so the first release seen inside
    the window really is *the* first release, not a revision that merely
    happens to be the earliest one visible.
    """

    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": obs_start.isoformat(),
        "observation_end": obs_end.isoformat(),
        "realtime_start": obs_start.isoformat(),
        "realtime_end": date.fromordinal(obs_end.toordinal() + slack_days).isoformat(),
        "output_type": 4,  # observations, initial release only
    }
    try:
        payload = _request_json(params, user_agent, timeout)
    except urllib.error.HTTPError as exc:
        message = _error_message(exc)
        if "does not exist in ALFRED" in message:
            raise AlfredUnavailable(f"{series_id}: {message}") from exc
        too_many = "exceeds the maximum number of vintage dates" in message
        if too_many and obs_start < obs_end and depth < 6:
            middle = date.fromordinal((obs_start.toordinal() + obs_end.toordinal()) // 2)
            left = _fetch_chunk(
                series_id, obs_start, middle, api_key,
                user_agent=user_agent, slack_days=slack_days, timeout=timeout,
                sleep_seconds=sleep_seconds, depth=depth + 1,
            )
            right = _fetch_chunk(
                series_id, date.fromordinal(middle.toordinal() + 1), obs_end, api_key,
                user_agent=user_agent, slack_days=slack_days, timeout=timeout,
                sleep_seconds=sleep_seconds, depth=depth + 1,
            )
            return left + right
        raise
    if sleep_seconds > 0:
        time.sleep(sleep_seconds)  # stay well inside FRED's 120 req/min budget
    return list(payload.get("observations", []))


def fetch_first_releases(
    series_id: str,
    start_date: str,
    end_date: str,
    api_key: str,
    *,
    user_agent: str = DEFAULT_USER_AGENT,
    chunk_years: int = 2,
    slack_days: int = DEFAULT_SLACK_DAYS,
    timeout: int = 120,
    sleep_seconds: float = 0.1,
    failures: list[str] | None = None,
) -> dict[str, FirstRelease]:
    """Return ``{observation_date_iso: FirstRelease}`` for one series.

    The mapping is **sparse**: observations ALFRED has no vintage record for
    are simply absent. Raises :class:`AlfredUnavailable` when the whole series
    is missing from ALFRED.

    A chunk that keeps failing after retries is appended to ``failures`` (when a
    list is supplied) and skipped, so a transient fault degrades only the
    observations in that window instead of the entire series.
    """

    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    releases: dict[str, FirstRelease] = {}
    if failures is None:
        failures = []
    for obs_start, obs_end in _chunk_bounds(start, end, chunk_years):
        try:
            rows = _fetch_chunk(
                series_id, obs_start, obs_end, api_key,
                user_agent=user_agent, slack_days=slack_days,
                timeout=timeout, sleep_seconds=sleep_seconds,
            )
        except AlfredUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - keep the chunks that did work
            failures.append(
                f"{obs_start.isoformat()}..{obs_end.isoformat()}: {type(exc).__name__}: {str(exc)[:120]}"
            )
            continue
        for row in rows:
            observation_date = date.fromisoformat(str(row["date"]))
            release_date = date.fromisoformat(str(row["realtime_start"]))
            if release_date < observation_date:
                # A vintage recorded before the observation itself cannot be a
                # genuine release date; leave it to the estimated-lag path.
                continue
            key = observation_date.isoformat()
            previous = releases.get(key)
            if previous is not None and previous.release_date <= release_date:
                continue
            releases[key] = FirstRelease(
                observation_date=observation_date,
                release_date=release_date,
                value=_parse_value(row.get("value", "")),
            )
    return releases
