"""
tests/test_fixes.py
Regression tests for the 6 fixes applied in v6.2:

  Fix 1 — Progress driven by on_downloaded callback, no double-counting
  Fix 2 — CSS processed from disk, not re-fetched
  Fix 3 — pages_done/pages_total updated via set_page_callbacks
  Fix 4 — discover_pages no longer creates directories
  Fix 5 — Preview server traversal guard (structural check)
  Fix 6 — cssutils not imported in replicator.py
"""

from __future__ import annotations

import asyncio
import inspect
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from website_replicator.core.downloader import Downloader
from website_replicator.core.models import ReplicatorConfig, DownloadResult
from website_replicator.core.crawler import discover_pages, _page_output_path
from website_replicator.core import replicator as replicator_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_dl(asset_map=None, semaphore=None, log=None, on_downloaded=None, cancelled=False):
    cfg       = ReplicatorConfig(max_retries=0, timeout=5)
    asset_map = asset_map if asset_map is not None else {}
    semaphore = semaphore or asyncio.Semaphore(2)
    log       = log or (lambda m: None)
    return Downloader(
        cfg=cfg, asset_map=asset_map, semaphore=semaphore,
        log=log, cancelled=lambda: cancelled,
        on_downloaded=on_downloaded,
    )


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
# Fix 1 — Progress driven by on_downloaded; no double-counting
# ===========================================================================

class TestProgressCallback:

    @pytest.mark.asyncio
    async def test_on_downloaded_called_once_per_real_download(self):
        """on_downloaded fires exactly once per written file, not on dedup hits."""
        counter = [0]
        dl = make_dl(on_downloaded=lambda: counter.__setitem__(0, counter[0] + 1))
        resp = mock_response(status=200, content=b"body{}", content_type="text/css")
        session = mock_session(resp)

        with tempfile.TemporaryDirectory() as d:
            await dl.fetch(session, "https://example.com/a.css", d, "css")
            assert counter[0] == 1

            # Second call to same URL hits dedup — counter must NOT increment
            await dl.fetch(session, "https://example.com/a.css", d, "css")
            assert counter[0] == 1

    @pytest.mark.asyncio
    async def test_on_downloaded_not_called_on_failure(self):
        """on_downloaded must NOT fire when download fails."""
        counter = [0]
        dl = make_dl(on_downloaded=lambda: counter.__setitem__(0, counter[0] + 1))
        resp = mock_response(status=404)
        session = mock_session(resp)

        with tempfile.TemporaryDirectory() as d:
            result = await dl.fetch(session, "https://example.com/missing.css", d, "css")

        assert not result.ok
        assert counter[0] == 0

    @pytest.mark.asyncio
    async def test_on_downloaded_not_called_on_resume(self):
        """Resumed files (existing on disk) count as dedup — callback silent."""
        counter = [0]
        dl = make_dl(on_downloaded=lambda: counter.__setitem__(0, counter[0] + 1))
        session = MagicMock()

        with tempfile.TemporaryDirectory() as d:
            # Pre-create file to trigger resume path
            existing = os.path.join(d, "style.css")
            with open(existing, "wb") as f:
                f.write(b"body{}")

            await dl.fetch(session, "https://example.com/style.css", d, "css")

        assert counter[0] == 0
        session.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_downloaded_none_does_not_crash(self):
        """Downloader works fine when on_downloaded is None."""
        dl = make_dl(on_downloaded=None)
        resp = mock_response(status=200, content=b"img", content_type="image/png")
        session = mock_session(resp)

        with tempfile.TemporaryDirectory() as d:
            result = await dl.fetch(session, "https://example.com/img.png", d, "img")

        assert result.ok

    @pytest.mark.asyncio
    async def test_multiple_files_count_correctly(self):
        """Each unique file increments the counter independently."""
        counter = [0]
        dl = make_dl(on_downloaded=lambda: counter.__setitem__(0, counter[0] + 1))
        session_a = mock_session(mock_response(content=b"a", content_type="text/css"))
        session_b = mock_session(mock_response(content=b"b", content_type="text/css"))

        with tempfile.TemporaryDirectory() as d:
            await dl.fetch(session_a, "https://example.com/a.css", d, "css")
            await dl.fetch(session_b, "https://example.com/b.css", d, "css")

        assert counter[0] == 2


# ===========================================================================
# Fix 2 — CSS read from disk, not re-fetched
# ===========================================================================

class TestCssReadFromDisk:
    """
    Verify that _process_css reads from the local file (css_fp)
    rather than making another HTTP request.
    """

    def test_process_css_reads_file_not_network(self):
        """_process_css source must use open(css_fp) not session.get(css_url)."""
        import inspect
        from website_replicator.core.replicator import Replicator
        src = inspect.getsource(Replicator._process_css)

        # Must read local file
        assert "open(css_fp" in src or 'open(css_fp,' in src

        # Must NOT make a network request
        assert "session.get" not in src
        assert "await session" not in src

    def test_process_css_not_async_session_dependent(self):
        """_process_css should be async but not need session for the CSS content."""
        import inspect
        from website_replicator.core.replicator import Replicator
        src = inspect.getsource(Replicator._process_css)
        # Should still be async (for dl.fetch calls inside it)
        assert "async def _process_css" in src


# ===========================================================================
# Fix 3 — pages_done/pages_total updated via set_page_callbacks
# ===========================================================================

