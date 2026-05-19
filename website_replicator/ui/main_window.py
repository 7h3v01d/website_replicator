"""
ui/main_window.py
MainWindow — pure presentation layer.

Responsibilities:
  - Build and style all widgets
  - Translate user actions into core calls (via AsyncWorker)
  - Translate core callbacks/signals into widget updates
  - Manage system tray, theme toggle, queue lifecycle

Not responsible for:
  - Any download, parsing, or crawl logic  (→ core/)
  - Persistent settings storage           (→ ui/settings.py)
  - Colour definitions                    (→ ui/theme.py)
"""

from __future__ import annotations

import http.server
import os
import socketserver
import threading
import webbrowser
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import QEvent, QTimer, Qt
from PyQt6.QtGui import QAction, QFont, QIcon
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QMainWindow, QMenu,
    QMessageBox, QProgressBar, QPushButton, QSplitter,
    QStatusBar, QSystemTrayIcon, QTabWidget, QTextEdit,
    QVBoxLayout, QWidget, QDialog,
)

from ..core.exporter import BrokenLinkReport, zip_output_dir
from ..core.models import AnalysisResult, QueueItem, ReplicatorConfig
from ..core.replicator import Replicator, analyse
from ..utils.helpers import normalise_url, log_line
from .queue_panel import QueuePanel
from .settings import AppSettings, SettingsDialog
from .theme import (
    THEMES, cancel_btn_sheet, line_edit_sheet,
    primary_btn_sheet, progress_bar_sheet,
    secondary_btn_sheet, text_area_sheet, window_sheet,
)
from .worker import AsyncWorker, WorkerSignals

from .. import VERSION  # single source of truth in package __init__


