"""
tests/test_v6_features.py
Tests for the four v6 must-have features:
  1. robots.txt — RobotsChecker
  2. Resume / incremental mode — Downloader.fetch skips existing files
  3. ZIP export — zip_output_dir
  4. Broken link report — BrokenLinkReport
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import zipfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from website_replicator.core.robots import RobotsChecker
from website_replicator.core.exporter import BrokenLinkReport, zip_output_dir
from website_replicator.core.downloader import Downloader
from website_replicator.core.models import ReplicatorConfig, DownloadResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_dl(cfg=None, asset_map=None, semaphore=None, log=None, cancelled=False):
    cfg       = cfg       or ReplicatorConfig(max_retries=0, timeout=5)
    asset_map = asset_map if asset_map is not None else {}
    semaphore = semaphore or asyncio.Semaphore(2)
    log       = log       or (lambda m: None)
    return Downloader(cfg=cfg, asset_map=asset_map, semaphore=semaphore,
                      log=log, cancelled=lambda: cancelled)


def mock_response(status=200, content=b"data", content_type="image/jpeg"):
    resp = AsyncMock()
    resp.status   = status
    resp.read     = AsyncMock(return_value=content)
    resp.text     = AsyncMock(return_value=content.decode("utf-8", errors="replace"))
    resp.headers  = {"Content-Type": content_type}
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__  = AsyncMock(return_value=False)
    return resp


def mock_session(response):
    s = MagicMock()
    s.get = MagicMock(return_value=response)
    return s


# ===========================================================================
# 1. robots.txt — RobotsChecker
# ===========================================================================

class TestRobotsChecker:

    def _make_checker(self, rules: str, base="https://example.com",
                      ua="Mozilla/5.0") -> RobotsChecker:
        """Build a checker directly from a robots.txt string."""
        from urllib.robotparser import RobotFileParser
        parser = RobotFileParser()
        parser.set_url(f"{base}/robots.txt")
        parser.parse(rules.splitlines())
        return RobotsChecker(base_url=base, parser=parser,
                             user_agent=ua, log=lambda m: None)

    def test_allow_all_when_no_parser(self):
        checker = RobotsChecker(
            base_url="https://example.com", parser=None,
            user_agent="Mozilla/5.0", log=lambda m: None
        )
        assert checker.allowed("https://example.com/private")
        assert checker.allowed("https://example.com/anything")

    def test_disallow_path_blocked(self):
        rules = "User-agent: *\nDisallow: /private/"
        checker = self._make_checker(rules)
        assert not checker.allowed("https://example.com/private/secret.html")

    def test_allow_path_permitted(self):
        rules = "User-agent: *\nDisallow: /private/\nAllow: /public/"
        checker = self._make_checker(rules)
        assert checker.allowed("https://example.com/public/page.html")

    def test_different_domain_always_allowed(self):
        rules = "User-agent: *\nDisallow: /"
        checker = self._make_checker(rules)
        # Different domain — not this site's robots.txt
        assert checker.allowed("https://other.com/page")

    def test_crawl_delay_zero_when_absent(self):
        rules = "User-agent: *\nDisallow: /admin/"
        checker = self._make_checker(rules)
        assert checker.crawl_delay == 0.0

    def test_crawl_delay_respected(self):
        rules = "User-agent: *\nCrawl-delay: 3"
        checker = self._make_checker(rules)
        assert checker.crawl_delay == 3.0

    def test_crawl_delay_capped_at_10(self):
        rules = "User-agent: *\nCrawl-delay: 999"
        checker = self._make_checker(rules)
        assert checker.crawl_delay == 10.0

    def test_allow_start_url_regardless(self):
        """The start URL is always fetched even if robots disallows /"""
        rules = "User-agent: *\nDisallow: /"
        checker = self._make_checker(rules)
        # The checker itself allows checking — it's the caller (discover_pages)
        # that skips the start URL check. Test the underlying parser here.
        # (start URL bypass is in crawler.py, not robots.py)
        assert not checker.allowed("https://example.com/")    # parser says no
        assert not checker.allowed("https://example.com/page") # consistent

    def test_empty_robots_txt_allows_all(self):
        checker = self._make_checker("")
        assert checker.allowed("https://example.com/anything")

    @pytest.mark.asyncio
    async def test_build_with_404(self):
        """404 on robots.txt → allow all."""
        resp = mock_response(status=404)
        session = mock_session(resp)
        import aiohttp
        checker = await RobotsChecker.build(
            base_url   = "https://example.com",
            user_agent = "Mozilla/5.0",
            session    = session,
            timeout    = aiohttp.ClientTimeout(total=5),
            log        = lambda m: None,
        )
        assert checker.allowed("https://example.com/anything")
        assert checker._parser is None

    @pytest.mark.asyncio
    async def test_build_parses_valid_robots(self):
        """Valid robots.txt is parsed correctly."""
        robots_content = b"User-agent: *\nDisallow: /secret/\n"
        resp = mock_response(status=200, content=robots_content, content_type="text/plain")
        session = mock_session(resp)
        import aiohttp
        checker = await RobotsChecker.build(
            base_url   = "https://example.com",
            user_agent = "Mozilla/5.0",
            session    = session,
            timeout    = aiohttp.ClientTimeout(total=5),
            log        = lambda m: None,
        )
        assert checker._parser is not None
        assert not checker.allowed("https://example.com/secret/data.json")
        assert checker.allowed("https://example.com/public/page")

    @pytest.mark.asyncio
    async def test_build_network_error_allows_all(self):
        """Network error fetching robots.txt → allow all."""
        session = MagicMock()
        session.get = MagicMock(side_effect=Exception("timeout"))
        import aiohttp
        checker = await RobotsChecker.build(
            base_url   = "https://example.com",
            user_agent = "Mozilla/5.0",
            session    = session,
            timeout    = aiohttp.ClientTimeout(total=5),
            log        = lambda m: None,
        )
        assert checker.allowed("https://example.com/anything")


# ===========================================================================
# 2. Resume / incremental mode
# ===========================================================================

class TestResumeMode:

    @pytest.mark.asyncio
    async def test_existing_file_skipped(self):
        """If the target file already exists and is non-empty, skip download."""
        logs = []
        dl = make_dl(log=lambda m: logs.append(m))
        session = MagicMock()  # should never be called

        with tempfile.TemporaryDirectory() as d:
            # Pre-create the expected file
            existing = os.path.join(d, "style.css")
            with open(existing, "wb") as f:
                f.write(b"body{}")

            result = await dl.fetch(session, "https://example.com/style.css", d, "css")

        assert result.ok
        assert result.cached
        session.get.assert_not_called()
        assert any("Resume" in l for l in logs)

    @pytest.mark.asyncio
    async def test_empty_file_not_resumed(self):
        """An empty file on disk is NOT treated as a valid resume target."""
        resp = mock_response(status=200, content=b"body{}", content_type="text/css")
        session = mock_session(resp)
        dl = make_dl()

        with tempfile.TemporaryDirectory() as d:
            # Zero-byte file = incomplete previous download
            empty = os.path.join(d, "style.css")
            open(empty, "wb").close()

            result = await dl.fetch(session, "https://example.com/style.css", d, "css")

        # Should have downloaded normally (empty file bypassed)
        assert result.ok
        session.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_asset_map_takes_priority_over_disk(self):
        """In-memory dedup map wins over disk check."""
        asset_map = {"https://example.com/style.css": "/cached/path/style.css"}
        dl = make_dl(asset_map=asset_map)
        session = MagicMock()

        with tempfile.TemporaryDirectory() as d:
            result = await dl.fetch(session, "https://example.com/style.css?v=1", d, "css")

        assert result.filepath == "/cached/path/style.css"
        assert result.cached
        session.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_resume_populates_asset_map(self):
        """Resumed file is registered in asset_map for future dedup."""
        asset_map = {}
        dl = make_dl(asset_map=asset_map)
        session = MagicMock()

        with tempfile.TemporaryDirectory() as d:
            existing = os.path.join(d, "app.js")
            with open(existing, "wb") as f:
                f.write(b"console.log(1)")

            await dl.fetch(session, "https://example.com/app.js", d, "js")

        assert "https://example.com/app.js" in asset_map


# ===========================================================================
# 3. ZIP export
# ===========================================================================

class TestZipExport:

    def _make_site(self, tmp: str) -> str:
        """Create a minimal replicated site structure in tmp."""
        site = os.path.join(tmp, "replicated")
        os.makedirs(os.path.join(site, "css"))
        os.makedirs(os.path.join(site, "img"))
        with open(os.path.join(site, "index.html"), "w") as f:
            f.write("<html><body>Hello</body></html>")
        with open(os.path.join(site, "css", "main.css"), "w") as f:
            f.write("body{margin:0}")
        with open(os.path.join(site, "img", "logo.png"), "wb") as f:
            f.write(b"\x89PNG")
        return site

    def test_zip_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = self._make_site(tmp)
            zip_path = os.path.join(tmp, "out.zip")
            result = zip_output_dir(site, zip_path)
            assert os.path.exists(result)
            assert result.endswith(".zip")

    def test_zip_contains_all_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = self._make_site(tmp)
            zip_path = os.path.join(tmp, "out.zip")
            zip_output_dir(site, zip_path)
            with zipfile.ZipFile(zip_path) as zf:
                names = zf.namelist()
            assert any("index.html" in n for n in names)
            assert any("main.css"   in n for n in names)
            assert any("logo.png"   in n for n in names)

    def test_zip_contents_correct(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = self._make_site(tmp)
            zip_path = os.path.join(tmp, "out.zip")
            zip_output_dir(site, zip_path)
            with zipfile.ZipFile(zip_path) as zf:
                names = zf.namelist()
                html_name = next(n for n in names if "index.html" in n)
                content = zf.read(html_name)
            assert b"Hello" in content

    def test_auto_named_zip_created(self):
        """When no zip_path given, auto-name is generated."""
        with tempfile.TemporaryDirectory() as tmp:
            site = self._make_site(tmp)
            result = zip_output_dir(site)
            assert os.path.exists(result)
            assert "replicated" in result
            assert result.endswith(".zip")

    def test_zip_is_valid_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = self._make_site(tmp)
            result = zip_output_dir(site)
            assert zipfile.is_zipfile(result)

    def test_empty_directory_zips_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty_site = os.path.join(tmp, "empty")
            os.makedirs(empty_site)
            result = zip_output_dir(empty_site)
            assert os.path.exists(result)

    def test_zip_compression_reduces_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = self._make_site(tmp)
            # Add a large compressible file
            with open(os.path.join(site, "big.js"), "w") as f:
                f.write("var x = 1;\n" * 10_000)
            result = zip_output_dir(site)
            raw_size = sum(
                os.path.getsize(os.path.join(r, fn))
                for r, _, fns in os.walk(site) for fn in fns
            )
            assert os.path.getsize(result) < raw_size


# ===========================================================================
# 4. Broken link report
# ===========================================================================

class TestBrokenLinkReport:

    def test_empty_report(self):
        blr = BrokenLinkReport("https://example.com", "/tmp/out")
        assert blr.count == 0
        assert not blr.has_errors
        lines = blr.summary_lines()
        assert len(lines) == 1
        assert "No broken" in lines[0]

    def test_record_adds_link(self):
        blr = BrokenLinkReport("https://example.com", "/tmp/out")
        blr.record("https://example.com/img.jpg", "https://example.com", "img", "HTTP 404")
        assert blr.count == 1
        assert blr.has_errors

    def test_summary_lines_with_errors(self):
        blr = BrokenLinkReport("https://example.com", "/tmp/out")
        blr.record("https://example.com/a.jpg", "https://example.com", "img", "HTTP 404")
        blr.record("https://example.com/b.jpg", "https://example.com", "img", "timeout")
        blr.record("https://example.com/c.css", "https://example.com", "css", "HTTP 403")
        lines = blr.summary_lines()
        assert any("2" in l or "img" in l for l in lines)
        assert any("css" in l for l in lines)

    def test_by_type_groups_correctly(self):
        blr = BrokenLinkReport("https://example.com", "/tmp/out")
        blr.record("https://example.com/a.jpg", "https://example.com", "img", "404")
        blr.record("https://example.com/b.jpg", "https://example.com", "img", "404")
        blr.record("https://example.com/c.css", "https://example.com", "css", "403")
        groups = blr.by_type()
        assert len(groups["img"]) == 2
        assert len(groups["css"]) == 1

    def test_write_creates_file(self):
        with tempfile.TemporaryDirectory() as d:
            blr = BrokenLinkReport("https://example.com", d)
            blr.record("https://example.com/x.png", "https://example.com", "img", "HTTP 404")
            path = blr.write()
            assert os.path.exists(path)
            content = open(path).read()
            assert "example.com/x.png" in content
            assert "HTTP 404" in content

    def test_write_custom_path(self):
        with tempfile.TemporaryDirectory() as d:
            blr = BrokenLinkReport("https://example.com", d)
            custom = os.path.join(d, "my_report.txt")
            path = blr.write(custom)
            assert path == custom
            assert os.path.exists(path)

    def test_write_empty_report(self):
        with tempfile.TemporaryDirectory() as d:
            blr = BrokenLinkReport("https://example.com", d)
            path = blr.write()
            content = open(path).read()
            assert "No broken links detected" in content

    def test_as_text_contains_url(self):
        blr = BrokenLinkReport("https://example.com", "/tmp/out")
        blr.record("https://example.com/missing.jpg", "https://example.com", "img", "HTTP 404")
        text = blr.as_text()
        assert "missing.jpg" in text
        assert "HTTP 404" in text
        assert "img" in text.lower()

    def test_as_text_header_correct(self):
        blr = BrokenLinkReport("https://example.com", "/tmp/out")
        text = blr.as_text()
        assert "https://example.com" in text
        assert "Broken Link Report" in text

    def test_multiple_pages_tracked(self):
        blr = BrokenLinkReport("https://example.com", "/tmp/out")
        blr.record("https://example.com/a.jpg", "https://example.com/page1", "img", "404")
        blr.record("https://example.com/b.jpg", "https://example.com/page2", "img", "404")
        text = blr.as_text()
        assert "page1" in text
        assert "page2" in text

    def test_report_survives_many_broken_links(self):
        blr = BrokenLinkReport("https://example.com", "/tmp/out")
        for i in range(100):
            blr.record(f"https://example.com/img_{i}.jpg",
                       "https://example.com/", "img", f"HTTP 404")
        assert blr.count == 100
        text = blr.as_text()
        assert "100" in text
