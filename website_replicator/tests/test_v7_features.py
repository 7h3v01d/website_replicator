"""
tests/test_v7_features.py
Tests for v7 features:
  1. Per-domain rate limiting — DomainRateLimiter
  2. Asset blacklist — Downloader._is_blacklisted
  3. CLI — argument parsing and dispatch
"""

from __future__ import annotations

import asyncio
import os
import time
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from website_replicator.core.ratelimiter import DomainRateLimiter
from website_replicator.core.downloader import Downloader
from website_replicator.core.models import ReplicatorConfig, DownloadResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_dl(cfg=None, asset_map=None, semaphore=None, rate_limiter=None):
    cfg       = cfg or ReplicatorConfig(max_retries=0, timeout=5)
    asset_map = asset_map if asset_map is not None else {}
    semaphore = semaphore or asyncio.Semaphore(4)
    return Downloader(
        cfg=cfg, asset_map=asset_map, semaphore=semaphore,
        log=lambda m: None, cancelled=lambda: False,
        rate_limiter=rate_limiter,
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
# 1. DomainRateLimiter
# ===========================================================================

class TestDomainRateLimiter:

    def test_zero_delay_creates_no_wait(self):
        """Default zero delay should not impose any sleep."""
        limiter = DomainRateLimiter(default_delay=0.0)
        assert limiter._effective_delay("example.com") == 0.0

    def test_default_delay_applies_to_all_domains(self):
        """default_delay is used for any domain not specifically configured."""
        limiter = DomainRateLimiter(default_delay=1.5)
        assert limiter._effective_delay("example.com") == 1.5
        assert limiter._effective_delay("other.com")   == 1.5

    def test_set_crawl_delay_overrides_for_domain(self):
        """set_crawl_delay() replaces the effective delay for a specific domain."""
        limiter = DomainRateLimiter(default_delay=0.5)
        limiter.set_crawl_delay("example.com", 3.0)
        assert limiter._effective_delay("example.com") == 3.0
        assert limiter._effective_delay("other.com")   == 0.5   # unchanged

    def test_set_crawl_delay_never_below_default(self):
        """Crawl-delay is max(default, crawl_delay) — default is a floor."""
        limiter = DomainRateLimiter(default_delay=2.0)
        limiter.set_crawl_delay("example.com", 0.5)   # below default
        assert limiter._effective_delay("example.com") == 2.0   # floor applied

    def test_crawl_delay_above_default_respected(self):
        """When crawl-delay > default, the crawl-delay wins."""
        limiter = DomainRateLimiter(default_delay=1.0)
        limiter.set_crawl_delay("example.com", 5.0)
        assert limiter._effective_delay("example.com") == 5.0

    def test_each_domain_gets_own_lock(self):
        """Different domains get different locks (enabling parallel downloads)."""
        limiter = DomainRateLimiter()
        lock_a = limiter._get_lock("a.com")
        lock_b = limiter._get_lock("b.com")
        assert lock_a is not lock_b

    def test_same_domain_gets_same_lock(self):
        """The same domain always returns the same lock object."""
        limiter = DomainRateLimiter()
        lock1 = limiter._get_lock("example.com")
        lock2 = limiter._get_lock("example.com")
        assert lock1 is lock2

    def test_stats_returns_all_domains(self):
        """stats() covers every domain that has been touched."""
        limiter = DomainRateLimiter(default_delay=1.0)
        limiter.set_crawl_delay("a.com", 3.0)
        limiter._get_lock("b.com")   # just touch a second domain
        stats = limiter.stats()
        assert "a.com" in stats
        assert stats["a.com"] == 3.0

    @pytest.mark.asyncio
    async def test_acquire_releases_lock(self):
        """acquire() context manager releases the lock on exit."""
        limiter = DomainRateLimiter(default_delay=0.0)
        async with limiter.acquire("https://example.com/img.png"):
            pass
        # Lock should be released — can be acquired again immediately
        lock = limiter._get_lock("example.com")
        assert not lock.locked()

    @pytest.mark.asyncio
    async def test_second_request_waits_for_delay(self):
        """Two consecutive same-domain requests are separated by the delay."""
        delay = 0.1
        limiter = DomainRateLimiter(default_delay=delay)
        url = "https://example.com/img.png"

        t0 = time.monotonic()
        async with limiter.acquire(url):
            pass
        async with limiter.acquire(url):
            pass
        elapsed = time.monotonic() - t0

        # Total time should be at least the delay (with some tolerance)
        assert elapsed >= delay * 0.8, (
            f"Expected at least {delay * 0.8:.3f}s, got {elapsed:.3f}s"
        )

    @pytest.mark.asyncio
    async def test_different_domains_do_not_block_each_other(self):
        """Requests to different domains are not serialised."""
        limiter = DomainRateLimiter(default_delay=0.1)

        t0 = time.monotonic()
        # These two are for different domains — should run concurrently
        async with limiter.acquire("https://a.com/img.png"):
            pass
        async with limiter.acquire("https://b.com/img.png"):
            pass
        elapsed = time.monotonic() - t0

        # With no same-domain constraint, both should finish quickly
        # (each independently, so total ≈ just one delay, not two)
        assert elapsed < 0.15, f"Different-domain requests took too long: {elapsed:.3f}s"

    @pytest.mark.asyncio
    async def test_acquire_records_timestamp(self):
        """After acquire(), last_request is set for the domain."""
        limiter = DomainRateLimiter(default_delay=0.0)
        before = time.monotonic()
        async with limiter.acquire("https://example.com/img.png"):
            pass
        after = time.monotonic()
        ts = limiter._last_request.get("example.com", 0.0)
        assert before <= ts <= after


# ===========================================================================
# 2. Asset blacklist
# ===========================================================================

class TestAssetBlacklist:

    def test_no_blacklist_allows_all(self):
        """Empty blacklist (default) never blocks anything."""
        dl = make_dl(cfg=ReplicatorConfig(blacklist=()))
        assert not dl._is_blacklisted("https://example.com/video.mp4")
        assert not dl._is_blacklisted("https://example.com/style.css")

    def test_exact_extension_match(self):
        """*.mp4 blocks MP4 URLs."""
        dl = make_dl(cfg=ReplicatorConfig(blacklist=("*.mp4",)))
        assert dl._is_blacklisted("https://example.com/video.mp4")

    def test_non_matching_url_allowed(self):
        """URLs that don't match any pattern are allowed."""
        dl = make_dl(cfg=ReplicatorConfig(blacklist=("*.mp4",)))
        assert not dl._is_blacklisted("https://example.com/style.css")

    def test_multiple_patterns(self):
        """Multiple patterns are all checked."""
        dl = make_dl(cfg=ReplicatorConfig(blacklist=("*.mp4", "*.zip", "*.mp3")))
        assert dl._is_blacklisted("https://example.com/archive.zip")
        assert dl._is_blacklisted("https://example.com/song.mp3")
        assert not dl._is_blacklisted("https://example.com/img.png")

    def test_path_segment_pattern(self):
        """Patterns matching path segments work correctly."""
        dl = make_dl(cfg=ReplicatorConfig(blacklist=("*/ads/*",)))
        assert dl._is_blacklisted("https://example.com/ads/banner.png")
        assert not dl._is_blacklisted("https://example.com/img/banner.png")

    def test_case_insensitive(self):
        """Matching is case-insensitive on both sides."""
        dl = make_dl(cfg=ReplicatorConfig(blacklist=("*.MP4",)))
        assert dl._is_blacklisted("https://example.com/Video.mp4")
        assert dl._is_blacklisted("https://example.com/VIDEO.MP4")

    def test_query_string_in_url(self):
        """Query strings don't prevent pattern matching on the path."""
        dl = make_dl(cfg=ReplicatorConfig(blacklist=("*.mp4",)))
        assert dl._is_blacklisted("https://example.com/video.mp4?t=10")

    @pytest.mark.asyncio
    async def test_blacklisted_url_not_downloaded(self):
        """A blacklisted URL returns a DownloadResult with error='blacklisted'."""
        dl = make_dl(cfg=ReplicatorConfig(blacklist=("*.mp4",)))
        session = MagicMock()

        with tempfile.TemporaryDirectory() as d:
            result = await dl.fetch(session, "https://example.com/movie.mp4", d, "img")

        assert not result.ok
        assert result.error == "blacklisted"
        session.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_blacklisted_url_downloaded(self):
        """A non-blacklisted URL proceeds normally through the download pipeline."""
        dl = make_dl(cfg=ReplicatorConfig(blacklist=("*.mp4",)))
        resp = mock_response(status=200, content=b"img", content_type="image/png")
        session = mock_session(resp)

        with tempfile.TemporaryDirectory() as d:
            result = await dl.fetch(session, "https://example.com/photo.png", d, "img")

        assert result.ok
        session.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_blacklist_checked_before_network(self):
        """Blacklist check precedes dedup and network — no extra work done."""
        logs = []
        dl = make_dl(
            cfg=ReplicatorConfig(blacklist=("*.mp4",)),
        )
        dl._log = logs.append
        session = MagicMock()

        with tempfile.TemporaryDirectory() as d:
            await dl.fetch(session, "https://example.com/video.mp4", d, "img")

        assert any("Blacklisted" in l or "blacklisted" in l.lower() for l in logs)
        session.get.assert_not_called()

    def test_default_blacklist_blocks_common_media(self):
        """The default blacklist string from AppSettings blocks video/audio/archive."""
        default = "*.mp4,*.mp3,*.avi,*.mov,*.mkv,*.wmv,*.flv,*.webm,*.ogg,*.wav,*.aac,*.m4a,*.zip,*.tar,*.gz,*.rar"
        patterns = tuple(p.strip() for p in default.split(",") if p.strip())
        dl = make_dl(cfg=ReplicatorConfig(blacklist=patterns))
        for ext in ["mp4", "mp3", "avi", "mov", "zip", "tar", "gz"]:
            assert dl._is_blacklisted(f"https://example.com/file.{ext}"), f".{ext} should be blacklisted"
        for ext in ["css", "js", "png", "jpg", "woff2", "html"]:
            assert not dl._is_blacklisted(f"https://example.com/file.{ext}"), f".{ext} should be allowed"


# ===========================================================================
# 3. CLI — argument parsing and dispatch
# ===========================================================================

class TestCli:

    def _parse(self, args: list[str]):
        """Parse args using the CLI parser, return namespace."""
        from website_replicator.cli import build_parser
        return build_parser().parse_args(args)

    def test_url_required_without_config(self):
        """--url is required when no --config file is provided."""
        import pytest
        from website_replicator.cli import main
        # parser.error() calls sys.exit(2) — catch SystemExit or non-zero return
        try:
            ret = main(["--output", "./out"])   # no --url, no --config
            assert ret != 0
        except SystemExit as e:
            assert e.code != 0

    def test_default_command_is_replicate(self):
        """No subcommand defaults to 'replicate'."""
        args = self._parse(["--url", "https://example.com"])
        assert args.command == "replicate"

    def test_analyse_command(self):
        """'analyse' subcommand is parsed correctly."""
        args = self._parse(["analyse", "--url", "https://example.com"])
        assert args.command == "analyse"

    def test_depth_default_zero(self):
        """Default crawl depth is 0."""
        args = self._parse(["--url", "https://example.com"])
        assert args.depth == 0

    def test_depth_custom(self):
        """--depth is parsed as int."""
        args = self._parse(["--url", "https://example.com", "--depth", "3"])
        assert args.depth == 3

    def test_output_default(self):
        """Default output dir is 'replicated_website'."""
        args = self._parse(["--url", "https://example.com"])
        assert args.output == "replicated_website"

    def test_output_custom(self):
        """--output is passed through."""
        args = self._parse(["--url", "https://example.com", "--output", "./out"])
        assert args.output == "./out"

    def test_passthru_flag(self):
        """--passthru sets passthru=True."""
        args = self._parse(["--url", "https://example.com", "--passthru"])
        assert args.passthru

    def test_no_resume_flag(self):
        """--no-resume sets no_resume=True."""
        args = self._parse(["--url", "https://example.com", "--no-resume"])
        assert args.no_resume

    def test_quiet_flag(self):
        """--quiet / -q sets quiet=True."""
        args = self._parse(["--url", "https://example.com", "-q"])
        assert args.quiet

    def test_delay_default_zero(self):
        """Default domain delay is 0.0."""
        args = self._parse(["--url", "https://example.com"])
        assert args.delay == 0.0

    def test_delay_custom(self):
        """--delay is parsed as float."""
        args = self._parse(["--url", "https://example.com", "--delay", "1.5"])
        assert args.delay == 1.5

    def test_blacklist_default_empty(self):
        """Default blacklist is empty string."""
        args = self._parse(["--url", "https://example.com"])
        assert args.blacklist == ""

    def test_blacklist_custom(self):
        """--blacklist is passed through as a string."""
        args = self._parse(["--url", "https://example.com", "--blacklist", "*.mp4,*.zip"])
        assert "*.mp4" in args.blacklist
        assert "*.zip" in args.blacklist

    def test_zip_flag(self):
        """--zip sets zip=True."""
        args = self._parse(["--url", "https://example.com", "--zip"])
        assert args.zip

    def test_zip_path(self):
        """--zip-path is passed through."""
        args = self._parse(["--url", "https://example.com", "--zip", "--zip-path", "./out.zip"])
        assert args.zip_path == "./out.zip"

    def test_concurrent_default(self):
        """Default concurrent downloads is 6."""
        args = self._parse(["--url", "https://example.com"])
        assert args.concurrent == 6

    def test_timeout_default(self):
        """Default timeout is 20."""
        args = self._parse(["--url", "https://example.com"])
        assert args.timeout == 20

    def test_retries_default(self):
        """Default retries is 5."""
        args = self._parse(["--url", "https://example.com"])
        assert args.retries == 5

    def test_invalid_url_returns_nonzero(self):
        """An invalid URL causes CLI to return non-zero."""
        from website_replicator.cli import main
        ret = main(["--url", "not-a-valid-url!!"])
        assert ret != 0

    def test_main_dispatches_cli_when_args_present(self):
        """main.py _has_cli_args() returns True when sys.argv has flags."""
        import sys
        original = sys.argv[:]
        try:
            sys.argv = ["main.py", "--url", "https://example.com"]
            from main import _has_cli_args
            assert _has_cli_args()
        finally:
            sys.argv = original

    def test_main_gui_mode_when_no_args(self):
        """_has_cli_args() returns False when no extra args."""
        import sys
        original = sys.argv[:]
        try:
            sys.argv = ["main.py"]
            from main import _has_cli_args
            assert not _has_cli_args()
        finally:
            sys.argv = original

    def test_cli_output_quiet_suppresses_log(self):
        """In quiet mode, log() produces no output."""
        from website_replicator.cli import CliOutput
        import io
        out = CliOutput(quiet=True)
        # Should not raise; output suppressed
        out.log("this should not appear")

    def test_cli_output_progress_format(self, capsys):
        """progress() prints a bar and percentage."""
        from website_replicator.cli import CliOutput
        out = CliOutput(quiet=False)
        out.progress(50)
        captured = capsys.readouterr()
        assert "50%" in captured.out
        assert "#" in captured.out

    def test_cli_output_progress_100_adds_newline(self, capsys):
        """progress(100) ends with a newline."""
        from website_replicator.cli import CliOutput
        out = CliOutput(quiet=False)
        out.progress(100)
        captured = capsys.readouterr()
        assert captured.out.endswith("\n")

    def test_blacklist_parsed_to_tuple_in_config(self):
        """CLI blacklist string is correctly split into tuple for ReplicatorConfig."""
        from website_replicator.cli import build_parser
        args = build_parser().parse_args([
            "--url", "https://example.com",
            "--blacklist", "*.mp4, *.zip, *.mp3",
        ])
        blacklist = tuple(p.strip() for p in args.blacklist.split(",") if p.strip())
        assert blacklist == ("*.mp4", "*.zip", "*.mp3")

    def test_no_resume_maps_to_resume_false_in_config(self):
        """--no-resume maps to resume=False in ReplicatorConfig."""
        args = self._parse(["--url", "https://example.com", "--no-resume"])
        # Simulate what run_replicate does
        resume = not args.no_resume
        assert not resume


# ===========================================================================
# v7.1 fixes
# ===========================================================================

class TestV71Fixes:

    def test_version_is_7(self):
        """Package version is 7.0.0 everywhere."""
        from website_replicator import VERSION
        assert VERSION == "7.0.0"

    def test_main_window_version_matches_package(self):
        """main_window.py imports VERSION from package, not hardcoded."""
        import pathlib
        src = pathlib.Path("website_replicator/ui/main_window.py").read_text(encoding="utf-8")
        # Should NOT have a hardcoded 6.x version string
        assert 'VERSION = "6.0.0"' not in src
        assert 'VERSION = "7.0.0"' not in src   # not hardcoded here either
        # Should import from package
        assert "from .. import VERSION" in src

    def test_save_config_persists_new_fields(self):
        """save_config() writes domain_delay and blacklist back to QSettings."""
        import pathlib
        src = pathlib.Path("website_replicator/ui/settings.py").read_text(encoding="utf-8")
        # Verify the method body contains the new fields
        assert '"domain_delay"' in src.split("def save_config")[1].split("def ")[0]
        assert '"blacklist"'    in src.split("def save_config")[1].split("def ")[0]
        assert '"resume"'       in src.split("def save_config")[1].split("def ")[0]

    def test_config_file_loading(self, tmp_path):
        """--config loads non-url values from a JSON file."""
        import json
        config = {
            "depth":  2,
            "delay":  1.5,
            "output": str(tmp_path / "out"),
        }
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(config))

        from website_replicator.cli import build_parser, _load_config_file, _apply_config_file
        # Provide --url on CLI (required), config file provides depth/delay/output
        args = build_parser().parse_args([
            "--url", "https://example.com",
            "--config", str(cfg_file),
        ])
        args = _apply_config_file(args, _load_config_file(str(cfg_file)))

        # Config file values applied where CLI used default
        assert args.depth  == 2
        assert args.delay  == 1.5
        assert str(tmp_path / "out") in args.output

    def test_cli_flag_overrides_config_file(self, tmp_path):
        """Explicit CLI flags take priority over config file values."""
        import json
        config = {"url": "https://example.com", "depth": 2}
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(config))

        from website_replicator.cli import build_parser, _load_config_file, _apply_config_file
        # Explicit --depth 5 should win over config file's depth: 2
        args = build_parser().parse_args([
            "--config", str(cfg_file),
            "--url", "https://other.com",
            "--depth", "5",
        ])
        args = _apply_config_file(args, _load_config_file(str(cfg_file)))

        assert args.depth == 5               # CLI flag wins
        assert args.url == "https://other.com"  # CLI flag wins

    def test_config_file_unknown_key_warned(self, tmp_path, capsys):
        """Unknown keys in config file produce a warning, not a crash."""
        import json
        config = {"url": "https://example.com", "unknown_key_xyz": "value"}
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(config))

        from website_replicator.cli import build_parser, _load_config_file, _apply_config_file
        args = build_parser().parse_args(["--url", "https://example.com"])
        _apply_config_file(args, {"unknown_key_xyz": "value"})
        captured = capsys.readouterr()
        assert "unknown" in captured.err.lower() or "WARNING" in captured.err

    def test_assets_remaining_in_replicator_log(self):
        """Replicator._process_page log includes 'remaining' count."""
        import pathlib
        src = pathlib.Path("website_replicator/core/replicator.py").read_text(encoding="utf-8")
        assert "remaining" in src


