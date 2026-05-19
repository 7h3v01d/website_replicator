"""
core/replicator.py
Orchestration layer — ties Downloader + Crawler together for one job.

All communication with the outside world (UI or CLI) goes through
plain Python callbacks, so this module has zero Qt dependency.

Callbacks protocol:
  log(msg: str)              — append a log line
  set_progress(pct: int)     — 0-100
  set_status(msg, colour)    — human-readable status + colour hint
"""

from __future__ import annotations

import asyncio
import os
from typing import Callable, Optional

import aiohttp
from ..utils.css_parser import extract_font_urls, extract_image_urls, rewrite_url
from ..utils.helpers import strip_query, ts
from .crawler import (
    AssetManifest, PageRecord,
    collect_internal_links, discover_pages,
    extract_assets, patch_html,
)
from .downloader import Downloader
from .exporter import BrokenLinkReport, zip_output_dir
from .ratelimiter import DomainRateLimiter
from .models import AnalysisResult, ReplicatorConfig
from urllib.parse import urljoin

import validators
from bs4 import BeautifulSoup



# ---------------------------------------------------------------------------
# Analyser
# ---------------------------------------------------------------------------

async def analyse(
    url:    str,
    cfg:    ReplicatorConfig,
    log:    Callable[[str], None],
    status: Callable[[str, str], None],
) -> Optional[AnalysisResult]:
    """
    Fetch *url* and build an AnalysisResult.
    Returns None on failure.
    """
    headers = {"User-Agent": cfg.user_agent}
    timeout = aiohttp.ClientTimeout(total=cfg.timeout)

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=timeout, allow_redirects=True) as resp:
                if resp.status != 200:
                    log(f"Failed to access {url}. Status: {resp.status}")
                    status(f"Status: Failed (HTTP {resp.status})", "#dc2626")
                    return None

                cors = (
                    "Access-Control-Allow-Origin" not in resp.headers
                    or resp.headers["Access-Control-Allow-Origin"] not in ["*", url]
                )
                text = await resp.text()
                soup = BeautifulSoup(text, "html.parser")

    except Exception as exc:
        log(f"Error analyzing website: {exc}")
        status("Status: Analysis failed", "#dc2626")
        return None

    css_links        = soup.find_all("link", rel="stylesheet")
    js_scripts       = soup.find_all("script", src=True)
    images           = soup.find_all("img", src=True)
    picture_elements = soup.find_all("picture")
    misc_links       = soup.find_all("link", rel=["manifest", "icon"])
    iframes          = soup.find_all("iframe", src=True)
    inline_scripts   = soup.find_all("script", src=False)

    # WebP count
    webp_urls: set[str] = set()
    for img in images:
        if img.get("src", "").lower().endswith(".webp"):
            webp_urls.add(urljoin(url, img["src"]))
        for part in img.get("srcset", "").split(","):
            parts = part.strip().split()
            if not parts:
                continue
            part = parts[0]
            if part.lower().endswith(".webp"):
                webp_urls.add(urljoin(url, part))

    # Dynamic indicators
    dynamic = sum(
        1 for s in inline_scripts
        if s.string and any(
            kw in s.string.lower()
            for kw in ["fetch(", "xmlhttprequest", "$.ajax", "axios.", "/api/"]
        )
    )

    # External domains
    ext_domains: set[str] = set()
    from urllib.parse import urlparse as _up
    base_netloc = _up(url).netloc
    for el in css_links + js_scripts + images + misc_links + iframes:
        src = el.get("href") or el.get("src")
        if src:
            a = urljoin(url, src)
            n = _up(a).netloc
            if n and n != base_netloc:
                ext_domains.add(n)

    # Feasibility
    css_count = len(css_links)
    js_count  = len(js_scripts)
    img_count = len(images)
    pic_count = len(picture_elements)
    misc_count = len(misc_links) + len(iframes)
    total = css_count + js_count + img_count + pic_count + misc_count

    if cors and ext_domains:
        feasibility, reason = "None", "Strict CORS restrictions and external domains detected."
    elif dynamic > 2 or len(ext_domains) > 3:
        feasibility, reason = "Partial", "Dynamic content or multiple external domains detected."
    elif total == 0:
        feasibility, reason = "None", "No static assets found."
    else:
        feasibility, reason = "Complete", "Mostly static content detected."

    result = AnalysisResult(
        url              = url,
        css_count        = css_count,
        js_count         = js_count,
        img_count        = img_count,
        webp_count       = len(webp_urls),
        picture_count    = pic_count,
        misc_count       = misc_count,
        inline_count     = len(inline_scripts),
        internal_links   = collect_internal_links(soup, url),
        external_domains = ext_domains,
        cors_restrictive = cors,
        dynamic_indicators = dynamic,
        feasibility      = feasibility,
        reason           = reason,
        soup             = soup,
    )

    for line in result.summary_lines():
        log(line)

    status(f"Status: Analysis complete ({feasibility})", "#16a34a")
    return result


