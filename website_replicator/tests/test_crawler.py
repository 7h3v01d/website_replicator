"""
tests/test_crawler.py
Unit tests for core/crawler.py — HTML parsing, asset extraction, link discovery.
All synchronous (no network, no I/O).
"""

import pytest
from bs4 import BeautifulSoup

from website_replicator.core.crawler import (
    collect_internal_links,
    extract_assets,
    patch_html,
    _page_output_path,
)

BASE = "https://example.com"


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


# ---------------------------------------------------------------------------
# collect_internal_links
# ---------------------------------------------------------------------------

class TestCollectInternalLinks:
    def test_relative_link(self):
        s = soup('<a href="/about">About</a>')
        links = collect_internal_links(s, BASE)
        assert "https://example.com/about" in links

    def test_absolute_internal_link(self):
        s = soup('<a href="https://example.com/page">Page</a>')
        links = collect_internal_links(s, BASE)
        assert "https://example.com/page" in links

    def test_external_link_excluded(self):
        s = soup('<a href="https://other.com/page">Ext</a>')
        links = collect_internal_links(s, BASE)
        assert not any("other.com" in l for l in links)

    def test_fragment_only_excluded(self):
        s = soup('<a href="#section">Jump</a>')
        links = collect_internal_links(s, BASE)
        assert not links

    def test_fragment_stripped_from_url(self):
        s = soup('<a href="/about#team">Team</a>')
        links = collect_internal_links(s, BASE)
        assert "https://example.com/about" in links
        assert all("#" not in l for l in links)

    def test_deduplication(self):
        s = soup('<a href="/about">A</a><a href="/about">B</a>')
        links = collect_internal_links(s, BASE)
        assert len([l for l in links if "about" in l]) == 1

    def test_trailing_slash_normalised(self):
        s = soup('<a href="/about/">About</a>')
        links = collect_internal_links(s, BASE)
        # Should not end with /
        assert all(not l.endswith("/") for l in links)

    def test_no_links(self):
        s = soup('<p>No links here</p>')
        assert collect_internal_links(s, BASE) == set()


# ---------------------------------------------------------------------------
# extract_assets
# ---------------------------------------------------------------------------

class TestExtractAssets:
    def test_css_link_extracted(self):
        s = soup('<link rel="stylesheet" href="/css/main.css">')
        m = extract_assets(s, BASE, passthru=False)
        assert any("main.css" in u for u in m.css_map)

    def test_js_script_extracted(self):
        s = soup('<script src="/js/app.js"></script>')
        m = extract_assets(s, BASE, passthru=False)
        assert any("app.js" in u for u in m.js_map)

    def test_img_extracted(self):
        s = soup('<img src="/img/hero.jpg">')
        m = extract_assets(s, BASE, passthru=False)
        assert len(m.img_tags) == 1
        assert any("hero.jpg" in u for u in m.img_urls)

    def test_img_passthru_not_queued(self):
        s = soup('<img src="/img/hero.jpg">')
        m = extract_assets(s, BASE, passthru=True)
        assert len(m.img_tags) == 1
        assert len(m.img_urls) == 0   # not queued for download

    def test_picture_source_extracted(self):
        s = soup('<picture><source srcset="/img/hero.webp"></picture>')
        m = extract_assets(s, BASE, passthru=False)
        assert len(m.picture_sources) == 1
        assert any("hero.webp" in u for u in m.img_urls)

    def test_manifest_link_extracted(self):
        s = soup('<link rel="manifest" href="/manifest.webmanifest">')
        m = extract_assets(s, BASE, passthru=False)
        assert any("manifest" in u for u in m.misc_map)

    def test_favicon_extracted(self):
        s = soup('<link rel="icon" href="/favicon.ico">')
        m = extract_assets(s, BASE, passthru=False)
        assert any("favicon" in u for u in m.misc_map)

    def test_iframe_extracted(self):
        s = soup('<iframe src="/embed/video.htm"></iframe>')
        m = extract_assets(s, BASE, passthru=False)
        assert any("video.htm" in u for u in m.misc_map)

    def test_invalid_url_skipped(self):
        s = soup('<img src="javascript:void(0)">')
        m = extract_assets(s, BASE, passthru=False)
        assert len(m.img_urls) == 0

    def test_css_deduplication(self):
        s = soup(
            '<link rel="stylesheet" href="/css/main.css">'
            '<link rel="stylesheet" href="/css/main.css">'
        )
        m = extract_assets(s, BASE, passthru=False)
        assert len(m.css_map) == 1

    def test_img_dedup_in_img_urls(self):
        # Same image twice — img_urls is a set
        s = soup('<img src="/img/a.png"><img src="/img/a.png">')
        m = extract_assets(s, BASE, passthru=False)
        assert len(m.img_urls) == 1

    def test_srcset_images_queued(self):
        s = soup('<img src="/img/a.jpg" srcset="/img/a-2x.jpg 2x">')
        m = extract_assets(s, BASE, passthru=False)
        assert any("a-2x" in u for u in m.img_urls)

    def test_total_counts(self):
        html = """
        <link rel="stylesheet" href="/a.css">
        <script src="/b.js"></script>
        <img src="/c.jpg">
        <link rel="icon" href="/favicon.ico">
        """
        m = extract_assets(soup(html), BASE, passthru=False)
        assert m.total == 4


