"""
utils/helpers.py
Pure utility functions — no Qt, no aiohttp, no BeautifulSoup.
Everything here is synchronous and trivially unit-testable.
"""

import os
import re
from datetime import datetime
from urllib.parse import urlparse, urljoin

import validators


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def normalise_url(url: str) -> tuple[bool, str]:
    """
    Ensure *url* has a scheme and is valid.

    Returns (is_valid, normalised_url).
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return bool(validators.url(url)), url


def strip_query(url: str) -> str:
    """Remove the query string from *url* — used as the dedup cache key."""
    return url.split("?")[0]


def is_same_domain(url: str, base_url: str) -> bool:
    """Return True if *url* shares the same netloc as *base_url*."""
    return urlparse(url).netloc == urlparse(base_url).netloc


def is_internal_link(href: str, base_url: str) -> bool:
    """
    Return True if *href* resolves to an internal (same-domain) HTTP/S URL.
    Strips fragments before checking so '#section' links are excluded.
    """
    href = href.split("#")[0].strip()
    if not href:
        return False
    abs_url = urljoin(base_url, href)
    parsed  = urlparse(abs_url)
    return (
        parsed.scheme in ("http", "https")
        and parsed.netloc == urlparse(base_url).netloc
    )


def resolve_url(href: str, base_url: str) -> str:
    """Absolute-ify *href* relative to *base_url* and strip query string."""
    return strip_query(urljoin(base_url, href))


# ---------------------------------------------------------------------------
# Filename helpers
# ---------------------------------------------------------------------------

def safe_filename(name: str) -> str:
    """Replace characters illegal in most filesystems with underscores."""
    return re.sub(r"[^\w\-\.]", "_", name)


def unique_filepath(path: str) -> str:
    """
    If *path* already exists, append _1, _2, … until unique.
    Returns the (possibly modified) path.
    """
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    counter = 1
    while os.path.exists(path):
        path = f"{base}_{counter}{ext}"
        counter += 1
    return path


# ---------------------------------------------------------------------------
# Content-type / extension mapping
# ---------------------------------------------------------------------------

CONTENT_TYPE_MAP: dict[str, str] = {
    "image/jpeg":              ".jpg",
    "image/png":               ".png",
    "image/gif":               ".gif",
    "image/webp":              ".webp",
    "image/svg+xml":           ".svg",
    "image/x-icon":            ".ico",
    "text/css":                ".css",
    "application/javascript":  ".js",
    "text/javascript":         ".js",
    "font/woff2":              ".woff2",
    "font/woff":               ".woff",
    "font/ttf":                ".ttf",
    "application/font-woff":   ".woff",
    "application/font-woff2":  ".woff2",
    "application/manifest+json": ".webmanifest",
    "text/html":               ".htm",
}

FONT_EXTENSIONS   = {".woff", ".woff2", ".ttf"}
IMAGE_EXTENSIONS  = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"}
MISC_CONTENT_TYPES = {
    "application/manifest+json", "text/html", "image/x-icon",
    "image/png", "image/jpeg", "image/gif",
}
MISC_EXTENSIONS = {".webmanifest", ".htm", ".ico", ".png", ".jpg", ".gif"}


def extension_for(content_type: str, url: str, asset_type: str) -> str:
    """
    Derive the file extension for a downloaded asset.

    Priority order:
      1. URL ends with .webp  → .webp  (servers sometimes lie about CT)
      2. Content-Type map
      3. Fallback by asset_type
    """
    ct = content_type.lower().split(";")[0].strip()
    if url.lower().endswith(".webp") or ct == "image/webp":
        return ".webp"
    if ct in CONTENT_TYPE_MAP:
        return CONTENT_TYPE_MAP[ct]
    return ".bin" if asset_type not in ("img", "font", "misc") else ".png"


def is_valid_asset_type(content_type: str, url: str, asset_type: str) -> bool:
    """
    Return False if the server returned a content-type that doesn't match
    what we expected for *asset_type*.  Avoids saving HTML error pages as .jpg.
    """
    ct = content_type.lower().split(";")[0].strip()
    if asset_type == "img":
        return ct.startswith("image/") or url.lower().endswith(".webp")
    if asset_type == "font":
        # Trust URL extension only when content-type is neutral (octet-stream or missing).
        # Reject obvious mismatches like text/html (error pages).
        url_ext_ok = any(url.lower().endswith(e) for e in FONT_EXTENSIONS)
        ct_ok = (
            ct.startswith("font/")
            or ct in {"application/font-woff", "application/font-woff2"}
        )
        ct_neutral = ct in {"application/octet-stream", "", "binary/octet-stream"}
        return ct_ok or (url_ext_ok and ct_neutral)
    if asset_type == "misc":
        return ct in MISC_CONTENT_TYPES or any(url.lower().endswith(e) for e in MISC_EXTENSIONS)
    return True   # css / js — trust content-type loosely


# ---------------------------------------------------------------------------
# Timestamp
# ---------------------------------------------------------------------------

def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log_line(msg: str) -> str:
    return f"[{ts()}] {msg}"
