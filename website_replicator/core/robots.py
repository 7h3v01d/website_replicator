"""
core/robots.py
robots.txt fetching and URL permission checking.

Wraps Python's stdlib urllib.robotparser so the rest of the codebase
never touches it directly — we can swap the implementation without
touching crawer.py or replicator.py.

Usage::

    checker = await RobotsChecker.build(
        base_url = "https://example.com",
        user_agent = "Mozilla/5.0 ...",
        session    = aiohttp_session,
        timeout    = aiohttp_timeout,
        log        = log_fn,
    )
    if checker.allowed("https://example.com/private/page"):
        ...  # safe to fetch
"""

from __future__ import annotations

import asyncio
from typing import Callable, Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser


class RobotsChecker:
    """
    Async-friendly robots.txt checker for one origin.

    - Fetches and parses robots.txt once on construction.
    - If the file is absent or unreachable, defaults to "allow all".
    - Respects the Crawl-delay directive via `crawl_delay` property.
    """

    def __init__(
        self,
        base_url:  str,
        parser:    Optional[RobotFileParser],
        user_agent: str,
        log:       Callable[[str], None],
    ) -> None:
        self._base       = base_url
        self._parser     = parser
        self._ua         = user_agent
        self._log        = log
        self._base_netloc = urlparse(base_url).netloc

    # ── factory ──────────────────────────────────────────────────────────────

    @classmethod
    async def build(
        cls,
        base_url:   str,
        user_agent: str,
        session,                             # aiohttp.ClientSession
        timeout,                             # aiohttp.ClientTimeout
        log:        Callable[[str], None],
    ) -> "RobotsChecker":
        """
        Fetch and parse robots.txt for *base_url*.
        Returns a checker even on failure (defaults to allow-all).
        """
        parsed    = urlparse(base_url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

        parser = RobotFileParser()
        parser.set_url(robots_url)

        try:
            async with session.get(robots_url, timeout=timeout,
                                   allow_redirects=True) as resp:
                if resp.status == 200:
                    text = await resp.text(errors="replace")
                    parser.parse(text.splitlines())
                    log(f"robots.txt loaded from {robots_url}")
                elif resp.status == 404:
                    log(f"No robots.txt found at {robots_url} — allowing all")
                    parser = None
                else:
                    log(f"robots.txt fetch returned HTTP {resp.status} — allowing all")
                    parser = None
        except Exception as exc:
            log(f"Could not fetch robots.txt ({exc}) — allowing all")
            parser = None

        return cls(
            base_url   = base_url,
            parser     = parser,
            user_agent = user_agent,
            log        = log,
        )

    # ── public API ────────────────────────────────────────────────────────────

    def allowed(self, url: str) -> bool:
        """
        Return True if *url* is allowed to be fetched.

        - URLs on a different domain are always allowed (they have their own robots.txt).
        - If no parser (robots.txt absent / unreachable), allow all.
        """
        if urlparse(url).netloc != self._base_netloc:
            return True
        if self._parser is None:
            return True
        return self._parser.can_fetch(self._ua, url)

    @property
    def crawl_delay(self) -> float:
        """
        Return the Crawl-delay in seconds (0.0 if not specified).
        Capped at 10 seconds to avoid runaway politeness.
        """
        if self._parser is None:
            return 0.0
        delay = self._parser.crawl_delay(self._ua)
        if delay is None:
            return 0.0
        return min(float(delay), 10.0)

    def disallowed_paths(self) -> list[str]:
        """Return a list of Disallow paths for the configured user-agent."""
        if self._parser is None:
            return []
        try:
            # RobotFileParser doesn't expose disallowed paths publicly,
            # so we inspect the internal entries list.
            entries = getattr(self._parser, "entries", [])
            paths   = []
            for entry in entries:
                ua_matches = any(
                    ua.useragent in ("*", self._ua.split("/")[0])
                    for ua in entry.useragents
                )
                if ua_matches:
                    for rule in entry.rulelines:
                        if not rule.allowance:
                            paths.append(rule.path)
            return paths
        except Exception:
            return []
