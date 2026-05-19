"""
core/downloader.py
Single-responsibility async file downloader.

Responsibilities:
  - Acquire semaphore slot before each real HTTP request
  - Dedup via shared asset_map (url → filepath)
  - Derive filename and extension from Content-Type + URL
  - Retry with backoff
  - Return DownloadResult (never raises)

Not responsible for: path rewriting, CSS parsing, crawling.
"""

import asyncio
import fnmatch
import os
from typing import Callable, Optional

import aiohttp

from ..utils.helpers import (
    extension_for, is_valid_asset_type, safe_filename,
    strip_query, unique_filepath, log_line
)
from .models import DownloadResult, ReplicatorConfig
from .ratelimiter import DomainRateLimiter
from urllib.parse import urlparse


class Downloader:
    """
    Manages all HTTP asset downloads for a single replication job.

    Create one instance per job; it holds a shared asset_map and semaphore.
    """

    def __init__(
        self,
        cfg:           ReplicatorConfig,
        asset_map:     dict[str, str],
        semaphore:     asyncio.Semaphore,
        log:           Callable[[str], None],
        cancelled:     Callable[[], bool],
        on_downloaded: Optional[Callable[[], None]] = None,
        rate_limiter:  Optional[DomainRateLimiter]  = None,
    ):
        self._cfg           = cfg
        self._asset_map     = asset_map
        self._sem           = semaphore
        self._log           = log
        self._cancelled     = cancelled
        self._on_downloaded = on_downloaded
        self._rate_limiter  = rate_limiter

    # ── public API ───────────────────────────────────────────────────────────

    async def fetch(
        self,
        session:    aiohttp.ClientSession,
        url:        str,
        folder:     str,
        asset_type: str,
        retries:    int = 0,
    ) -> DownloadResult:
        """
        Download *url* into *folder* as *asset_type*.
        Returns a DownloadResult — never raises.
        """
        if self._cancelled():
            return DownloadResult(url=url, filepath=None, error="cancelled")

        # ── Blacklist check ─────────────────────────────────────────────────
        if self._is_blacklisted(url):
            self._log(f"⛔ Blacklisted: {url}")
            return DownloadResult(url=url, filepath=None, error="blacklisted")

        # ── Deduplication ────────────────────────────────────────────────────
        cache_key = strip_query(url)
        if cache_key in self._asset_map:
            cached_path = self._asset_map[cache_key]
            self._log(f"⚡ Dedup ({asset_type}): {os.path.basename(cached_path)}")
            return DownloadResult(url=url, filepath=cached_path, cached=True)

        # ── Resume: skip if file already exists on disk from a previous run ──
        # We derive what the local filename would be and check for it.
        # If found, register in asset_map and return without hitting the network.
        _preview_ext  = extension_for("", url, asset_type)
        _preview_name = self._derive_filename(url, _preview_ext, asset_type)
        # Folder may not be known yet, but the caller passes it — check there
        _preview_path = os.path.join(folder, _preview_name)
        if os.path.exists(_preview_path) and os.path.getsize(_preview_path) > 0:
            self._log(f"↩ Resume ({asset_type}): {_preview_name}")
            self._asset_map[cache_key] = _preview_path
            return DownloadResult(url=url, filepath=_preview_path, cached=True)

        # ── HTTP fetch (semaphore-gated, then per-domain rate-limited) ────────
        # Semaphore controls total concurrent connections.
        # Rate limiter then enforces per-domain minimum delay inside the slot.
        timeout = aiohttp.ClientTimeout(total=self._cfg.timeout)
        try:
            async with self._sem:
                # Per-domain politeness delay (acquires domain lock, waits, then
                # records the request time on exit — serialising same-domain hits)
                if self._rate_limiter:
                    async with self._rate_limiter.acquire(url):
                        async with session.get(url, timeout=timeout, allow_redirects=True) as resp:
                            if resp.status != 200:
                                self._log(f"✗ HTTP {resp.status}: {url}")
                                return DownloadResult(url=url, filepath=None,
                                                      error=f"HTTP {resp.status}")
                            content_type = resp.headers.get("Content-Type", "")
                            ext = extension_for(content_type, url, asset_type)
                            if not is_valid_asset_type(content_type, url, asset_type):
                                self._log(f"✗ Wrong content-type for {asset_type}: {content_type} — {url}")
                                return DownloadResult(url=url, filepath=None,
                                                      error=f"content-type mismatch: {content_type}")
                            filename = self._derive_filename(url, ext, asset_type)
                            filepath = unique_filepath(os.path.join(folder, filename))
                            content  = await resp.read()
                else:
                    async with session.get(url, timeout=timeout, allow_redirects=True) as resp:
                        if resp.status != 200:
                            self._log(f"✗ HTTP {resp.status}: {url}")
                            return DownloadResult(url=url, filepath=None,
                                                  error=f"HTTP {resp.status}")
                        content_type = resp.headers.get("Content-Type", "")
                        ext = extension_for(content_type, url, asset_type)
                        if not is_valid_asset_type(content_type, url, asset_type):
                            self._log(f"✗ Wrong content-type for {asset_type}: {content_type} — {url}")
                            return DownloadResult(url=url, filepath=None,
                                                  error=f"content-type mismatch: {content_type}")
                        filename = self._derive_filename(url, ext, asset_type)
                        filepath = unique_filepath(os.path.join(folder, filename))
                        content  = await resp.read()

        except asyncio.CancelledError:
            return DownloadResult(url=url, filepath=None, error="cancelled")
        except Exception as exc:
            return await self._retry(session, url, folder, asset_type, retries, exc)

        # ── Write file ───────────────────────────────────────────────────────
        try:
            os.makedirs(folder, exist_ok=True)
            with open(filepath, "wb") as fh:
                fh.write(content)
        except OSError as exc:
            self._log(f"✗ Write failed: {filepath} — {exc}")
            return DownloadResult(url=url, filepath=None, error=str(exc))

        self._log(f"✓ {asset_type}: {os.path.basename(filepath)}")
        self._asset_map[cache_key] = filepath
        if self._on_downloaded:
            self._on_downloaded()
        return DownloadResult(url=url, filepath=filepath)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _is_blacklisted(self, url: str) -> bool:
        """Return True if *url* matches any blacklist glob pattern.
        Query strings are stripped before matching so *.mp4 catches
        video.mp4?t=10 correctly."""
        if not self._cfg.blacklist:
            return False
        # Strip query string for matching — the path is what matters
        url_path = url.split("?")[0].lower()
        return any(fnmatch.fnmatch(url_path, pat.lower())
                   for pat in self._cfg.blacklist)

    def _derive_filename(self, url: str, extension: str, asset_type: str) -> str:
        raw = os.path.basename(urlparse(url).path.split("?")[0])
        if not raw or not os.path.splitext(raw)[1]:
            return f"{asset_type}_{hash(url) % 100_000}{extension}"
        filename = safe_filename(raw)
        # Enforce correct extension for asset types where it matters
        base, existing_ext = os.path.splitext(filename)
        if existing_ext.lower() != extension and extension in {
            ".webp", ".woff", ".woff2", ".ttf", ".ico",
        }:
            filename = f"{base}{extension}"
        return filename

    async def _retry(
        self,
        session:    aiohttp.ClientSession,
        url:        str,
        folder:     str,
        asset_type: str,
        retries:    int,
        exc:        Exception,
    ) -> DownloadResult:
        if retries < self._cfg.max_retries and not self._cancelled():
            self._log(f"↻ Retry {retries + 1}/{self._cfg.max_retries}: {url}")
            await asyncio.sleep(2 ** retries)          # exponential back-off
            return await self.fetch(session, url, folder, asset_type, retries + 1)
        self._log(f"✗ Failed after {self._cfg.max_retries} retries: {url} — {exc}")
        return DownloadResult(url=url, filepath=None, error=str(exc))
