"""
cli.py
Command-line interface for Website Replicator.

Runs the full replication pipeline without any Qt dependency.
All output goes to stdout/stderr; progress is shown as a live percentage.

Usage examples:
    python main.py --url https://example.com
    python main.py --url https://example.com --depth 2 --output ./out
    python main.py --url https://example.com --passthru --delay 1.5
    python main.py --url https://example.com --blacklist "*.mp4,*.zip"
    python main.py --url https://example.com --no-resume --zip
    python main.py --url https://example.com --depth 1 --quiet
    python main.py analyse https://example.com
    python main.py --url https://example.com --verbose
    python main.py --headless --url https://example.com
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime

from .core.exporter import zip_output_dir
from .core.models import ReplicatorConfig
from .core.replicator import Replicator, analyse
from .utils.helpers import normalise_url


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


class CliOutput:
    """
    Wraps stdout/stderr output with three verbosity levels:
      --quiet   : only summary lines (no per-asset logs, no status)
      default   : progress bar + status updates + page progress
      --verbose : everything including per-asset download lines
    """

    def __init__(self, quiet: bool = False, verbose: bool = False,
                 no_colour: bool = False) -> None:
        self.quiet     = quiet
        self.verbose   = verbose and not quiet
        self.no_colour = no_colour
        self._last_pct = -1

    def log(self, msg: str) -> None:
        """Per-asset log — only shown in --verbose mode."""
        if self.verbose:
            print(f"[{_ts()}] {msg}", flush=True)

    def info(self, msg: str) -> None:
        """Always printed — used for summary lines regardless of quiet mode."""
        print(msg, flush=True)

    def err(self, msg: str) -> None:
        print(f"ERROR: {msg}", file=sys.stderr, flush=True)

    def progress(self, pct: int) -> None:
        if pct == self._last_pct:
            return
        self._last_pct = pct
        bar = "#" * (pct // 5) + "." * (20 - pct // 5)
        # Overwrite same line
        print(f"\r  [{bar}] {pct:3d}%", end="", flush=True)
        if pct == 100:
            print()  # newline on completion

    def status(self, text: str, _colour: str) -> None:
        """Status updates go to stdout in CLI mode (colour ignored)."""
        # In default mode, only show top-level status (not per-page updates
        # which are very frequent and covered by the progress bar).
        # In verbose mode, show everything.
        is_per_page = text.startswith("Page ")
        if not self.quiet and (self.verbose or not is_per_page):
            print(f"  → {text}", flush=True)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog        = "website-replicator",
        description = "Replicate websites for offline viewing.",
        formatter_class = argparse.RawDescriptionHelpFormatter,
        epilog = """
