# Website Replicator

A production-quality website replicator for offline viewing, built with Python.
Available as both a **desktop GUI** (PyQt6) and a **command-line tool** (no Qt required).

---

## Features

| Feature | Detail |
|---|---|
| **Static site replication** | Downloads HTML, CSS, JS, images, fonts, and misc assets |
| **Path rewriting** | All local links updated to relative paths — opens correctly offline |
| **Crawl depth** | Follow internal links N levels deep (0 = homepage only) |
| **Resume mode** | Skip already-downloaded assets on subsequent runs |
| **robots.txt** | Respects `Disallow` rules and `Crawl-delay` |
| **Per-domain rate limiting** | Configurable delay between requests to the same host |
| **Asset blacklist** | Glob patterns to skip (e.g. `*.mp4,*.zip`) |
| **Passthru mode** | Keep image URLs pointing to original server |
| **ZIP export** | Archive the entire replicated site |
| **Broken link report** | `broken_links.txt` written after each job |
| **Download queue** | Queue multiple jobs; runs them sequentially |
| **Verbose log mode** | Per-asset logging toggle in Settings and CLI (`--verbose`) |
| **Dark / light mode** | Persistent theme preference |
| **System tray** | Minimise to tray; completion notification |
| **Config file** | `--config mysite.json` for repeatable CLI jobs |

---

## Installation

```bash
# Clone or unzip, then install with pip (recommended)
pip install -e ".[gui]"          # GUI + CLI
pip install -e "."               # CLI only (no PyQt6)
pip install -e ".[dev]"          # everything + test tools

# Or install dependencies manually
pip install aiohttp beautifulsoup4 validators cssutils   # CLI only
pip install PyQt6                                        # add GUI support
```

**Python 3.11+ required.**

---

## Quick Start

### GUI

```bash
python main.py
```

### CLI

```bash
# Replicate a site
python main.py --url https://example.com

# Crawl 2 levels deep, 1s polite delay, export as ZIP
python main.py --url https://example.com --depth 2 --delay 1.0 --zip

# Analyse only (no download)
python main.py analyse --url https://example.com

# Headless mode (no PyQt6 needed)
python main.py --headless --url https://example.com --output ./sites/example

# Force GUI explicitly
python main.py --gui
```

---

## CLI Reference

```
python main.py [--headless] [--gui] [{replicate,analyse}] --url URL [options]
```

### Commands

| Command | Description |
|---|---|
| `replicate` | Download the site (default) |
| `analyse` | Inspect the site without downloading |

### Core options

| Flag | Default | Description |
|---|---|---|
| `--url`, `-u` | required | Target URL |
| `--output`, `-o` | `replicated_website` | Output directory |
| `--depth`, `-d` | `0` | Crawl depth (0 = homepage only) |
| `--config`, `-c` | — | JSON config file (overridden by explicit flags) |

### Download behaviour

| Flag | Default | Description |
|---|---|---|
| `--passthru` | off | Keep image URLs pointing to original server |
| `--no-resume` | off | Re-download everything, ignore existing manifest |
| `--concurrent` | `6` | Max simultaneous HTTP connections |
| `--retries` | `5` | Max retries per failed asset |
| `--timeout` | `20` | HTTP timeout in seconds |

### Politeness

| Flag | Default | Description |
|---|---|---|
| `--delay` | `0.0` | Seconds between requests to same domain |
| `--user-agent` | Chrome UA | HTTP User-Agent string |
| `--blacklist` | `""` | Comma-separated glob patterns to skip |

### Post-processing

| Flag | Default | Description |
|---|---|---|
| `--zip` | off | Create ZIP archive after replication |
| `--zip-path` | auto | Path for the ZIP file |

### Output control

| Flag | Description |
|---|---|
| `--quiet`, `-q` | Show only summary lines |
| `--verbose`, `-v` | Show every downloaded asset |
| `--no-colour` | Disable ANSI colour codes |

---

## Config file

Create a JSON file with any CLI flag names (hyphens → underscores):