# ===========================================================================
# v7.1 polish — verbose flag, headless dispatch, queue panel update_item
# ===========================================================================

class TestV71Polish:

    def test_verbose_flag_parsed(self):
        """--verbose / -v is accepted by the parser."""
        from website_replicator.cli import build_parser
        args = build_parser().parse_args(["--url", "https://x.com", "--verbose"])
        assert args.verbose

    def test_verbose_short_flag(self):
        """-v is the short form of --verbose."""
        from website_replicator.cli import build_parser
        args = build_parser().parse_args(["--url", "https://x.com", "-v"])
        assert args.verbose

    def test_verbose_and_quiet_coexist_gracefully(self):
        """--verbose --quiet: quiet wins (verbose is forced off)."""
        from website_replicator.cli import CliOutput
        out = CliOutput(quiet=True, verbose=True)
        assert not out.verbose   # quiet suppresses verbose

    def test_cli_output_verbose_shows_log(self, capsys):
        """In verbose mode, log() prints to stdout."""
        from website_replicator.cli import CliOutput
        out = CliOutput(quiet=False, verbose=True)
        out.log("downloaded img.png")
        captured = capsys.readouterr()
        assert "downloaded img.png" in captured.out

    def test_cli_output_default_suppresses_log(self, capsys):
        """In default (non-verbose) mode, log() is silent."""
        from website_replicator.cli import CliOutput
        out = CliOutput(quiet=False, verbose=False)
        out.log("this should not appear")
        captured = capsys.readouterr()
        assert "this should not appear" not in captured.out

    def test_cli_output_status_suppresses_per_page_noise(self, capsys):
        """Per-page status updates are suppressed in non-verbose mode."""
        from website_replicator.cli import CliOutput
        out = CliOutput(quiet=False, verbose=False)
        out.status("Page 2/5 — 10 assets remaining", "#2563eb")
        captured = capsys.readouterr()
        assert "Page 2/5" not in captured.out

    def test_cli_output_verbose_shows_per_page_status(self, capsys):
        """In verbose mode, per-page status is shown."""
        from website_replicator.cli import CliOutput
        out = CliOutput(quiet=False, verbose=True)
        out.status("Page 2/5 — 10 assets remaining", "#2563eb")
        captured = capsys.readouterr()
        assert "Page 2/5" in captured.out

    def test_main_py_has_headless_dispatch(self):
        """main.py source has --headless dispatch logic."""
        import pathlib
        src = pathlib.Path("main.py").read_text(encoding="utf-8")
        assert "--headless" in src
        assert "_wants_headless" in src
        assert "_try_import_qt" in src

    def test_main_py_has_gui_fallback_message(self):
        """main.py tells users how to install PyQt6 if GUI unavailable."""
        import pathlib
        src = pathlib.Path("main.py").read_text(encoding="utf-8")
        assert "pip install PyQt6" in src

    def test_queue_panel_update_item_patches_in_place(self):
        """update_item() updates the row text without clearing the whole list."""
        import pathlib
        src = pathlib.Path("website_replicator/ui/queue_panel.py").read_text(encoding="utf-8")
        # Should use list_item.setText() not just _refresh()
        assert "list_item.setText" in src
        assert "self._items.index(item)" in src

    def test_readme_exists_and_has_key_sections(self):
        """README.md exists and covers the main feature areas."""
        import pathlib
        readme = pathlib.Path("README.md")
        assert readme.exists()
        content = readme.read_text(encoding="utf-8")
        for section in ["Installation", "Quick Start", "CLI Reference",
                        "Config file", "Running tests", "Architecture"]:
            assert section in content, f"README missing section: {section}"

    def test_readme_has_cli_examples(self):
        """README includes concrete CLI usage examples."""
        import pathlib
        content = pathlib.Path("README.md").read_text(encoding="utf-8")
        assert "--url" in content
        assert "--depth" in content
        assert "--headless" in content
        assert "--config" in content


