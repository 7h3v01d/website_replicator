"""
tests/test_helpers.py
Unit tests for utils/helpers.py — pure functions, no I/O.
"""

import os
import pytest
import tempfile

from website_replicator.utils.helpers import (
    normalise_url,
    strip_query,
    is_same_domain,
    is_internal_link,
    resolve_url,
    safe_filename,
    unique_filepath,
    extension_for,
    is_valid_asset_type,
    FONT_EXTENSIONS,
    IMAGE_EXTENSIONS,
)


# ---------------------------------------------------------------------------
# normalise_url
# ---------------------------------------------------------------------------

class TestNormaliseUrl:
    def test_adds_https_when_no_scheme(self):
        ok, url = normalise_url("example.com")
        assert ok
        assert url.startswith("https://")

    def test_leaves_https_intact(self):
        ok, url = normalise_url("https://example.com")
        assert ok
        assert url == "https://example.com"

    def test_leaves_http_intact(self):
        ok, url = normalise_url("http://example.com")
        assert ok
        assert url == "http://example.com"

    def test_invalid_url_returns_false(self):
        ok, _ = normalise_url("not a url at all!!!")
        assert not ok

    def test_localhost_invalid(self):
        # validators considers bare localhost invalid
        ok, _ = normalise_url("localhost:8000")
        assert not ok


# ---------------------------------------------------------------------------
# strip_query
# ---------------------------------------------------------------------------

class TestStripQuery:
    def test_removes_query_string(self):
        assert strip_query("https://x.com/a.css?v=2") == "https://x.com/a.css"

    def test_no_query_unchanged(self):
        assert strip_query("https://x.com/a.css") == "https://x.com/a.css"

    def test_empty_query_removed(self):
        assert strip_query("https://x.com/a.css?") == "https://x.com/a.css"

    def test_multiple_params(self):
        assert strip_query("https://x.com/img.png?w=100&h=200") == "https://x.com/img.png"


# ---------------------------------------------------------------------------
# is_same_domain
# ---------------------------------------------------------------------------

class TestIsSameDomain:
    def test_same_domain(self):
        assert is_same_domain("https://example.com/a", "https://example.com/b")

    def test_different_domain(self):
        assert not is_same_domain("https://cdn.net/a.css", "https://example.com")

    def test_subdomain_is_different(self):
        assert not is_same_domain("https://cdn.example.com/a", "https://example.com")

    def test_same_domain_different_path(self):
        assert is_same_domain("https://example.com/page/about", "https://example.com/")


# ---------------------------------------------------------------------------
# is_internal_link
# ---------------------------------------------------------------------------

class TestIsInternalLink:
    BASE = "https://example.com"

    def test_relative_path(self):
        assert is_internal_link("/about", self.BASE)

    def test_absolute_same_domain(self):
        assert is_internal_link("https://example.com/contact", self.BASE)

    def test_external_domain(self):
        assert not is_internal_link("https://other.com/page", self.BASE)

    def test_fragment_only_excluded(self):
        assert not is_internal_link("#section", self.BASE)

    def test_empty_href_excluded(self):
        assert not is_internal_link("", self.BASE)

    def test_fragment_stripped_from_path(self):
        # /about#top should still count as internal
        assert is_internal_link("/about#top", self.BASE)

    def test_javascript_href_excluded(self):
        assert not is_internal_link("javascript:void(0)", self.BASE)

    def test_mailto_excluded(self):
        assert not is_internal_link("mailto:a@b.com", self.BASE)


# ---------------------------------------------------------------------------
# safe_filename
# ---------------------------------------------------------------------------

class TestSafeFilename:
    def test_spaces_replaced(self):
        assert " " not in safe_filename("my file.css")

    def test_slashes_replaced(self):
        result = safe_filename("path/to/file.js")
        assert "/" not in result

    def test_valid_chars_preserved(self):
        result = safe_filename("bootstrap-5.3.min.css")
        assert result == "bootstrap-5.3.min.css"

    def test_unicode_replaced(self):
        result = safe_filename("Ångström.woff2")
        assert all(c.isalnum() or c in "-_." for c in result)

    def test_dots_preserved(self):
        result = safe_filename("jquery.min.js")
        assert "." in result


