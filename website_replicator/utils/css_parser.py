"""
utils/css_parser.py
CSS url() extraction and path rewriting.
Pure string operations — no I/O, no network, easily testable.
"""

import re
from urllib.parse import urljoin

import validators

from .helpers import FONT_EXTENSIONS, IMAGE_EXTENSIONS, strip_query


# Matches url("..."), url('...'), url(...) in CSS
_URL_RE = re.compile(r'url\(["\']?(.*?)["\']?\)', re.IGNORECASE)


def extract_urls(css_content: str) -> list[str]:
    """Return every raw URL token found inside url() calls in *css_content*."""
    return _URL_RE.findall(css_content)


def extract_font_urls(css_content: str, css_base_url: str) -> list[str]:
    """
    Return all absolute font URLs referenced in *css_content*.
    Deduplicates; strips query strings.
    """
    seen: set[str] = set()
    results: list[str] = []
    for raw in extract_urls(css_content):
        abs_url = urljoin(css_base_url, strip_query(raw))
        if (
            validators.url(abs_url)
            and any(abs_url.lower().endswith(e) for e in FONT_EXTENSIONS)
            and abs_url not in seen
        ):
            seen.add(abs_url)
            results.append(abs_url)
    return results


def extract_image_urls(css_content: str, css_base_url: str) -> list[str]:
    """
    Return all absolute image URLs referenced in *css_content*.
    Deduplicates; strips query strings.
    """
    seen: set[str] = set()
    results: list[str] = []
    for raw in extract_urls(css_content):
        abs_url = urljoin(css_base_url, strip_query(raw))
        if (
            validators.url(abs_url)
            and any(abs_url.lower().endswith(e) for e in IMAGE_EXTENSIONS)
            and abs_url not in seen
        ):
            seen.add(abs_url)
            results.append(abs_url)
    return results


def rewrite_url(css_content: str, original_url: str, new_url: str) -> str:
    """
    Replace all occurrences of url(<original_url>) with url("<new_url>")
    in *css_content*, case-insensitively.
    Matches the base URL with an optional trailing query string (?...) inside the url().
    """
    base = re.escape(strip_query(original_url))
    # Allow optional ?query after the base URL inside the CSS url() token
    pattern = r'url\(["\']?' + base + r'(?:\?[^"\')]*)?["\']?\)'
    return re.sub(pattern, f'url("{new_url}")', css_content, flags=re.IGNORECASE)


def rewrite_urls(css_content: str, url_map: dict[str, str]) -> str:
    """
    Apply multiple URL rewrites from *url_map* (original → new).
    More efficient than calling rewrite_url() in a loop when many replacements
    are needed, because we compile a single combined regex.
    """
    for original, new in url_map.items():
        css_content = rewrite_url(css_content, original, new)
    return css_content
