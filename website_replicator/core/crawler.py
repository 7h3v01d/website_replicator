"""
core/crawler.py
Page discovery (BFS over internal links) and per-page asset extraction.

Responsibilities:
  - Collect pages to replicate up to crawl_depth
  - Extract asset URLs from HTML (CSS, JS, img, picture, misc, iframe)
  - Extract asset URLs from CSS (fonts, background images)
  - Extract image URLs from iframe HTML
  - Rewrite HTML tag attributes to local paths

Not responsible for: downloading files, writing HTML to disk.
"""

import os
from collections import deque
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse

import aiohttp
import validators
from bs4 import BeautifulSoup

from ..utils.helpers import (
    is_internal_link, strip_query, resolve_url, log_line
)
from ..utils.css_parser import (
    extract_font_urls, extract_image_urls, rewrite_url
)
from .models import ReplicatorConfig
from .robots import RobotsChecker


class PageRecord:
    """Everything we know about one discovered page."""
    __slots__ = ("url", "soup", "out_dir", "html_name")

    def __init__(self, url: str, soup: BeautifulSoup, out_dir: str, html_name: str):
        self.url       = url
        self.soup      = soup
        self.out_dir   = out_dir
        self.html_name = html_name


class AssetManifest:
    """
    All asset URLs found on one HTML page, keyed by URL for dedup-safe lookup.

    css_map  / js_map  / misc_map : url → tag  (one tag per unique URL)
    img_tags                       : list[(tag, url)] – order preserved for srcset
    picture_sources                : list[(source_tag, url)]
    img_urls                       : set of unique image URLs to download
    """
    def __init__(self):
        self.css_map:         dict[str, object] = {}
        self.js_map:          dict[str, object] = {}
        self.misc_map:        dict[str, object] = {}
        self.img_tags:        list[tuple]        = []
        self.picture_sources: list[tuple]        = []
        self.img_urls:        set[str]           = set()

    @property
    def total(self) -> int:
        return (
            len(self.css_map) + len(self.js_map) + len(self.img_urls)
            + len(self.misc_map)
        )


# ---------------------------------------------------------------------------
# HTML asset extraction
# ---------------------------------------------------------------------------

def extract_assets(soup: BeautifulSoup, page_url: str, passthru: bool) -> AssetManifest:
    """
    Walk *soup* and populate an AssetManifest.
    Does NOT download anything.
    """
    m = AssetManifest()

    for link in soup.find_all("link", rel="stylesheet"):
        href = link.get("href")
        if href:
            abs_u = strip_query(urljoin(page_url, href))
            if validators.url(abs_u) and abs_u not in m.css_map:
                m.css_map[abs_u] = link

    for script in soup.find_all("script", src=True):
        src = script.get("src")
        if src:
            abs_u = strip_query(urljoin(page_url, src))
            if validators.url(abs_u) and abs_u not in m.js_map:
                m.js_map[abs_u] = script

    for img in soup.find_all("img", src=True):
        src = img.get("src")
        if src:
            abs_u = strip_query(urljoin(page_url, src))
            if validators.url(abs_u):
                m.img_tags.append((img, abs_u))
                if not passthru:
                    m.img_urls.add(abs_u)
        if not passthru:
            for part in img.get("srcset", "").split(","):
                parts = part.strip().split()
                if not parts:
                    continue
                part = parts[0]
                if part:
                    abs_s = strip_query(urljoin(page_url, part))
                    if validators.url(abs_s):
                        m.img_urls.add(abs_s)

    for picture in soup.find_all("picture"):
        for source in picture.find_all("source"):
            raw = source.get("src") or source.get("srcset", "")
            if raw:
                abs_u = strip_query(urljoin(page_url, raw.split()[0] if " " in raw else raw))
                if validators.url(abs_u):
                    m.picture_sources.append((source, abs_u))
                    if not passthru:
                        m.img_urls.add(abs_u)

    for link in soup.find_all("link", rel=["manifest", "icon"]):
        href = link.get("href")
        if href:
            abs_u = strip_query(urljoin(page_url, href))
            if validators.url(abs_u):
                m.misc_map[abs_u] = link

    for iframe in soup.find_all("iframe", src=True):
        src = iframe.get("src")
        if src:
            abs_u = strip_query(urljoin(page_url, src))
            if validators.url(abs_u):
                m.misc_map[abs_u] = iframe

    return m


# ---------------------------------------------------------------------------
# HTML path rewriting
# ---------------------------------------------------------------------------

def patch_html(
    manifest:   AssetManifest,
    css_results:  dict[str, str | None],
    js_results:   dict[str, str | None],
    img_results:  dict[str, str | None],
    misc_results: dict[str, str | None],
    output_dir:   str,
    passthru:     bool,
) -> None:
    """
    Mutate BeautifulSoup tags in *manifest* to use local relative paths.
    Results dicts map url → local filepath (or None on failure).
    """
    def rel(fp: str) -> str:
        return os.path.relpath(fp, output_dir).replace("\\", "/")

    for abs_u, tag in manifest.css_map.items():
        fp = css_results.get(abs_u)
        if fp:
            tag["href"] = rel(fp)

    for abs_u, tag in manifest.js_map.items():
        fp = js_results.get(abs_u)
        if fp:
            tag["src"] = rel(fp)

    for img_tag, abs_u in manifest.img_tags:
        if passthru:
            img_tag["src"] = abs_u
        else:
            fp = img_results.get(abs_u)
            if fp:
                img_tag["src"] = rel(fp)

    for source_tag, abs_u in manifest.picture_sources:
        if passthru:
            source_tag["srcset"] = abs_u
        else:
            fp = img_results.get(abs_u)
            if fp:
                source_tag["srcset"] = rel(fp)

    for abs_u, tag in manifest.misc_map.items():
        fp = misc_results.get(abs_u)
        if fp:
            r = rel(fp)
            if tag.name == "link":
                tag["href"] = r
            else:
                tag["src"] = r


