"""Fetching with a disk cache.  (Owner: Dinuka)

Every adapter goes through here so that (a) a demo can run with the network unplugged once the data
has been fetched, (b) tests never touch the network, and (c) a slow public server is hit once.

The cache is ``data/cache/http/`` (git-ignored). A cached response is used until it is older than
``max_age_s`` (``None`` means forever, which is right for history that never changes).
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache" / "http"
USER_AGENT = "ATLAS-disaster-response/0.1 (university project; contact via repository)"


class DatasetError(RuntimeError):
    """A source could not be reached, or returned something we do not understand."""


def _key(url: str, params: dict[str, Any] | None) -> str:
    full = url + ("?" + urllib.parse.urlencode(sorted(params.items())) if params else "")
    return hashlib.sha256(full.encode()).hexdigest()[:40]


def fetch(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    offline: bool = False,
    max_age_s: float | None = None,
    timeout: float = 45,
    retries: int = 2,
    cache_dir: Path | None = None,
    headers: dict[str, str] | None = None,
) -> bytes:
    """GET ``url`` (with ``params``), through the cache. ``offline=True`` never uses the network."""
    directory = cache_dir or CACHE_DIR
    path = directory / _key(url, params)
    if path.exists() and (max_age_s is None or time.time() - path.stat().st_mtime < max_age_s):
        return path.read_bytes()
    if offline:
        raise DatasetError(f"offline and not cached: {url}")
    full = url + ("?" + urllib.parse.urlencode(params) if params else "")
    request = urllib.request.Request(full, headers={"User-Agent": USER_AGENT, **(headers or {})})
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
            directory.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            return body
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
            time.sleep(0.5 * (attempt + 1))
    if path.exists():  # stale cache beats no data
        return path.read_bytes()
    raise DatasetError(f"could not fetch {url}: {last}")


def fetch_json(url: str, **kwargs: Any) -> Any:
    body = fetch(url, **kwargs)
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise DatasetError(f"{url} did not return JSON: {exc}") from exc