```json
{
  "url":        "https://example.com",
  "output":     "./sites/example",
  "depth":      2,
  "delay":      1.0,
  "concurrent": 4,
  "blacklist":  "*.mp4,*.mp3,*.zip",
  "zip":        true
}
```

```bash
python main.py --config mysite.json

# Override individual values
python main.py --config mysite.json --depth 0 --no-resume
```

---

## Default asset blacklist

The GUI settings and CLI both ship with a sensible default blacklist that skips
common large/non-essential file types:

```
*.mp4, *.mp3, *.avi, *.mov, *.mkv, *.wmv, *.flv, *.webm,
*.ogg, *.wav, *.aac, *.m4a, *.zip, *.tar, *.gz, *.rar
```

Edit via **Settings → Politeness → Asset Blacklist** in the GUI, or pass
`--blacklist` on the CLI.

---

## Package layout

```
website_replicator/
├── __init__.py             ← Package version (VERSION = "7.0.0")
├── __main__.py             ← Enables: python -m website_replicator
├── _gui_entry.py           ← GUI entry point for pip-installed script
├── cli.py                  ← CLI entry point (no Qt dependency)
├── core/
│   ├── models.py           ← ReplicatorConfig, AnalysisResult, QueueItem
│   ├── replicator.py       ← Orchestration (analyse + run)
│   ├── crawler.py          ← BFS page discovery, asset extraction, HTML rewriting
│   ├── downloader.py       ← Async file downloader (dedup, resume, blacklist)
│   ├── robots.py           ← robots.txt fetching and URL permission checking
│   ├── ratelimiter.py      ← Per-domain politeness delays
│   └── exporter.py         ← ZIP export, broken link report
├── ui/
│   ├── main_window.py      ← MainWindow (PyQt6)
│   ├── queue_panel.py      ← Download queue widget
│   ├── settings.py         ← AppSettings (QSettings) + SettingsDialog
│   ├── worker.py           ← AsyncWorker (QThread bridge)
│   └── theme.py            ← Light/dark palettes
├── utils/
│   ├── helpers.py          ← URL, filename, content-type utilities
│   └── css_parser.py       ← CSS url() extraction and rewriting
└── tests/                  ← 307 tests (pytest + pytest-asyncio)
assets/
├── icon.svg                ← Source icon (vector)
└── icon.ico                ← Multi-size ICO for Windows / PyInstaller
main.py                     ← Entry point (GUI ↔ CLI dispatch)
pyproject.toml              ← PEP 621 metadata + optional deps + tool config
website_replicator.spec     ← PyInstaller build spec
README.md
```

---

## Running tests

```bash
pip install pytest pytest-asyncio
python -m pytest -v
```

**307 tests** — unit tests for all pure logic modules, integration tests with a
real in-process aiohttp HTTP server, and regression tests for every bug fix.

---

## Building a standalone executable

```bash
pip install pyinstaller cairosvg pillow
pyinstaller website_replicator.spec
# Output: dist/WebsiteReplicator/WebsiteReplicator.exe (Windows)
# The icon (assets/icon.ico) is embedded automatically.
```

---

## Architecture notes

**Core is framework-agnostic.** `core/` has zero Qt imports. All communication
between the async replication engine and the outside world (GUI or CLI) goes
through plain Python callbacks (`log`, `progress`, `status`). This means:

- The same `Replicator` class powers both the GUI and the CLI
- Unit tests run without any Qt installation
- Adding a new frontend (web UI, Textual TUI, etc.) only requires wiring callbacks

**Single event loop per job.** Each replication job runs in its own
`asyncio` event loop on a `QThread` (GUI) or the main thread (CLI via
`asyncio.run()`). The semaphore and rate limiter are created fresh per job,
always bound to the correct loop.

**Deduplication is job-scoped.** The `asset_map` (URL → local path) is reset
at the start of each job but shared across all pages in a multi-page crawl.
Assets shared between pages are downloaded exactly once. A
`.replicator_manifest.json` is written on completion so the next run can resume.

---

## Known limitations

- JavaScript-rendered content is not executed — only static HTML is parsed
- Single-page applications (SPAs) may produce incomplete results
- Authentication (login walls, cookies) is not supported in this version
- `<base href>` tags are not yet handled