# ---------------------------------------------------------------------------
# CSS path rewriting helpers (called after CSS files are written to disk)
# ---------------------------------------------------------------------------

def patch_css(css_content: str, url_map: dict[str, str]) -> str:
    """
    Apply *url_map* (original_url → /fonts/... or /misc/...) to *css_content*.
    """
    for original, new_path in url_map.items():
        css_content = rewrite_url(css_content, original, new_path)
    return css_content


# ---------------------------------------------------------------------------
# Internal link discovery
# ---------------------------------------------------------------------------

def collect_internal_links(soup: BeautifulSoup, base_url: str) -> set[str]:
    """Return same-domain <a href> links found in *soup*, stripped of fragments."""
    links: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if is_internal_link(href, base_url):
            # Strip both query string and fragment from the final URL
            resolved = urljoin(base_url, href).split("?")[0].split("#")[0].rstrip("/")
            links.add(resolved)
    return links


# ---------------------------------------------------------------------------
# BFS page discovery
# ---------------------------------------------------------------------------

async def discover_pages(
    start_url:    str,
    crawl_depth:  int,
    output_dir:   str,
    session:      aiohttp.ClientSession,
    timeout:      aiohttp.ClientTimeout,
    log:          Callable[[str], None],
    cancelled:    Callable[[], bool],
    user_agent:   str = "Mozilla/5.0",
    rate_limiter: Optional["DomainRateLimiter"] = None,
) -> list[PageRecord]:
    """
    BFS from *start_url* up to *crawl_depth* levels deep.
    Respects robots.txt before fetching each page.
    Returns a list of PageRecord objects in visit order.
    """
    # Fetch and parse robots.txt once for the origin
    robots = await RobotsChecker.build(
        base_url   = start_url,
        user_agent = user_agent,
        session    = session,
        timeout    = timeout,
        log        = log,
    )
    # If robots.txt specifies Crawl-delay, register it with the rate limiter
    if rate_limiter and robots.crawl_delay > 0:
        from urllib.parse import urlparse as _up
        domain = _up(start_url).netloc
        rate_limiter.set_crawl_delay(domain, robots.crawl_delay)
        log(f"Crawl-delay {robots.crawl_delay}s registered for {domain}")

    # Normalise start_url for consistent dedup comparison
    start_url = start_url.rstrip("/") or "/"
    queue:   deque[tuple[str, int]] = deque([(start_url, 0)])
    visited: set[str]               = set()
    records: list[PageRecord]       = []

    while queue and not cancelled():
        page_url, depth = queue.popleft()
        # Normalise trailing slash for dedup — http://host/ and http://host are the same
        page_url = page_url.rstrip("/") or "/"
        if page_url in visited:
            continue
        visited.add(page_url)

        # robots.txt check — always allow start_url itself
        if page_url != start_url and not robots.allowed(page_url):
            log(f"robots.txt: skipping disallowed URL {page_url}")
            continue

        # Honour Crawl-delay between page fetches
        delay = robots.crawl_delay
        if delay > 0 and len(records) > 0:
            import asyncio as _aio
            await _aio.sleep(delay)

        log(f"Fetching page (depth {depth}): {page_url}")
        try:
            async with session.get(page_url, timeout=timeout, allow_redirects=True) as resp:
                if resp.status != 200:
                    log(f"Skipping {page_url}: HTTP {resp.status}")
                    continue
                text = await resp.text()
                soup = BeautifulSoup(text, "html.parser")
        except Exception as exc:
            log(f"Error fetching {page_url}: {exc}")
            continue

        out_dir, html_name = _page_output_path(page_url, start_url, output_dir)
        # Directory creation deferred to _process_page (crawler shouldn't touch FS)
        records.append(PageRecord(page_url, soup, out_dir, html_name))

        if depth < crawl_depth:
            for link in collect_internal_links(soup, start_url):
                norm = link.rstrip("/") or "/"
                if norm not in visited:
                    queue.append((norm, depth + 1))

    return records


def _page_output_path(page_url: str, start_url: str, output_dir: str) -> tuple[str, str]:
    """Compute (out_dir, html_name) for a crawled page."""
    if page_url == start_url or page_url == start_url.rstrip("/"):
        return output_dir, "index.html"

    path_part = urlparse(page_url).path.strip("/")
    if not path_part or not path_part.endswith(".html"):
        path_part = (path_part + "/index.html").lstrip("/")

    return (
        os.path.join(output_dir, os.path.dirname(path_part)),
        os.path.basename(path_part),
    )