Examples:
  Replicate a site to ./out/:
    python main.py --url https://example.com --output ./out

  Crawl 2 levels deep with a 1s polite delay:
    python main.py --url https://example.com --depth 2 --delay 1.0

  Analyse only (no download):
    python main.py --analyse --url https://example.com

  Export ZIP after replication:
    python main.py --url https://example.com --zip

  Skip videos and archives:
    python main.py --url https://example.com --blacklist "*.mp4,*.zip"

  Passthru mode (images loaded from original URLs):
    python main.py --url https://example.com --passthru
        """,
    )

    # Positional (optional) subcommand
    p.add_argument(
        "command", nargs="?", default="replicate",
        choices=["replicate", "analyse"],
        help="Operation to perform (default: replicate)",
    )

    # Config file (processed before all other args)
    p.add_argument(
        "--config", "-c", default=None, metavar="FILE",
        help="JSON config file. Values are overridden by explicit CLI flags.",
    )

    # Core options
    p.add_argument("--url",    "-u", required=False, default=None, help="Target URL")
    p.add_argument("--output", "-o", default="replicated_website",
                   help="Output directory (default: replicated_website)")
    p.add_argument("--depth",  "-d", type=int, default=0,
                   help="Crawl depth — 0 = homepage only (default: 0)")

    # Download behaviour
    p.add_argument("--passthru",   action="store_true",
                   help="Keep image URLs pointing to original server")
    p.add_argument("--no-resume",  action="store_true",
                   help="Ignore existing manifest — re-download everything")
    p.add_argument("--concurrent", type=int, default=6,
                   help="Max simultaneous downloads (default: 6)")
    p.add_argument("--retries",    type=int, default=5,
                   help="Max retries per asset (default: 5)")
    p.add_argument("--timeout",    type=int, default=20,
                   help="HTTP timeout in seconds (default: 20)")

    # Politeness
    p.add_argument("--delay", type=float, default=0.0,
                   help="Seconds between requests to same domain (default: 0)")
    p.add_argument("--user-agent", default=(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    ), help="HTTP User-Agent string")

    # Asset filtering
    p.add_argument("--blacklist", default="",
                   help="Comma-separated glob patterns to skip (e.g. '*.mp4,*.zip')")

    # Post-processing
    p.add_argument("--zip", action="store_true",
                   help="Create a ZIP archive of the output after replication")
    p.add_argument("--zip-path", default=None,
                   help="Path for the ZIP file (auto-named if omitted)")

    # Output control
    p.add_argument("--quiet",    "-q", action="store_true",
                   help="Show only summary — suppress logs and progress bar")
    p.add_argument("--verbose",  "-v", action="store_true",
                   help="Show every downloaded asset (overrides --quiet)")
    p.add_argument("--no-colour", action="store_true",
                   help="Disable ANSI colour codes in output")

    return p


# ---------------------------------------------------------------------------
# Analyse command
# ---------------------------------------------------------------------------

async def run_analyse(args: argparse.Namespace, out: CliOutput) -> int:
    ok, url = normalise_url(args.url)
    if not ok:
        out.err(f"Invalid URL: {args.url}")
        return 1

    out.info(f"Analysing: {url}")
    cfg    = ReplicatorConfig(
        timeout    = args.timeout,
        user_agent = args.user_agent,
    )
    result = await analyse(url, cfg, out.log, out.status)
    if result is None:
        out.err("Analysis failed.")
        return 1

    out.info("")
    out.info("=" * 55)
    out.info("Analysis Result")
    out.info("=" * 55)
    for line in result.summary_lines():
        out.info(f"  {line}")
    out.info("=" * 55)
    return 0


# ---------------------------------------------------------------------------
# Replicate command
# ---------------------------------------------------------------------------

async def run_replicate(args: argparse.Namespace, out: CliOutput) -> int:
    ok, url = normalise_url(args.url)
    if not ok:
        out.err(f"Invalid URL: {args.url}")
        return 1

    blacklist = tuple(
        p.strip() for p in args.blacklist.split(",") if p.strip()
    ) if args.blacklist else ()

    cfg = ReplicatorConfig(
        max_retries    = args.retries,
        timeout        = args.timeout,
        user_agent     = args.user_agent,
        crawl_depth    = args.depth,
        max_concurrent = args.concurrent,
        output_dir     = args.output,
        passthru       = args.passthru,
        resume         = not args.no_resume,
        domain_delay   = args.delay,
        blacklist      = blacklist,
    )

    out.info(f"Replicating: {url}")
    out.info(f"  Output:     {args.output}")
    out.info(f"  Depth:      {args.depth}")
    out.info(f"  Concurrent: {args.concurrent}")
    if args.delay:
        out.info(f"  Delay:      {args.delay}s per domain")
    if blacklist:
        out.info(f"  Blacklist:  {', '.join(blacklist)}")
    if args.passthru:
        out.info(f"  Mode:       passthru (images not downloaded)")
    if args.no_resume:
        out.info(f"  Resume:     disabled")
    out.info("")

    rep = Replicator(
        cfg      = cfg,
        log      = out.log,
        progress = out.progress,
        status   = out.status,
    )

    # Page progress callback
    pages_counted = [0]

    def _on_counted(total: int) -> None:
        pages_counted[0] = total
        if not out.quiet:
            print(f"  Discovered {total} page(s) to replicate", flush=True)

    def _on_done(done: int, total: int) -> None:
        if not out.quiet:
            print(f"  Page {done}/{total} complete", flush=True)

    rep.set_page_callbacks(_on_counted, _on_done)

    success = await rep.run(
        url         = url,
        analysis    = None,
        passthru    = args.passthru,
        output_dir  = args.output,
        crawl_depth = args.depth,
    )

    out.info("")

    if not success:
        out.err("Replication failed — check the log above for details.")
        return 1

    # Broken link summary
    blr = rep.broken_link_report
    if blr and blr.has_errors:
        out.info(f"  Broken links: {blr.count} (see broken_links.txt in output)")
    else:
        out.info("  Broken links: none")

    out.info(f"  Site saved to: {args.output}")

    # Optional ZIP export
    if args.zip:
        try:
            zip_path = zip_output_dir(args.output, args.zip_path)
            import os
            size_mb = os.path.getsize(zip_path) / 1_048_576
            out.info(f"  ZIP archive:  {zip_path} ({size_mb:.1f} MB)")
        except Exception as exc:
            out.err(f"ZIP export failed: {exc}")

    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _load_config_file(path: str) -> dict:
    """Load a JSON config file, returning a dict of arg-name → value."""
    import json as _json
    try:
        with open(path, encoding="utf-8") as fh:
            data = _json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("Config file must be a JSON object")
        return data
    except Exception as exc:
        print(f"ERROR: Cannot load config file {path!r}: {exc}", file=sys.stderr)
        sys.exit(1)


def _apply_config_file(args: argparse.Namespace, config: dict) -> argparse.Namespace:
    """
    Apply config file values to *args* for any field the user did NOT
    explicitly set on the command line (config file = lower priority than flags).

    Supported config keys mirror the long-form flag names (without --),
    with hyphens replaced by underscores:
        url, output, depth, passthru, no_resume, concurrent,
        retries, timeout, delay, user_agent, blacklist, zip, zip_path, quiet
    """
    # Fields that are boolean flags (store_true) — only apply if config says True
    bool_flags = {"passthru", "no_resume", "zip", "quiet", "no_colour"}
    # Fields with defaults we can detect as "not explicitly set"
    # We compare against the parser defaults rather than None
    parser = build_parser()
    defaults = vars(parser.parse_args(["--url", "placeholder"]))

    for key, value in config.items():
        # Normalise hyphens → underscores
        attr = key.replace("-", "_")
        if not hasattr(args, attr):
            print(f"WARNING: Unknown config key '{key}' — ignored", file=sys.stderr)
            continue
        current = getattr(args, attr)
        default = defaults.get(attr)
        # Only override if the current value equals the parser default
        # (meaning the user didn't explicitly pass it on the command line)
        if current == default:
            if attr in bool_flags:
                setattr(args, attr, bool(value))
            else:
                setattr(args, attr, value)
    return args


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args   = parser.parse_args(argv)

    # Load config file first, then let explicit flags override it
    if args.config:
        config = _load_config_file(args.config)
        args   = _apply_config_file(args, config)

    # --url is required (but not marked required= so --config can supply it)
    if not args.url:
        parser.error("the following arguments are required: --url/-u")

    out    = CliOutput(quiet=args.quiet, verbose=getattr(args, "verbose", False),
                   no_colour=args.no_colour)

    command = args.command or "replicate"
    if command == "analyse":
        coro = run_analyse(args, out)
    else:
        coro = run_replicate(args, out)

    try:
        return asyncio.run(coro)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