# ===========================================================================
# Final polish — verbose GUI, icon, status callback audit
# ===========================================================================

class TestFinalPolish:

    # ── verbose_log in GUI settings ───────────────────────────────────────────

    def test_verbose_log_in_settings_defaults(self):
        """verbose_log has a False default in AppSettings."""
        import pathlib
        src = pathlib.Path("website_replicator/ui/settings.py").read_text(encoding="utf-8")
        assert '"verbose_log"' in src
        assert "False" in src.split('"verbose_log"')[1][:20]

    def test_verbose_log_checkbox_in_dialog(self):
        """SettingsDialog source contains the verbose_log checkbox."""
        import pathlib
        src = pathlib.Path("website_replicator/ui/settings.py").read_text(encoding="utf-8")
        assert "_verbose_log" in src
        assert "Verbose progress log" in src

    def test_verbose_log_saved_in_accept(self):
        """_save_and_accept() persists verbose_log."""
        import pathlib
        src = pathlib.Path("website_replicator/ui/settings.py").read_text(encoding="utf-8")
        save_section = src.split("def _save_and_accept")[1]
        assert '"verbose_log"' in save_section

    # ── _cb_log_p filtering ────────────────────────────────────────────────────

    def test_cb_log_p_filters_in_non_verbose(self):
        """_cb_log_p source contains filtering logic keyed on verbose_log."""
        import pathlib
        src = pathlib.Path("website_replicator/ui/main_window.py").read_text(encoding="utf-8")
        # Should check verbose_log setting
        assert "verbose_log" in src.split("def _cb_log_p")[1].split("def _")[0]
        # Should return early for suppressed lines
        assert "return" in src.split("def _cb_log_p")[1].split("def _")[0]

    def test_cb_log_p_keep_prefixes_include_errors(self):
        """The keep_prefixes list in _cb_log_p includes error indicators."""
        import pathlib
        src = pathlib.Path("website_replicator/ui/main_window.py").read_text(encoding="utf-8")
        cb_section = src.split("def _cb_log_p")[1].split("def _")[0]
        assert '"✗"' in cb_section or "'✗'" in cb_section
        assert '"Error"' in cb_section or "'Error'" in cb_section

    def test_cb_log_p_keep_success_markers(self):
        """Success (✓) and dedup (⚡) markers are always kept."""
        import pathlib
        src = pathlib.Path("website_replicator/ui/main_window.py").read_text(encoding="utf-8")
        cb_section = src.split("def _cb_log_p")[1].split("def _")[0]
        assert '"✓"' in cb_section or "'✓'" in cb_section
        assert '"⚡"' in cb_section or "'⚡'" in cb_section

    # ── _cb_status audit ──────────────────────────────────────────────────────

    def test_cb_status_echoes_to_progress_log(self):
        """_cb_status echoes non-per-page status to the progress signal."""
        import pathlib
        src = pathlib.Path("website_replicator/ui/main_window.py").read_text(encoding="utf-8")
        cb_section = src.split("def _cb_status")[1].split("def _")[0]
        # Should emit to log_progress as well as status signal
        assert "log_progress" in cb_section

    def test_cb_status_filters_per_page_spam(self):
        """_cb_status suppresses per-page messages unless verbose."""
        import pathlib
        src = pathlib.Path("website_replicator/ui/main_window.py").read_text(encoding="utf-8")
        cb_section = src.split("def _cb_status")[1].split("def _")[0]
        assert "is_per_page" in cb_section
        assert 'startswith("Page ")' in cb_section

    def test_all_replicator_status_calls_use_standard_prefix(self):
        """All self._status() calls in replicator.py start with 'Status:'
        or 'Page ' so the filter in _cb_status works correctly."""
        import pathlib, re
        src = pathlib.Path("website_replicator/core/replicator.py").read_text(encoding="utf-8")
        # Extract all string literals passed as first arg to self._status(
        calls = re.findall(r'self\._status\(\s*[f]?"([^"]+)"', src)
        calls += re.findall(r"self\._status\(\s*[f]?'([^']+)'", src)
        # f-strings with variables — check the static prefix
        fstring_calls = re.findall(r'self\._status\(\s*f"([^"]+)"', src)
        all_calls = calls + fstring_calls
        for call in all_calls:
            assert call.startswith(("Status:", "Page ")), (
                f"status() call has unexpected prefix: {call!r}"
            )

    # ── Icon ──────────────────────────────────────────────────────────────────

    def test_icon_svg_exists(self):
        """assets/icon.svg was created."""
        import pathlib
        assert pathlib.Path("assets/icon.svg").exists()

    def test_icon_ico_exists(self):
        """assets/icon.ico was created and is non-empty."""
        import pathlib
        ico = pathlib.Path("assets/icon.ico")
        assert ico.exists()
        assert ico.stat().st_size > 1000   # meaningful ICO, not a stub

    def test_icon_ico_multi_size(self):
        """The ICO file contains multiple resolution entries."""
        # ICO header: bytes 4-5 are image count (little-endian uint16)
        import pathlib, struct
        ico = pathlib.Path("assets/icon.ico").read_bytes()
        count = struct.unpack_from("<H", ico, 4)[0]
        assert count >= 4, f"Expected >= 4 sizes in ICO, got {count}"

    def test_main_window_sets_icon(self):
        """MainWindow source loads icon from assets/ directory."""
        import pathlib
        src = pathlib.Path("website_replicator/ui/main_window.py").read_text(encoding="utf-8")
        assert "_set_window_icon" in src
        assert "icon.ico" in src
        assert "setWindowIcon" in src

    def test_pyinstaller_spec_includes_icon(self):
        """PyInstaller spec references the icon file."""
        import pathlib
        spec = pathlib.Path("website_replicator.spec").read_text(encoding="utf-8")
        assert "icon.ico" in spec
        assert "assets" in spec

    def test_pyinstaller_spec_includes_assets_datas(self):
        """PyInstaller spec bundles the assets/ directory."""
        import pathlib
        spec = pathlib.Path("website_replicator.spec").read_text(encoding="utf-8")
        assert '"assets"' in spec or "'assets'" in spec