class TestPageCallbacks:

    def test_set_page_callbacks_exists(self):
        """Replicator must have a set_page_callbacks method."""
        from website_replicator.core.replicator import Replicator
        assert hasattr(Replicator, "set_page_callbacks")

    def test_page_callbacks_stored(self):
        """Callbacks passed to set_page_callbacks are stored correctly."""
        from website_replicator.core.replicator import Replicator
        from website_replicator.core.models import ReplicatorConfig

        rep = Replicator(
            cfg      = ReplicatorConfig(),
            log      = lambda m: None,
            progress = lambda p: None,
            status   = lambda t, c: None,
        )
        counted = [None]
        done    = [None, None]

        rep.set_page_callbacks(
            on_counted = lambda total: counted.__setitem__(0, total),
            on_done    = lambda d, t: done.__setitem__(0, d) or done.__setitem__(1, t),
        )

        assert rep._on_pages_counted is not None
        assert rep._on_page_done     is not None

        # Fire them
        rep._on_pages_counted(5)
        assert counted[0] == 5

        rep._on_page_done(2, 5)
        assert done[0] == 2
        assert done[1] == 5

    def test_callbacks_default_to_none(self):
        """Without set_page_callbacks, callbacks are None (no crash)."""
        from website_replicator.core.replicator import Replicator
        from website_replicator.core.models import ReplicatorConfig

        rep = Replicator(
            cfg      = ReplicatorConfig(),
            log      = lambda m: None,
            progress = lambda p: None,
            status   = lambda t, c: None,
        )
        assert rep._on_pages_counted is None
        assert rep._on_page_done     is None


# ===========================================================================
# Fix 4 — discover_pages no longer creates directories
# ===========================================================================

class TestCrawlerNoMkdir:

    def test_discover_pages_source_has_no_makedirs(self):
        """discover_pages must not call os.makedirs."""
        import inspect
        src = inspect.getsource(discover_pages)
        assert "makedirs" not in src
        assert "mkdir" not in src

    def test_page_output_path_does_not_create_dirs(self, tmp_path):
        """_page_output_path is pure: just computes paths, creates nothing."""
        before = list(tmp_path.iterdir())
        out_dir, html = _page_output_path(
            "https://example.com/about",
            "https://example.com",
            str(tmp_path / "out")
        )
        after = list(tmp_path.iterdir())
        # No directories should have been created
        assert before == after

    def test_process_page_creates_dirs(self):
        """Replicator._process_page source must call makedirs for page.out_dir."""
        from website_replicator.core.replicator import Replicator
        import inspect
        src = inspect.getsource(Replicator._process_page)
        assert "page.out_dir" in src
        assert "makedirs" in src


# ===========================================================================
# Fix 5 — Preview server: no os.chdir, traversal guard present
#
# These tests read main_window.py directly from disk rather than importing
# it, because importing triggers a full PyQt6 DLL load which fails in
# headless / non-QApplication test contexts on Windows.
# ===========================================================================

def _main_window_source() -> str:
    """Read main_window.py from disk — no Qt import required."""
    import pathlib
    path = pathlib.Path(__file__).parent.parent / "ui" / "main_window.py"
    return path.read_text(encoding="utf-8")


def _preview_site_source() -> str:
    """Extract just the _preview_site method body from the source file."""
    src = _main_window_source()
    # Find the method and return everything from its def to the next same-level def
    start = src.find("    def _preview_site(")
    if start == -1:
        return src  # fallback: return full file
    # Find the next method at the same indentation level
    next_def = src.find("\n    def ", start + 1)
    return src[start:next_def] if next_def != -1 else src[start:]


class TestPreviewServerSecurity:

    def test_no_os_chdir_in_preview_server(self):
        """_preview_site must not call os.chdir (global cwd mutation)."""
        src = _preview_site_source()
        # Strip comment lines before checking — a comment may mention chdir
        code_lines = [l for l in src.splitlines() if not l.strip().startswith("#")]
        code_only = "\n".join(code_lines)
        assert "os.chdir(" not in code_only
        assert ".chdir(" not in code_only

    def test_directory_param_used(self):
        """Handler must use directory= parameter instead of chdir."""
        src = _preview_site_source()
        assert "directory=" in src

    def test_traversal_guard_present(self):
        """Handler must check that resolved path stays inside serve root."""
        src = _preview_site_source()
        assert "startswith" in src
        assert "403" in src or "Forbidden" in src

    def test_realpath_used_for_serve_root(self):
        """realpath is used to normalise the serve root (prevents symlink escape)."""
        src = _preview_site_source()
        assert "realpath" in src


# ===========================================================================
# Fix 6 — cssutils not imported in replicator.py
# ===========================================================================

class TestNoCssutilsInReplicator:

    def test_cssutils_not_imported_in_replicator(self):
        """replicator.py must not import cssutils (it's in utils/css_parser.py)."""
        import inspect
        src = inspect.getsource(replicator_module)
        assert "import cssutils" not in src
        assert "cssutils.log" not in src

    def test_cssutils_still_available_in_css_parser(self):
        """css_parser.py may still use cssutils if needed (currently doesn't but may)."""
        from website_replicator.utils import css_parser
        # Just verify the module imports cleanly — no assertion about cssutils presence
        assert css_parser is not None
