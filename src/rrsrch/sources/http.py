"""One polite HTTP client for every outside source.

Every request rrsrch makes leaves the machine, so they all go through here:
one place to see what is sent, one User-Agent, one retry policy.
"""
from __future__ import annotations

import time

import httpx

USER_AGENT = "rrsrch/0.1 (+https://rrsrch.com; research tool)"


class Http:
    def __init__(self, min_interval: float = 0.15, retries: int = 3, timeout: float = 30.0):
        self._client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=timeout,
                                    follow_redirects=True)
        self._min_interval = min_interval
        self._retries = retries
        self._last = 0.0

    def get(self, url: str, params: dict | None = None) -> httpx.Response:
        for attempt in range(self._retries):
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                r = self._client.get(url, params=params)
            except httpx.TransportError:
                if attempt == self._retries - 1:
                    raise
            else:
                # 404 is an answer ("no such record"), not a failure to retry
                if r.status_code < 500 and r.status_code != 429:
                    return r
                if attempt == self._retries - 1:
                    r.raise_for_status()
            time.sleep(2 ** attempt)
        raise RuntimeError("unreachable")