# ---------------------------------------------------------------------------
# MainWindow
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app

        # ── State ─────────────────────────────────────────────────────────────
        self._cfg             = AppSettings()
        self._dark            = self._cfg.get("dark_mode")
        self._signals         = WorkerSignals()
        self._worker:  Optional[AsyncWorker]    = None
        self._rep:     Optional[Replicator]     = None
        self._server:  Optional[tuple]          = None   # (httpd|None, thread)
        self._is_busy         = False
        self._last_analysis:  Optional[AnalysisResult] = None
        self._current_item:   Optional[QueueItem]      = None

        self.setWindowTitle(f"Website Replicator v{VERSION}")
        self.setMinimumSize(900, 800)

        self._build_ui()
        self._build_tray()
        self._connect_signals()
        self._apply_theme()
        self._set_window_icon()

    # =========================================================================
    # Theme
    # =========================================================================

    @property
    def _theme(self) -> dict:
        return THEMES["dark"] if self._dark else THEMES["light"]

    def _set_window_icon(self) -> None:
        """Load icon from assets/ next to main.py, fall back gracefully."""
        import pathlib
        # Look for icon.ico relative to the package root
        candidates = [
            pathlib.Path(__file__).parent.parent.parent / "assets" / "icon.ico",
            pathlib.Path(__file__).parent.parent.parent / "assets" / "icon.png",
        ]
        for path in candidates:
            if path.exists():
                icon = QIcon(str(path))
                self.setWindowIcon(icon)
                self._app.setWindowIcon(icon)
                # Also update tray icon if available
                if hasattr(self, "_tray") and not icon.isNull():
                    self._tray.setIcon(icon)
                return
        # No icon file found — use Qt built-in fallback (already set in _build_tray)

    def _toggle_theme(self) -> None:
        self._dark = not self._dark
        self._cfg.set("dark_mode", self._dark)
        self._btn_theme.setText("☀ Light" if self._dark else "🌙 Dark")
        self._apply_theme()

    def _apply_theme(self) -> None:
        t = self._theme
        self.setStyleSheet(window_sheet(t))

        self._url_entry.setStyleSheet(line_edit_sheet(t))
        self._analysis_text.setStyleSheet(text_area_sheet(t))
        self._progress_text.setStyleSheet(text_area_sheet(t))
        self._progress_bar.setStyleSheet(progress_bar_sheet(t))
        self._passthru_check.setStyleSheet(f"color: {t['text']};")
        self._output_label.setStyleSheet(f"color: {t['text_muted']}; font-size: 10px;")
        self._status_label.setStyleSheet(f"color: {t['text_muted']};")

        for btn in self._primary_btns:
            btn.setStyleSheet(primary_btn_sheet(t))
        self._btn_cancel.setStyleSheet(cancel_btn_sheet(t))
        self._btn_theme.setStyleSheet(secondary_btn_sheet(t))
        self._btn_settings.setStyleSheet(secondary_btn_sheet(t))
        self._btn_folder.setStyleSheet(secondary_btn_sheet(t))

        self._queue_panel.theme = t
        self._queue_panel.restyle()

    # =========================================================================
    # UI construction
    # =========================================================================

    def _make_primary_btn(self, text: str, slot, enabled: bool = True) -> QPushButton:
        btn = QPushButton(text)
        btn.setFont(QFont("Helvetica", 11))
        btn.setEnabled(enabled)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(slot)
        self._primary_btns.append(btn)
        return btn

    def _make_secondary_btn(self, text: str, slot, enabled: bool = True) -> QPushButton:
        btn = QPushButton(text)
        btn.setFont(QFont("Helvetica", 10))
        btn.setEnabled(enabled)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(slot)
        return btn

    def _lbl(self, text: str, bold: bool = False) -> QLabel:
        lbl = QLabel(text)
        weight = QFont.Weight.Bold if bold else QFont.Weight.Normal
        lbl.setFont(QFont("Helvetica", 11, weight))
        return lbl

    def _build_ui(self) -> None:
        self._primary_btns: list[QPushButton] = []

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)

        # ── Left panel ────────────────────────────────────────────────────────
        left = QWidget()
        ll   = QVBoxLayout(left)
        ll.setContentsMargins(20, 20, 12, 20)
        ll.setSpacing(10)

        # Title row
        title_row = QHBoxLayout()
        title_lbl = QLabel(f"Website Replicator v{VERSION}")
        title_lbl.setFont(QFont("Helvetica", 15, QFont.Weight.Bold))
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        self._btn_theme    = self._make_secondary_btn("🌙 Dark", self._toggle_theme)
        self._btn_settings = self._make_secondary_btn("⚙ Settings", self._open_settings)
        title_row.addWidget(self._btn_theme)
        title_row.addWidget(self._btn_settings)
        ll.addLayout(title_row)

        # URL input
        ll.addWidget(self._lbl("Website URL:"))
        self._url_entry = QLineEdit()
        self._url_entry.setFont(QFont("Helvetica", 12))
        self._url_entry.setPlaceholderText("https://example.com")
        self._url_entry.returnPressed.connect(self._start_analysis)
        ll.addWidget(self._url_entry)

        # Output folder row
        folder_row = QHBoxLayout()
        self._output_label = QLabel(f"Output: {self._cfg.get('output_dir')}")
        self._output_label.setFont(QFont("Helvetica", 10))
        folder_row.addWidget(self._output_label, 1)
        self._btn_folder = self._make_secondary_btn("📁 Choose…", self._choose_folder)
        folder_row.addWidget(self._btn_folder)
        ll.addLayout(folder_row)

        # Options row
        opts = QHBoxLayout()
        self._passthru_check = QCheckBox("Passthru images (keep online URLs)")
        self._passthru_check.setChecked(self._cfg.get("passthru"))
        self._passthru_check.setFont(QFont("Helvetica", 10))
        opts.addWidget(self._passthru_check)
        opts.addStretch()
        ll.addLayout(opts)

        # Action buttons — row 1
        row1 = QHBoxLayout()
        self._btn_analyze   = self._make_primary_btn("🔍 Analyze",       self._start_analysis)
        self._btn_replicate = self._make_primary_btn("⬇ Replicate",     self._enqueue, enabled=False)
        self._btn_export    = self._make_primary_btn("📄 Export Report", self._export_report, enabled=False)
        row1.addWidget(self._btn_analyze)
        row1.addWidget(self._btn_replicate)
        row1.addWidget(self._btn_export)
        ll.addLayout(row1)

        # Action buttons — row 2
        row2 = QHBoxLayout()
        self._btn_preview = self._make_primary_btn("🌐 Preview Site",   self._preview_site,   enabled=False)
        self._btn_zip     = self._make_primary_btn("📦 Export ZIP",     self._export_zip,     enabled=False)
        self._btn_brlinks = self._make_primary_btn("🔗 Broken Links",   self._show_broken_links, enabled=False)
        self._btn_cancel  = QPushButton("✕ Cancel")
        self._btn_cancel.setFont(QFont("Helvetica", 11))
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_cancel.clicked.connect(self._cancel_current)
        row2.addWidget(self._btn_preview)
        row2.addWidget(self._btn_zip)
        row2.addWidget(self._btn_brlinks)
        row2.addWidget(self._btn_cancel)
        ll.addLayout(row2)

        # Progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setFixedHeight(18)
        self._progress_bar.setTextVisible(True)
        ll.addWidget(self._progress_bar)

        # Log tabs
        self._tabs = QTabWidget()
        self._analysis_text = QTextEdit()
        self._analysis_text.setReadOnly(True)
        self._analysis_text.setFont(QFont("Courier New", 9))
        self._progress_text = QTextEdit()
        self._progress_text.setReadOnly(True)
        self._progress_text.setFont(QFont("Courier New", 9))
        self._tabs.addTab(self._analysis_text, "Analysis")
        self._tabs.addTab(self._progress_text, "Progress")
        ll.addWidget(self._tabs, 1)

        # Status label
        self._status_label = QLabel("Ready")
        self._status_label.setFont(QFont("Helvetica", 10))
        ll.addWidget(self._status_label)

        splitter.addWidget(left)

        # ── Right panel (queue) ───────────────────────────────────────────────
        right = QWidget()
        rl    = QVBoxLayout(right)
        rl.setContentsMargins(12, 20, 20, 20)
        self._queue_panel = QueuePanel(self._theme)
        rl.addWidget(self._queue_panel)
        splitter.addWidget(right)

        splitter.setSizes([640, 260])
        splitter.setCollapsible(1, True)

        # Status bar
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._sb_right = QLabel(f"v{VERSION}")
        sb.addPermanentWidget(self._sb_right)

    # =========================================================================
    # System tray
    # =========================================================================

    def _build_tray(self) -> None:
        icon = self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon)
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip(f"Website Replicator v{VERSION}")

        menu = QMenu()
        act_show = QAction("Show", self)
        act_show.triggered.connect(self._show_from_tray)
        act_quit = QAction("Quit", self)
        act_quit.triggered.connect(self._quit_app)
        menu.addAction(act_show)
        menu.addSeparator()
        menu.addAction(act_quit)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._tray_activated)
        self._tray.show()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_from_tray()

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.activateWindow()

    def _quit_app(self) -> None:
        self._tray.hide()
        QApplication.quit()

    def changeEvent(self, event: QEvent) -> None:
        if (
            event.type() == QEvent.Type.WindowStateChange
            and self.isMinimized()
            and self._cfg.get("min_to_tray")
        ):
            self.hide()
            self._tray.showMessage(
                "Website Replicator",
                "Running in the background — double-click to restore.",
                QSystemTrayIcon.MessageIcon.Information, 2000,
            )
        super().changeEvent(event)

    # =========================================================================
    # Signal wiring — core callbacks → Qt signals → UI slots
    # =========================================================================

    def _connect_signals(self) -> None:
        s = self._signals
        s.log_analysis.connect(self._analysis_text.append)
        s.log_progress.connect(self._progress_text.append)
        s.status.connect(self._set_status)
        s.progress_val.connect(self._progress_bar.setValue)
        s.analysis_done.connect(self._on_analysis_done)
        s.replication_done.connect(self._on_replication_done)

    # ── Callback adapters (called from worker thread, emit signals) ────────────

    def _cb_log_a(self, msg: str) -> None:
        self._signals.log_analysis.emit(log_line(msg))

    def _cb_log_p(self, msg: str) -> None:
        """
        Forward a progress log line to the UI.
        In non-verbose mode, suppress noisy per-asset lines so the pane
        stays readable.  Always forward: page headers, success/failure
        markers, dedup hits, resume hits, and any error lines.
        """
        verbose = self._cfg.get("verbose_log")
        if not verbose:
            # Keep lines that are genuinely informative at a glance
            first_word = msg.lstrip().split()[0] if msg.strip() else ""
            keep_prefixes = ("✓", "✗", "⚡", "↩", "⛔", "Page", "↻",
                             "Saved", "ZIP", "Manifest", "Broken", "No broken",
                             "robots", "Crawl", "Error", "Fetching", "Processing",
                             "Replication", "Discovered", "Resume")
            if not any(msg.lstrip().startswith(p) for p in keep_prefixes):
                return   # suppress verbose per-asset chatter
        self._signals.log_progress.emit(log_line(msg))

    def _cb_status(self, text: str, colour: str) -> None:
        self._signals.status.emit(text, colour)
        # Echo significant status changes to the progress log so they
        # appear even when the user is watching the Analysis tab.
        # Per-page "Page N/M" lines are frequent — only log them in verbose mode.
        is_per_page = text.startswith("Page ")
        verbose     = self._cfg.get("verbose_log")
        if not is_per_page or verbose:
            self._signals.log_progress.emit(log_line(f"→ {text}"))

    def _cb_progress(self, pct: int) -> None:
        self._signals.progress_val.emit(pct)

    # =========================================================================
    # UI helpers
    # =========================================================================

    def _ts(self) -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _set_status(self, text: str, colour: str) -> None:
        self._status_label.setText(text)
        self._status_label.setStyleSheet(f"color: {colour};")
        self._sb_right.setText(f"{text}  ·  v{VERSION}")

    # =========================================================================
    # Actions
    # =========================================================================

    # ── Folder picker ─────────────────────────────────────────────────────────

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose Output Folder", self._cfg.get("output_dir")
        )
        if folder:
            self._cfg.set("output_dir", folder)
            self._output_label.setText(f"Output: {folder}")

    # ── Settings dialog ───────────────────────────────────────────────────────

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self._cfg, self._theme, self)
        dlg.exec()   # settings are saved inside the dialog on OK

    # ── Analysis ──────────────────────────────────────────────────────────────

    def _start_analysis(self) -> None:
        if self._is_busy:
            return

        raw = self._url_entry.text().strip()
        ok, url = normalise_url(raw)
        if not ok:
            QMessageBox.critical(self, "Invalid URL", f"'{raw}' is not a valid URL.")
            return

        self._is_busy = True
        self._btn_analyze.setEnabled(False)
        self._btn_replicate.setEnabled(False)
        self._btn_export.setEnabled(False)
        self._analysis_text.clear()
        self._progress_bar.setValue(0)
        self._tabs.setCurrentIndex(0)
        self._set_status("Analyzing…", self._theme["info"])

        cfg = self._cfg.to_config()

        async def _run():
            result = await analyse(
                url    = url,
                cfg    = cfg,
                log    = self._cb_log_a,
                status = self._cb_status,
            )
            self._signals.analysis_done.emit(result)

        self._worker = AsyncWorker(_run())
        self._worker.start()

    def _on_analysis_done(self, result: Optional[AnalysisResult]) -> None:
        self._is_busy = False
        self._btn_analyze.setEnabled(True)
        self._last_analysis = result
        if result is not None:
            self._btn_replicate.setEnabled(True)
            self._btn_export.setEnabled(True)

    # ── Queue / replication ───────────────────────────────────────────────────

    def _enqueue(self) -> None:
        if not self._last_analysis:
            return
        cfg = self._cfg.to_config()
        item = QueueItem(
            url         = self._last_analysis.url,
            passthru    = self._passthru_check.isChecked(),
            output_dir  = self._cfg.get("output_dir"),
            crawl_depth = cfg.crawl_depth,
        )
        self._queue_panel.add_item(item)
        self._set_status(f"Queued: {item.url}", self._theme["text_muted"])
        self._process_queue()

    def _process_queue(self) -> None:
        if self._is_busy or self._queue_panel.active_item():
            return
        pending = self._queue_panel.pending_items()
        if not pending:
            return

        item = pending[0]
        item.status = QueueItem.ACTIVE
        self._queue_panel.update_item(item)
        self._current_item = item

        self._is_busy = True
        self._btn_cancel.setEnabled(True)
        self._btn_replicate.setEnabled(False)
        self._progress_text.clear()
        self._progress_bar.setValue(0)
        self._tabs.setCurrentIndex(1)
        self._set_status(f"Replicating: {item.url}", self._theme["info"])

        cfg = self._cfg.to_config()
        self._rep = Replicator(
            cfg      = cfg,
            log      = self._cb_log_p,
            progress = self._cb_progress,
            status   = self._cb_status,
        )

        analysis = self._last_analysis if (
            self._last_analysis and self._last_analysis.url == item.url
        ) else None

        # Wire page-progress callbacks so queue item stays up-to-date
        _item_ref = item  # capture for closures

        def _on_pages_counted(total: int) -> None:
            _item_ref.pages_total = total
            self._queue_panel.update_item(_item_ref)

        def _on_page_done(done: int, total: int) -> None:
            _item_ref.pages_done  = done
            _item_ref.pages_total = total
            self._queue_panel.update_item(_item_ref)

        self._rep.set_page_callbacks(_on_pages_counted, _on_page_done)

        async def _run():
            ok = await self._rep.run(
                url         = item.url,
                analysis    = analysis,
                passthru    = item.passthru,
                output_dir  = item.output_dir,
                crawl_depth = item.crawl_depth,
            )
            self._signals.replication_done.emit(ok)

        self._worker = AsyncWorker(_run())
        self._worker.start()

    def _on_replication_done(self, success: bool) -> None:
        self._is_busy = False
        self._btn_cancel.setEnabled(False)
        self._progress_bar.setValue(0)

        if self._current_item:
            cancelled = self._rep and self._rep._cancelled
            self._current_item.status = (
                QueueItem.DONE      if success   else
                QueueItem.CANCELLED if cancelled else
                QueueItem.ERROR
            )
            self._queue_panel.update_item(self._current_item)

        if success:
            self._btn_preview.setEnabled(True)
            self._btn_zip.setEnabled(True)
            self._btn_brlinks.setEnabled(True)
            if self._cfg.get("min_to_tray") and not self.isVisible():
                output = self._current_item.output_dir if self._current_item else "output"
                self._tray.showMessage(
                    "Replication Complete",
                    f"Saved to {output}",
                    QSystemTrayIcon.MessageIcon.Information, 3000,
                )

        self._btn_replicate.setEnabled(self._last_analysis is not None)
        # Give the event loop a moment before picking the next job
        QTimer.singleShot(400, self._process_queue)

    def _cancel_current(self) -> None:
        if self._rep:
            self._rep.cancel()
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
        self._set_status("Cancelling…", self._theme["warning"])
        self._btn_cancel.setEnabled(False)

    # ── Export report ─────────────────────────────────────────────────────────

    def _export_report(self) -> None:
        if not self._last_analysis:
            QMessageBox.warning(self, "No Data", "Run an analysis first.")
            return
        default_name = f"analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Report", default_name, "Text Files (*.txt)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(f"Website Analysis Report\nURL: {self._last_analysis.url}\n")
                fh.write("=" * 50 + "\n\n")
                for line in self._last_analysis.summary_lines():
                    fh.write(line + "\n")
            QMessageBox.information(self, "Saved", f"Report saved to:\n{path}")
            self._progress_text.append(log_line(f"Exported report → {path}"))
        except OSError as exc:
            QMessageBox.critical(self, "Error", f"Could not save report:\n{exc}")

    # ── ZIP export ───────────────────────────────────────────────────────────

    def _export_zip(self) -> None:
        output_dir = self._cfg.get("output_dir")
        if not os.path.exists(os.path.join(output_dir, "index.html")):
            QMessageBox.warning(self, "Not Found",
                                "No replicated site found — run a replication first.")
            return
        _basename  = os.path.basename(output_dir.rstrip("/\\"))
        _timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default = os.path.join(
            os.path.dirname(os.path.abspath(output_dir)),
            f"{_basename}_{_timestamp}.zip"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Save ZIP Archive", default, "ZIP Archives (*.zip)"
        )
        if not path:
            return
        try:
            self._set_status("Creating ZIP…", self._theme["info"])
            QApplication.processEvents()
            result = zip_output_dir(output_dir, path)
            size_mb = os.path.getsize(result) / 1_048_576
            self._progress_text.append(
                log_line(f"📦 ZIP export: {result} ({size_mb:.1f} MB)")
            )
            self._set_status(f"ZIP saved — {size_mb:.1f} MB", self._theme["success"])
            QMessageBox.information(self, "Export Complete",
                                    f"Archive saved to:\n{result}\n({size_mb:.1f} MB)")
        except Exception as exc:
            QMessageBox.critical(self, "Export Failed", str(exc))
            self._set_status("ZIP export failed", self._theme["error"])

    # ── Broken link report ───────────────────────────────────────────────────

    def _show_broken_links(self) -> None:
        blr = self._rep.broken_link_report if self._rep else None
        if blr is None:
            QMessageBox.information(self, "Broken Links",
                                    "No broken link data available.\nRun a replication first.")
            return

        # Show in a scrollable dialog
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle("Broken Link Report")
        dlg.setMinimumSize(640, 480)
        dlg.setStyleSheet(
            f"QDialog {{ background: {self._theme['bg']}; }}"
            f"QTextEdit {{ background: {self._theme['bg_widget']}; color: {self._theme['text']};"
            f" border: 1px solid {self._theme['border']}; border-radius: 4px; }}"
        )
        layout = QVBoxLayout(dlg)
        ta = QTextEdit()
        ta.setReadOnly(True)
        ta.setFont(QFont("Courier New", 9))
        ta.setPlainText(blr.as_text())
        layout.addWidget(ta)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        save_btn = btns.addButton("Save to File…", QDialogButtonBox.ButtonRole.ActionRole)

        def _save():
            path, _ = QFileDialog.getSaveFileName(
                dlg, "Save Broken Link Report",
                f"broken_links_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
                "Text Files (*.txt)"
            )
            if path:
                blr.write(path)
                QMessageBox.information(dlg, "Saved", f"Report saved to:\n{path}")

        save_btn.clicked.connect(_save)
        btns.accepted.connect(dlg.accept)
        layout.addWidget(btns)
        dlg.exec()

    # ── Preview server ────────────────────────────────────────────────────────

    def _preview_site(self) -> None:
        output_dir = self._cfg.get("output_dir")
        index      = os.path.join(output_dir, "index.html")

        if not os.path.exists(index):
            QMessageBox.warning(self, "Not Found",
                                "No replicated site found — run a replication first.")
            return

        port = self._cfg.get("server_port")

        # Re-use running server if alive
        if self._server and self._server[1].is_alive():
            webbrowser.open(f"http://localhost:{port}/index.html")
            return

        log_fn = self._progress_text.append
        ts     = self._ts

        _serve_root = os.path.realpath(output_dir)

        def _run_server() -> None:
            # Serve from a handler rooted at output_dir — no os.chdir() so
            # we don't move the process working directory globally.
            class Handler(http.server.SimpleHTTPRequestHandler):
                # Python 3.7+ BaseHTTPRequestHandler supports directory=
                def __init__(self_, *args, **kwargs):
                    super().__init__(*args, directory=_serve_root, **kwargs)

                def log_message(self_, fmt, *args):
                    log_fn(f"[{ts()}] Server: {fmt % args}")

                def do_GET(self_):
                    # Block dynamic file types
                    if self_.path.split("?")[0].endswith((".php", ".asp", ".aspx")):
                        self_.send_error(404, "Dynamic files not supported")
                        return
                    # Redirect legacy font paths
                    if self_.path.startswith(("/lib/fonts/", "/css/fonts/")):
                        self_.path = "/fonts/" + os.path.basename(
                            self_.path.split("?")[0]
                        )
                    # Directory traversal guard: resolved path must stay inside root
                    rel = self_.path.lstrip("/")
                    abs_path = os.path.realpath(os.path.join(_serve_root, rel))
                    if not abs_path.startswith(_serve_root):
                        self_.send_error(403, "Forbidden")
                        return
                    try:
                        super().do_GET()
                    except Exception:
                        pass

                extensions_map = {
                    ".jpg": "image/jpeg",  ".png": "image/png",
                    ".gif": "image/gif",   ".webp": "image/webp",
                    ".svg": "image/svg+xml", ".css": "text/css",
                    ".js":  "application/javascript",
                    ".woff": "font/woff",  ".woff2": "font/woff2",
                    ".ttf": "font/ttf",    ".webmanifest": "application/manifest+json",
                    ".htm": "text/html",   ".ico": "image/x-icon",
                    "": "application/octet-stream",
                }

            class Server(socketserver.TCPServer):
                allow_reuse_address = True

            try:
                with Server(("", port), Handler) as httpd:
                    log_fn(f"[{ts()}] Serving at http://localhost:{port}")
                    webbrowser.open(f"http://localhost:{port}/index.html")
                    httpd.serve_forever()
            except Exception as exc:
                log_fn(f"[{ts()}] Server error: {exc}")

        t = threading.Thread(target=_run_server, daemon=True)
        t.start()
        self._server = (None, t)
        self._progress_text.append(log_line(f"Preview server started on port {port}"))

    # =========================================================================
    # Window lifecycle
    # =========================================================================

    def closeEvent(self, event: QEvent) -> None:
        if self._rep:
            self._rep.cancel()
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(2000)
        self._tray.hide()
        event.accept()
