"""
tests/test_downloader.py
Unit tests for core/downloader.py using aiohttp mocking.
All tests run synchronously via pytest-asyncio.
"""

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
import pytest_asyncio

from website_replicator.core.downloader import Downloader
from website_replicator.core.models import ReplicatorConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg():
    return ReplicatorConfig(max_retries=1, timeout=5, max_concurrent=2)


@pytest.fixture
def asset_map():
    return {}


@pytest.fixture
def semaphore():
    return asyncio.Semaphore(2)


@pytest.fixture
def log_lines():
    lines = []
    return lines, lambda msg: lines.append(msg)


def make_dl(cfg, asset_map, semaphore, log_fn, cancelled=False):
    return Downloader(
        cfg       = cfg,
        asset_map = asset_map,
        semaphore = semaphore,
        log       = log_fn,
        cancelled = lambda: cancelled,
    )


def mock_response(status=200, content=b"data", content_type="image/jpeg"):
    resp = AsyncMock()
    resp.status = status
    resp.read   = AsyncMock(return_value=content)
    resp.headers = {"Content-Type": content_type}
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__  = AsyncMock(return_value=False)
    return resp


def mock_session(response):
    session = MagicMock()
    session.get = MagicMock(return_value=response)
    return session


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    @pytest.mark.asyncio
    async def test_cached_url_returns_immediately(self, cfg, semaphore, log_lines):
        lines, log = log_lines
        asset_map = {"https://example.com/img.jpg": "/out/img/img.jpg"}
        dl = make_dl(cfg, asset_map, semaphore, log)
        session = MagicMock()

        result = await dl.fetch(session, "https://example.com/img.jpg?v=1", "/out/img", "img")

        assert result.ok
        assert result.cached
        assert result.filepath == "/out/img/img.jpg"
        session.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_query_string_stripped_for_cache_key(self, cfg, semaphore, log_lines):
        lines, log = log_lines
        asset_map = {"https://example.com/img.jpg": "/cached.jpg"}
        dl = make_dl(cfg, asset_map, semaphore, log)
        session = MagicMock()

        result = await dl.fetch(session, "https://example.com/img.jpg?t=123", "/out", "img")
        assert result.cached


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

class TestCancellation:
    @pytest.mark.asyncio
    async def test_cancelled_before_fetch(self, cfg, semaphore, log_lines):
        lines, log = log_lines
        dl = make_dl(cfg, {}, semaphore, log, cancelled=True)
        session = MagicMock()

        result = await dl.fetch(session, "https://example.com/img.jpg", "/out", "img")

        assert not result.ok
        assert result.error == "cancelled"
        session.get.assert_not_called()


# ---------------------------------------------------------------------------
# HTTP failures
# ---------------------------------------------------------------------------

class TestHttpFailures:
    @pytest.mark.asyncio
    async def test_404_returns_none(self, cfg, asset_map, semaphore, log_lines):
        lines, log = log_lines
        dl = make_dl(cfg, asset_map, semaphore, log)
        resp = mock_response(status=404)
        session = mock_session(resp)

        with tempfile.TemporaryDirectory() as d:
            result = await dl.fetch(session, "https://example.com/img.jpg", d, "img")

        assert not result.ok
        assert "404" in (result.error or "")

    @pytest.mark.asyncio
    async def test_content_type_mismatch_rejected(self, cfg, asset_map, semaphore, log_lines):
        lines, log = log_lines
        dl = make_dl(cfg, asset_map, semaphore, log)
        # Trying to download as font but server returns HTML (error page)
        resp = mock_response(status=200, content=b"<html>", content_type="text/html")
        session = mock_session(resp)

        with tempfile.TemporaryDirectory() as d:
            result = await dl.fetch(session, "https://example.com/f.woff2", d, "font")

        assert not result.ok
        assert "content-type" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# Successful downloads
# ---------------------------------------------------------------------------

