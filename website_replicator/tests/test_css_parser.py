"""
tests/test_css_parser.py
Unit tests for utils/css_parser.py
"""

import pytest

from website_replicator.utils.css_parser import (
    extract_urls,
    extract_font_urls,
    extract_image_urls,
    rewrite_url,
    rewrite_urls,
)

BASE = "https://example.com/css/main.css"


# ---------------------------------------------------------------------------
# extract_urls
# ---------------------------------------------------------------------------

class TestExtractUrls:
    def test_double_quoted(self):
        css = 'body { background: url("img/bg.png"); }'
        assert "img/bg.png" in extract_urls(css)

    def test_single_quoted(self):
        css = "body { background: url('img/bg.png'); }"
        assert "img/bg.png" in extract_urls(css)

    def test_unquoted(self):
        css = "body { background: url(img/bg.png); }"
        assert "img/bg.png" in extract_urls(css)

    def test_font_face(self):
        css = "@font-face { src: url('/fonts/f.woff2') format('woff2'); }"
        assert "/fonts/f.woff2" in extract_urls(css)

    def test_multiple_urls(self):
        css = "a { bg: url(a.png); } b { bg: url(b.png); }"
        urls = extract_urls(css)
        assert "a.png" in urls
        assert "b.png" in urls

    def test_empty_css(self):
        assert extract_urls("") == []

    def test_no_urls(self):
        assert extract_urls("body { color: red; }") == []

    def test_data_uri_extracted(self):
        css = 'img { src: url("data:image/png;base64,abc"); }'
        urls = extract_urls(css)
        assert any("data:" in u for u in urls)


# ---------------------------------------------------------------------------
# extract_font_urls
# ---------------------------------------------------------------------------

class TestExtractFontUrls:
    def test_woff2_extracted(self):
        css = "@font-face { src: url('https://example.com/fonts/f.woff2'); }"
        result = extract_font_urls(css, BASE)
        assert "https://example.com/fonts/f.woff2" in result

    def test_relative_resolved(self):
        css = "@font-face { src: url('../fonts/f.woff2'); }"
        result = extract_font_urls(css, BASE)
        assert any("woff2" in u for u in result)

    def test_image_not_extracted(self):
        css = "body { background: url('https://example.com/img.png'); }"
        assert extract_font_urls(css, BASE) == []

    def test_deduplication(self):
        css = """
        @font-face { src: url('https://example.com/fonts/f.woff2'); }
        @font-face { src: url('https://example.com/fonts/f.woff2'); }
        """
        result = extract_font_urls(css, BASE)
        assert result.count("https://example.com/fonts/f.woff2") == 1

    def test_query_string_stripped(self):
        css = "@font-face { src: url('https://example.com/fonts/f.woff2?v=2'); }"
        result = extract_font_urls(css, BASE)
        assert all("?" not in u for u in result)

    def test_all_font_extensions(self):
        for ext in [".woff", ".woff2", ".ttf"]:
            css = f"@font-face {{ src: url('https://example.com/f{ext}'); }}"
            result = extract_font_urls(css, BASE)
            assert len(result) == 1


# ---------------------------------------------------------------------------
# extract_image_urls
# ---------------------------------------------------------------------------

class TestExtractImageUrls:
    def test_png_extracted(self):
        css = "body { background: url('https://example.com/img/bg.png'); }"
        result = extract_image_urls(css, BASE)
        assert "https://example.com/img/bg.png" in result

    def test_font_not_extracted(self):
        css = "@font-face { src: url('https://example.com/fonts/f.woff2'); }"
        assert extract_image_urls(css, BASE) == []

    def test_webp_extracted(self):
        css = "div { background: url('https://example.com/hero.webp'); }"
        result = extract_image_urls(css, BASE)
        assert any("webp" in u for u in result)

    def test_svg_extracted(self):
        css = "div { background: url('https://example.com/icon.svg'); }"
        result = extract_image_urls(css, BASE)
        assert any("svg" in u for u in result)

    def test_deduplication(self):
        css = """
        .a { background: url('https://example.com/bg.png'); }
        .b { background: url('https://example.com/bg.png'); }
        """
        result = extract_image_urls(css, BASE)
        assert result.count("https://example.com/bg.png") == 1


# ---------------------------------------------------------------------------
# rewrite_url
# ---------------------------------------------------------------------------

class TestRewriteUrl:
    def test_basic_rewrite(self):
        css = 'body { background: url("https://example.com/img/bg.png"); }'
        result = rewrite_url(css, "https://example.com/img/bg.png", "/misc/bg.png")
        assert 'url("/misc/bg.png")' in result

    def test_unquoted_rewritten(self):
        css = "body { background: url(https://example.com/img/bg.png); }"
        result = rewrite_url(css, "https://example.com/img/bg.png", "/misc/bg.png")
        assert 'url("/misc/bg.png")' in result

    def test_query_string_in_source_handled(self):
        # Original URL with query — rewrite_url strips query internally
        css = 'body { background: url("https://example.com/bg.png?v=2"); }'
        result = rewrite_url(css, "https://example.com/bg.png", "/misc/bg.png")
        # After strip_query, the pattern matches
        assert "/misc/bg.png" in result

    def test_no_match_unchanged(self):
        css = "body { background: url(other.png); }"
        result = rewrite_url(css, "https://example.com/bg.png", "/misc/bg.png")
        assert result == css

    def test_case_insensitive(self):
        css = 'body { background: URL("https://example.com/bg.PNG"); }'
        result = rewrite_url(css, "https://example.com/bg.PNG", "/misc/bg.PNG")
        assert "/misc/bg.PNG" in result

    def test_multiple_occurrences_all_replaced(self):
        css = (
            'a { bg: url("https://example.com/bg.png"); } '
            'b { bg: url("https://example.com/bg.png"); }'
        )
        result = rewrite_url(css, "https://example.com/bg.png", "/misc/bg.png")
        assert result.count("/misc/bg.png") == 2


# ---------------------------------------------------------------------------
# rewrite_urls (batch)
# ---------------------------------------------------------------------------

class TestRewriteUrls:
    def test_multiple_rewrites(self):
        css = (
            'a { bg: url("https://example.com/a.png"); } '
            'b { bg: url("https://example.com/b.woff2"); }'
        )
        url_map = {
            "https://example.com/a.png":   "/misc/a.png",
            "https://example.com/b.woff2": "/fonts/b.woff2",
        }
        result = rewrite_urls(css, url_map)
        assert "/misc/a.png" in result
        assert "/fonts/b.woff2" in result

    def test_empty_map_unchanged(self):
        css = "body { color: red; }"
        assert rewrite_urls(css, {}) == css