# ---------------------------------------------------------------------------
# unique_filepath
# ---------------------------------------------------------------------------

class TestUniqueFilepath:
    def test_nonexistent_path_returned_unchanged(self):
        path = "/tmp/definitely_does_not_exist_xyz.css"
        assert unique_filepath(path) == path

    def test_existing_file_gets_counter(self):
        with tempfile.NamedTemporaryFile(suffix=".css", delete=False) as f:
            existing = f.name
        try:
            result = unique_filepath(existing)
            assert result != existing
            assert "_1" in result
        finally:
            os.unlink(existing)

    def test_counter_increments(self):
        with tempfile.TemporaryDirectory() as d:
            base = os.path.join(d, "file.css")
            # Create file and _1
            open(base, "w").close()
            open(os.path.join(d, "file_1.css"), "w").close()
            result = unique_filepath(base)
            assert result.endswith("file_2.css")


# ---------------------------------------------------------------------------
# extension_for
# ---------------------------------------------------------------------------

class TestExtensionFor:
    def test_jpeg_content_type(self):
        assert extension_for("image/jpeg", "https://x.com/img.jpg", "img") == ".jpg"

    def test_webp_url_overrides_ct(self):
        # Even if server says octet-stream, .webp URL wins
        assert extension_for("application/octet-stream", "https://x.com/img.webp", "img") == ".webp"

    def test_webp_content_type(self):
        assert extension_for("image/webp", "https://x.com/img", "img") == ".webp"

    def test_woff2_font(self):
        assert extension_for("font/woff2", "https://x.com/f.woff2", "font") == ".woff2"

    def test_unknown_type_css(self):
        assert extension_for("application/javascript", "https://x.com/app.js", "css") == ".js"

    def test_unknown_type_img_fallback(self):
        # unknown content-type for img → .png fallback
        assert extension_for("application/octet-stream", "https://x.com/img", "img") == ".png"

    def test_content_type_with_charset(self):
        # charset suffix should be stripped
        assert extension_for("text/css; charset=utf-8", "https://x.com/s.css", "css") == ".css"


# ---------------------------------------------------------------------------
# is_valid_asset_type
# ---------------------------------------------------------------------------

class TestIsValidAssetType:
    def test_valid_image_jpeg(self):
        assert is_valid_asset_type("image/jpeg", "https://x.com/img.jpg", "img")

    def test_invalid_image_html(self):
        assert not is_valid_asset_type("text/html", "https://x.com/img.jpg", "img")

    def test_webp_url_valid_for_img(self):
        # octet-stream + .webp URL should be valid for img
        assert is_valid_asset_type("application/octet-stream", "https://x.com/img.webp", "img")

    def test_valid_font_woff2(self):
        assert is_valid_asset_type("font/woff2", "https://x.com/f.woff2", "font")

    def test_invalid_font_jpeg(self):
        assert not is_valid_asset_type("image/jpeg", "https://x.com/img.jpg", "font")

    def test_font_by_url_extension(self):
        # Even if CT is wrong, .woff2 extension on URL saves it
        assert is_valid_asset_type("application/octet-stream", "https://x.com/f.woff2", "font")

    def test_valid_misc_manifest(self):
        assert is_valid_asset_type("application/manifest+json", "https://x.com/m.webmanifest", "misc")

    def test_valid_misc_ico(self):
        assert is_valid_asset_type("image/x-icon", "https://x.com/favicon.ico", "misc")

    def test_invalid_misc_js(self):
        assert not is_valid_asset_type("application/javascript", "https://x.com/app.js", "misc")

    def test_css_type_always_valid(self):
        # For css/js we trust loosely
        assert is_valid_asset_type("text/plain", "https://x.com/s.css", "css")