class TestSuccessfulDownloads:
    @pytest.mark.asyncio
    async def test_file_written_to_disk(self, cfg, asset_map, semaphore, log_lines):
        lines, log = log_lines
        dl = make_dl(cfg, asset_map, semaphore, log)
        resp = mock_response(status=200, content=b"body{}", content_type="text/css")
        session = mock_session(resp)

        with tempfile.TemporaryDirectory() as d:
            result = await dl.fetch(session, "https://example.com/style.css", d, "css")
            assert result.ok
            assert os.path.exists(result.filepath)
            assert open(result.filepath, "rb").read() == b"body{}"

    @pytest.mark.asyncio
    async def test_asset_map_populated_after_download(self, cfg, asset_map, semaphore, log_lines):
        lines, log = log_lines
        dl = make_dl(cfg, asset_map, semaphore, log)
        resp = mock_response(status=200, content=b"img", content_type="image/jpeg")
        session = mock_session(resp)

        with tempfile.TemporaryDirectory() as d:
            result = await dl.fetch(session, "https://example.com/img.jpg", d, "img")
            assert "https://example.com/img.jpg" in asset_map
            assert asset_map["https://example.com/img.jpg"] == result.filepath

    @pytest.mark.asyncio
    async def test_unique_filepath_on_collision(self, cfg, asset_map, semaphore, log_lines):
        lines, log = log_lines
        dl = make_dl(cfg, asset_map, semaphore, log)

        with tempfile.TemporaryDirectory() as d:
            # Pre-create the target file to force a collision
            existing = os.path.join(d, "img.jpg")
            open(existing, "wb").close()

            resp = mock_response(status=200, content=b"img", content_type="image/jpeg")
            session = mock_session(resp)

            result = await dl.fetch(session, "https://example.com/img.jpg", d, "img")
            assert result.ok
            assert result.filepath != existing
            assert "_1" in result.filepath

    @pytest.mark.asyncio
    async def test_webp_extension_forced_from_url(self, cfg, asset_map, semaphore, log_lines):
        lines, log = log_lines
        dl = make_dl(cfg, asset_map, semaphore, log)
        resp = mock_response(status=200, content=b"webp", content_type="application/octet-stream")
        session = mock_session(resp)

        with tempfile.TemporaryDirectory() as d:
            result = await dl.fetch(session, "https://example.com/img.webp", d, "img")
            assert result.ok
            assert result.filepath.endswith(".webp")

    @pytest.mark.asyncio
    async def test_log_called_on_success(self, cfg, asset_map, semaphore, log_lines):
        lines, log = log_lines
        dl = make_dl(cfg, asset_map, semaphore, log)
        resp = mock_response(status=200, content=b"js", content_type="application/javascript")
        session = mock_session(resp)

        with tempfile.TemporaryDirectory() as d:
            await dl.fetch(session, "https://example.com/app.js", d, "js")

        assert any("✓" in line for line in lines)


# ---------------------------------------------------------------------------
# Filename derivation
# ---------------------------------------------------------------------------

class TestFilenameDerivation:
    def test_url_without_filename(self, cfg, asset_map, semaphore, log_lines):
        _, log = log_lines
        dl = make_dl(cfg, asset_map, semaphore, log)
        name = dl._derive_filename("https://example.com/", ".css", "css")
        assert name.endswith(".css")
        assert len(name) > 4

    def test_normal_url(self, cfg, asset_map, semaphore, log_lines):
        _, log = log_lines
        dl = make_dl(cfg, asset_map, semaphore, log)
        name = dl._derive_filename("https://example.com/assets/main.css", ".css", "css")
        assert "main" in name
        assert name.endswith(".css")

    def test_special_chars_sanitised(self, cfg, asset_map, semaphore, log_lines):
        _, log = log_lines
        dl = make_dl(cfg, asset_map, semaphore, log)
        name = dl._derive_filename("https://example.com/my file (1).jpg", ".jpg", "img")
        assert " " not in name
        assert "(" not in name