# ---------------------------------------------------------------------------
# Replicator
# ---------------------------------------------------------------------------

class Replicator:
    """
    Orchestrates one full replication job.

    Usage::

        rep = Replicator(cfg, log_fn, progress_fn, status_fn)
        await rep.run(url, analysis_result, passthru, output_dir, crawl_depth)
    """

    def __init__(
        self,
        cfg:      ReplicatorConfig,
        log:      Callable[[str], None],
        progress: Callable[[int], None],
        status:   Callable[[str, str], None],
    ):
        self._cfg      = cfg
        self._log      = log
        self._progress = progress
        self._status   = status
        self._cancelled          = False
        self._asset_map: dict[str, str] = {}
        self._total_assets       = 0
        self._done_assets        = 0
        self.broken_link_report: BrokenLinkReport | None = None
        # Optional page-progress callbacks (set by UI after construction)
        self._on_pages_counted: Optional[Callable[[int], None]] = None
        self._on_page_done: Optional[Callable[[int, int], None]] = None

    def cancel(self) -> None:
        self._cancelled = True

    def set_page_callbacks(
        self,
        on_counted: Optional[Callable[[int], None]],
        on_done:    Optional[Callable[[int, int], None]],
    ) -> None:
        """
        Register page-progress callbacks.
        on_counted(total)     — called once after page discovery
        on_done(done, total)  — called after each page completes
        """
        self._on_pages_counted = on_counted
        self._on_page_done     = on_done

    def _is_cancelled(self) -> bool:
        return self._cancelled

    async def run(
        self,
        url:         str,
        analysis:    Optional[AnalysisResult],
        passthru:    bool,
        output_dir:  str,
        crawl_depth: int,
    ) -> bool:
        """
        Execute the full replication.  Returns True on success.
        """
        self._cancelled   = False
        self._asset_map   = {}
        self._done_assets = 0
        self._total_assets = 0
        self.broken_link_report = BrokenLinkReport(url, output_dir)

        semaphore = asyncio.Semaphore(self._cfg.max_concurrent)
        headers   = {"User-Agent": self._cfg.user_agent}
        timeout   = aiohttp.ClientTimeout(total=self._cfg.timeout)
        os.makedirs(output_dir, exist_ok=True)

        # ── Resume: load existing asset map from a previous run ──────────────
        manifest_path = os.path.join(output_dir, ".replicator_manifest.json")
        if os.path.exists(manifest_path):
            try:
                import json as _json
                with open(manifest_path, encoding="utf-8") as _f:
                    saved = _json.load(_f)
                # Only reuse entries where the local file still exists
                self._asset_map = {
                    k: v for k, v in saved.items() if os.path.exists(v)
                }
                self._log(f"↩ Resume: loaded {len(self._asset_map)} cached assets from previous run")
            except Exception as exc:
                self._log(f"Could not load resume manifest: {exc}")

        self._status("Status: Starting replication…", "#2563eb")

        try:
            async with aiohttp.ClientSession(headers=headers) as session:

                # ── Per-domain rate limiter ──────────────────────────────────
                from urllib.parse import urlparse as _urlparse
                rate_limiter = DomainRateLimiter(
                    default_delay=self._cfg.domain_delay
                )
                # robots.txt crawl-delay is wired in after page discovery
                # (discover_pages internally fetches robots.txt and returns pages)

                # ── Page discovery (respects robots.txt) ─────────────────────
                pages = await discover_pages(
                    start_url   = url,
                    crawl_depth = crawl_depth,
                    output_dir  = output_dir,
                    session     = session,
                    timeout     = timeout,
                    log         = self._log,
                    cancelled   = self._is_cancelled,
                    user_agent  = self._cfg.user_agent,
                    rate_limiter= rate_limiter,
                )

                if not pages:
                    self._log("No pages discovered — aborting.")
                    self._status("Status: No pages found", "#dc2626")
                    return False

                # Seed first page with cached soup from analysis if available
                if analysis and analysis.soup and pages[0].url == url:
                    pages[0].soup = analysis.soup

                # Notify caller of total page count for queue display
                if self._on_pages_counted is not None:
                    self._on_pages_counted(len(pages))

                # Count total assets for progress bar
                self._total_assets = sum(
                    len(p.soup.find_all("link", rel="stylesheet")) +
                    len(p.soup.find_all("script", src=True)) +
                    len(p.soup.find_all("img", src=True)) +
                    len(p.soup.find_all("picture")) +
                    len(p.soup.find_all("link", rel=["manifest", "icon"])) +
                    len(p.soup.find_all("iframe", src=True))
                    for p in pages
                )

                def _on_downloaded() -> None:
                    """Called by Downloader after each real (non-dedup) write."""
                    self._done_assets += 1
                    if self._total_assets > 0:
                        pct = min(int(self._done_assets / self._total_assets * 100), 100)
                        self._progress(pct)

                dl = Downloader(
                    cfg           = self._cfg,
                    asset_map     = self._asset_map,
                    semaphore     = semaphore,
                    log           = self._log,
                    cancelled     = self._is_cancelled,
                    on_downloaded = _on_downloaded,
                    rate_limiter  = rate_limiter,
                )

                # ── Process each page ────────────────────────────────────────
                for i, page in enumerate(pages):
                    if self._cancelled:
                        break
                    remaining = self._total_assets - self._done_assets
                    self._log(
                        f"Page {i+1}/{len(pages)} — "
                        f"{self._done_assets}/{self._total_assets} done"
                        f" ({remaining} remaining) — {page.url}"
                    )
                    self._status(
                        f"Page {i+1}/{len(pages)} — {remaining} assets remaining",
                        "#2563eb"
                    )
                    await self._process_page(session, page, dl, output_dir,
                                             passthru, timeout,
                                             self.broken_link_report)
                    if self._on_page_done is not None:
                        self._on_page_done(i + 1, len(pages))

            if self._cancelled:
                self._log("Replication cancelled.")
                self._status("Status: Cancelled", "#d97706")
                return False

            # ── Save resume manifest ─────────────────────────────────────
            try:
                import json as _json
                with open(manifest_path, "w", encoding="utf-8") as _f:
                    _json.dump(self._asset_map, _f, indent=2)
                self._log(f"💾 Manifest saved ({len(self._asset_map)} assets)")
            except Exception as exc:
                self._log(f"Could not save manifest: {exc}")

            # ── Write broken link report ──────────────────────────────────────
            if self.broken_link_report and self.broken_link_report.has_errors:
                report_path = self.broken_link_report.write()
                self._log(f"⚠ Broken links found — report saved to {report_path}")
                for line in self.broken_link_report.summary_lines():
                    self._log(line)
            else:
                self._log("✓ No broken links detected")

            self._log(f"✓ Replicated {len(pages)} page(s) to {output_dir}")
            self._log("Use 'Preview Site' to open in your browser.")
            self._status(f"Status: Done — {len(pages)} page(s)", "#16a34a")
            return True

        except Exception as exc:
            self._log(f"Replication error: {exc}")
            self._status("Status: Error occurred", "#dc2626")
            return False

    # ── per-page logic ───────────────────────────────────────────────────────

    async def _process_page(
        self,
        session:    aiohttp.ClientSession,
        page:       PageRecord,
        dl:         Downloader,
        output_dir: str,
        passthru:   bool,
        timeout:    aiohttp.ClientTimeout,
        blr:        "BrokenLinkReport | None" = None,
    ) -> None:
        css_dir  = os.path.join(output_dir, "css")
        js_dir   = os.path.join(output_dir, "js")
        img_dir  = os.path.join(output_dir, "img")
        font_dir = os.path.join(output_dir, "fonts")
        misc_dir = os.path.join(output_dir, "misc")
        for d in [css_dir, js_dir, font_dir, misc_dir]:
            os.makedirs(d, exist_ok=True)
        if not passthru:
            os.makedirs(img_dir, exist_ok=True)
        # Ensure page-specific output directory exists (crawler no longer does this)
        os.makedirs(page.out_dir, exist_ok=True)

        manifest = extract_assets(page.soup, page.url, passthru)

        # ── Parallel downloads ────────────────────────────────────────────
        css_tasks  = {u: dl.fetch(session, u, css_dir,  "css")  for u in manifest.css_map}
        js_tasks   = {u: dl.fetch(session, u, js_dir,   "js")   for u in manifest.js_map}
        img_tasks  = {u: dl.fetch(session, u, img_dir,  "img")  for u in manifest.img_urls}
        misc_tasks = {u: dl.fetch(session, u, misc_dir, "misc") for u in manifest.misc_map}

        css_res  = dict(zip(css_tasks,  await asyncio.gather(*css_tasks.values(),  return_exceptions=True)))
        js_res   = dict(zip(js_tasks,   await asyncio.gather(*js_tasks.values(),   return_exceptions=True)))
        img_res  = dict(zip(img_tasks,  await asyncio.gather(*img_tasks.values(),  return_exceptions=True))) if not passthru else {}
        misc_res = dict(zip(misc_tasks, await asyncio.gather(*misc_tasks.values(), return_exceptions=True)))

        # Unwrap DownloadResult → filepath (or None)
        def fp(results: dict, url: str) -> str | None:
            r = results.get(url)
            if r is None:
                return None
            if isinstance(r, Exception):
                return None
            return r.filepath  # DownloadResult

        css_fps  = {u: fp(css_res,  u) for u in css_tasks}
        js_fps   = {u: fp(js_res,   u) for u in js_tasks}
        img_fps  = {u: fp(img_res,  u) for u in img_tasks}
        misc_fps = {u: fp(misc_res, u) for u in misc_tasks}

        # ── Record broken links ───────────────────────────────────────────────
        if blr is not None:
            for atype, fps_dict, res_dict in [
                ("css",  css_fps,  css_res),
                ("js",   js_fps,   js_res),
                ("img",  img_fps,  img_res),
                ("misc", misc_fps, misc_res),
            ]:
                for u, local_fp in fps_dict.items():
                    if local_fp is None:
                        raw = res_dict.get(u)
                        err = raw.error if hasattr(raw, "error") and raw else "unknown"
                        blr.record(url=u, page_url=page.url, asset_type=atype, error=err or "failed")

        # ── CSS post-processing (fonts + bg images) ───────────────────────
        for css_url, css_fp in css_fps.items():
            if css_fp:
                await self._process_css(session, css_url, css_fp, css_dir,
                                        font_dir, misc_dir, dl, timeout)

        # ── Iframe image extraction ───────────────────────────────────────
        for misc_url, misc_fp in misc_fps.items():
            tag = manifest.misc_map.get(misc_url)
            if misc_fp and tag and tag.name == "iframe" and misc_fp.endswith(".htm"):
                await self._process_iframe(session, misc_url, misc_dir, dl, timeout)

        # ── Patch HTML ────────────────────────────────────────────────────
        patch_html(manifest, css_fps, js_fps, img_fps, misc_fps, output_dir, passthru)

        html_path = os.path.join(page.out_dir, page.html_name)
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write(page.soup.prettify())
        self._log(f"Saved: {html_path}")

    async def _process_css(
        self,
        session:  aiohttp.ClientSession,
        css_url:  str,
        css_fp:   str,
        css_dir:  str,
        font_dir: str,
        misc_dir: str,
        dl:       Downloader,
        timeout:  aiohttp.ClientTimeout,
    ) -> None:
        # Read from the already-downloaded local file — no second network hit.
        try:
            with open(css_fp, encoding="utf-8", errors="replace") as fh:
                css_content = fh.read()
        except OSError as exc:
            self._log(f"CSS read error: {css_fp} — {exc}")
            return

        url_map: dict[str, str] = {}

        # Fonts
        for font_url in extract_font_urls(css_content, css_url):
            result = await dl.fetch(session, font_url, font_dir, "font")
            if result.filepath:
                url_map[font_url] = f"/fonts/{os.path.basename(result.filepath)}"

        # Background images
        for img_url in extract_image_urls(css_content, css_url):
            result = await dl.fetch(session, img_url, misc_dir, "img")
            if result.filepath:
                url_map[img_url] = f"/misc/{os.path.basename(result.filepath)}"

        # Rewrite + save
        for original, new_path in url_map.items():
            css_content = rewrite_url(css_content, original, new_path)
            self._log(f"CSS path → {new_path}")

        try:
            with open(css_fp, "w", encoding="utf-8") as fh:
                fh.write(css_content)
        except OSError as exc:
            self._log(f"CSS write error: {css_fp} — {exc}")

    async def _process_iframe(
        self,
        session:   aiohttp.ClientSession,
        iframe_url: str,
        misc_dir:  str,
        dl:        Downloader,
        timeout:   aiohttp.ClientTimeout,
    ) -> None:
        try:
            async with session.get(iframe_url, timeout=timeout, allow_redirects=True) as resp:
                if resp.status != 200:
                    return
                content = await resp.text()
        except Exception as exc:
            self._log(f"Iframe fetch error: {iframe_url} — {exc}")
            return

        from bs4 import BeautifulSoup as BS
        soup = BS(content, "html.parser")
        for img in soup.find_all("img", src=True):
            img_url = strip_query(
                __import__("urllib.parse", fromlist=["urljoin"]).urljoin(iframe_url, img["src"])
            )
            if validators.url(img_url):
                result = await dl.fetch(session, img_url, misc_dir, "img")
                if result.filepath:
                    new = f"/misc/{os.path.basename(result.filepath)}"
                    content = content.replace(img_url, new)
                    self._log(f"Iframe image → {new}")

        from urllib.parse import urlparse as _up
        iframe_name = os.path.basename(_up(iframe_url).path)
        iframe_fp   = os.path.join(misc_dir, iframe_name)
        try:
            with open(iframe_fp, "w", encoding="utf-8") as fh:
                fh.write(content)
        except OSError as exc:
            self._log(f"Iframe write error: {iframe_fp} — {exc}")