# ---------------------------------------------------------------------------
# patch_html
# ---------------------------------------------------------------------------

class TestPatchHtml:
    def _make_page(self):
        """Return a soup + manifest with one CSS, one JS, one img."""
        html = (
            '<link rel="stylesheet" href="/css/main.css">'
            '<script src="/js/app.js"></script>'
            '<img src="/img/hero.jpg">'
        )
        s = soup(html)
        m = extract_assets(s, BASE, passthru=False)
        return s, m

    def test_css_href_patched(self):
        s, m = self._make_page()
        css_url = next(iter(m.css_map))
        patch_html(m,
                   css_results  ={css_url: "/out/css/main.css"},
                   js_results   ={},
                   img_results  ={},
                   misc_results ={},
                   output_dir   ="/out",
                   passthru     =False)
        link = s.find("link", rel="stylesheet")
        assert link["href"] == "css/main.css"

    def test_img_src_patched(self):
        s, m = self._make_page()
        img_url = next(iter(m.img_urls))
        patch_html(m,
                   css_results  ={},
                   js_results   ={},
                   img_results  ={img_url: "/out/img/hero.jpg"},
                   misc_results ={},
                   output_dir   ="/out",
                   passthru     =False)
        img = s.find("img")
        assert img["src"] == "img/hero.jpg"

    def test_passthru_keeps_absolute_src(self):
        html = '<img src="/img/hero.jpg">'
        s = soup(html)
        m = extract_assets(s, BASE, passthru=True)
        patch_html(m, {}, {}, {}, {}, "/out", passthru=True)
        img = s.find("img")
        assert img["src"].startswith("http")

    def test_failed_download_leaves_original(self):
        s, m = self._make_page()
        img_url = next(iter(m.img_urls))
        # Simulate failed download: None in img_results
        patch_html(m, {}, {}, {img_url: None}, {}, "/out", passthru=False)
        img = s.find("img")
        # Should remain unchanged (not patched to None)
        assert img["src"] == "/img/hero.jpg"


# ---------------------------------------------------------------------------
# _page_output_path
# ---------------------------------------------------------------------------

class TestPageOutputPath:
    def test_start_url_is_index(self):
        out_dir, name = _page_output_path(
            "https://example.com", "https://example.com", "/out"
        )
        assert out_dir == "/out"
        assert name == "index.html"

    def test_subpage_gets_subdir(self):
        out_dir, name = _page_output_path(
            "https://example.com/about", "https://example.com", "/out"
        )
        assert "about" in out_dir
        assert name == "index.html"

    def test_html_file_preserved(self):
        out_dir, name = _page_output_path(
            "https://example.com/blog/post.html", "https://example.com", "/out"
        )
        assert name == "post.html"
