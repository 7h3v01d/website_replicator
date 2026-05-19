"""
core/ratelimiter.py
Per-domain rate limiting for polite crawling.

A single DomainRateLimiter instance is shared across all Downloader calls
in one replication job.  Each domain gets its own asyncio.Lock so concurrent
downloads to different domains proceed in parallel while the same domain is
serialised and throttled.

Design choices:
  - Lock per domain (not a global lock) — maximises concurrency across domains
  - Minimum delay enforced between consecutive requests to the same domain
  - robots.txt Crawl-delay is respected as a floor via set_crawl_delay()
  - Zero delay (default) = no throttling, backward-compatible
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlparse


class DomainRateLimiter:
    """
    Enforces a minimum delay between consecutive HTTP requests to the same domain.

    Usage::

        limiter = DomainRateLimiter(default_delay=1.0)
        limiter.set_crawl_delay("example.com", 3.0)   # from robots.txt
        async with limiter.acquire("https://example.com/img.png"):
            async with session.get(...) as resp:
                ...
    """

    def __init__(self, default_delay: float = 0.0) -> None:
        """
        default_delay: seconds to wait between requests to the same domain.
                       0.0 = no throttling (backward-compatible default).
        """
        self._default_delay  = default_delay
        self._locks:         dict[str, asyncio.Lock]  = {}
        self._last_request:  dict[str, float]         = {}
        self._domain_delays: dict[str, float]         = {}

    def set_crawl_delay(self, domain: str, delay: float) -> None:
        """
        Set a per-domain delay (e.g. from robots.txt Crawl-delay).
        The effective delay for the domain will be max(default, crawl_delay).
        """
        self._domain_delays[domain] = max(self._default_delay, delay)

    def _effective_delay(self, domain: str) -> float:
        return self._domain_delays.get(domain, self._default_delay)

    def _get_lock(self, domain: str) -> asyncio.Lock:
        if domain not in self._locks:
            self._locks[domain] = asyncio.Lock()
        return self._locks[domain]

    def acquire(self, url: str) -> "_DomainContext":
        """
        Async context manager.  Acquires the domain lock, waits for any
        remaining politeness delay, then yields.  Releases on exit and
        records the request timestamp.

        Usage::

            async with limiter.acquire(url):
                # make the HTTP request here
        """
        domain = urlparse(url).netloc
        return _DomainContext(self, domain)

    async def _wait_for_domain(self, domain: str) -> None:
        """Sleep until the per-domain delay has elapsed since the last request."""
        delay = self._effective_delay(domain)
        if delay <= 0:
            return
        last = self._last_request.get(domain, 0.0)
        elapsed = time.monotonic() - last
        remaining = delay - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining)

    def _record_request(self, domain: str) -> None:
        self._last_request[domain] = time.monotonic()

    def stats(self) -> dict[str, float]:
        """Return effective delay per visited domain (for logging/testing)."""
        all_domains = set(self._locks) | set(self._domain_delays)
        return {d: self._effective_delay(d) for d in all_domains}


class _DomainContext:
    """Internal async context manager returned by DomainRateLimiter.acquire()."""

    __slots__ = ("_limiter", "_domain")

    def __init__(self, limiter: DomainRateLimiter, domain: str) -> None:
        self._limiter = limiter
        self._domain  = domain

    async def __aenter__(self) -> None:
        lock = self._limiter._get_lock(self._domain)
        await lock.acquire()
        await self._limiter._wait_for_domain(self._domain)

    async def __aexit__(self, *_) -> None:
        self._limiter._record_request(self._domain)
        self._limiter._get_lock(self._domain).release()
