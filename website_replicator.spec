# -*- mode: python ; coding: utf-8 -*-
"""
website_replicator.spec
PyInstaller build spec for Website Replicator v7.

Build commands:
    Windows:  pyinstaller website_replicator.spec
    macOS:    pyinstaller website_replicator.spec
    Linux:    pyinstaller website_replicator.spec --onefile

Output:
    dist/WebsiteReplicator/          (one-folder — fast startup)
    dist/WebsiteReplicator.exe       (Windows executable)

Notes:
    - GUI mode: run with no arguments
    - CLI mode: run with --url / --help flags
    - All hidden imports for aiohttp, validators, bs4 are listed explicitly
      because PyInstaller can't detect them through dynamic imports
"""

import sys
from pathlib import Path

block_cipher = None

# ── Paths ────────────────────────────────────────────────────────────────────
ROOT    = Path(SPECPATH)
SRC     = ROOT / "website_replicator"


# ── Analysis ─────────────────────────────────────────────────────────────────
a = Analysis(
    [str(ROOT / "main.py")],
    pathex        = [str(ROOT)],
    binaries      = [],
    datas         = [
        # Include the whole package so __init__.py etc. are present
        (str(SRC), "website_replicator"),
        # Assets (icon, etc.)
        (str(ROOT / "assets"), "assets"),
    ],
    hiddenimports = [
        # aiohttp internals
        "aiohttp",
        "aiohttp.web",
        "aiohttp.connector",
        "aiohttp.client",
        "aiohttp.streams",
        "aiohttp.payload",
        "aiohttp.http",
        "aiohttp.web_protocol",
        "aiohttp.resolver",
        "aiohttp._websocket",
        # HTML parsing
        "bs4",
        "bs4.builder",
        "bs4.builder._html5lib",
        "bs4.builder._htmlparser",
        "bs4.builder._lxml",
        "html.parser",
        # URL handling
        "validators",
        "validators.url",
        # CSS parsing
        "cssutils",
        "cssutils.css",
        "cssutils.parse",
        "cssutils.serialize",
        # Stdlib
        "urllib.robotparser",
        "fnmatch",
        "zipfile",
        "json",
        "http.server",
        "socketserver",
        "webbrowser",
        "asyncio",
        "asyncio.events",
        "asyncio.tasks",
        "asyncio.streams",
        # PyQt6 — include all submodules to avoid missing platform plugin errors
        "PyQt6",
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
        "PyQt6.QtNetwork",
        "PyQt6.sip",
    ],
    hookspath      = [],
    hooksconfig    = {},
    runtime_hooks  = [],
    excludes       = [
        # Exclude things we definitely don't use to reduce bundle size
        "tkinter",
        "matplotlib",
        "numpy",
        "pandas",
        "scipy",
        "PIL",
        "pytest",
        "IPython",
    ],
    win_no_prefer_redirects = False,
    win_private_assemblies  = False,
    cipher         = block_cipher,
    noarchive      = False,
)

# ── PYZ archive ───────────────────────────────────────────────────────────────
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── One-folder EXE ────────────────────────────────────────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries = True,
    name             = "WebsiteReplicator",
    debug            = False,
    bootloader_ignore_signals = False,
    strip            = False,
    upx              = True,        # compress with UPX if available
    console          = False,       # no console window in GUI mode
                                    # CLI output still works via stdout
    disable_windowed_traceback = False,
    target_arch      = None,
    codesign_identity = None,
    entitlements_file = None,
    icon             = str(ROOT / "assets" / "icon.ico"),
)

# ── Collect ───────────────────────────────────────────────────────────────────
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip            = False,
    upx              = True,
    upx_exclude      = [],
    name             = "WebsiteReplicator",
)
