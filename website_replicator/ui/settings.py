"""
ui/settings.py
QSettings-backed persistence wrapper around ReplicatorConfig,
and the SettingsDialog that edits it.
"""

from __future__ import annotations

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QGridLayout, QGroupBox, QLabel, QLineEdit, QSpinBox, QVBoxLayout,
)

from ..core.models import ReplicatorConfig


# ---------------------------------------------------------------------------
# AppSettings — persists ReplicatorConfig fields via QSettings
# ---------------------------------------------------------------------------

class AppSettings:
    """
    Thin QSettings wrapper.  All values are typed and have safe defaults.
    Also owns the UI-only settings (dark_mode, min_to_tray) that don't
    belong in ReplicatorConfig.
    """

    _DEFAULTS: dict[str, object] = {
        # ReplicatorConfig fields
        "max_retries":   5,
        "timeout":       20,
        "user_agent":    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "server_port":   8000,
        "crawl_depth":   0,
        "max_concurrent": 6,
        "output_dir":    "replicated_website",
        "passthru":      False,
        "resume":        True,     # skip already-downloaded assets
        "domain_delay":  0.0,
        "blacklist":     "*.mp4,*.mp3,*.avi,*.mov,*.mkv,*.wmv,*.flv,*.webm,*.ogg,*.wav,*.aac,*.m4a,*.zip,*.tar,*.gz,*.rar",
        # UI-only
        "dark_mode":     False,
        "min_to_tray":   False,
        "verbose_log":   False,     # show per-asset lines in progress pane
    }

    def __init__(self) -> None:
        self._q = QSettings("KeystoneAI", "WebsiteReplicator")

    # ── typed get/set ─────────────────────────────────────────────────────────

    def get(self, key: str) -> object:
        default = self._DEFAULTS[key]
        val     = self._q.value(key, default)
        # QSettings serialises bools as strings on some platforms
        if isinstance(default, bool):
            return val.lower() == "true" if isinstance(val, str) else bool(val)
        if isinstance(default, int):
            return int(val)
        if isinstance(default, float):
            return float(val)
        return val

    def set(self, key: str, value: object) -> None:
        self._q.setValue(key, value)

    # ── convenience: build a ReplicatorConfig from current settings ───────────

    def to_config(self) -> ReplicatorConfig:
        return ReplicatorConfig(
            max_retries    = self.get("max_retries"),
            timeout        = self.get("timeout"),
            user_agent     = self.get("user_agent"),
            server_port    = self.get("server_port"),
            crawl_depth    = self.get("crawl_depth"),
            max_concurrent = self.get("max_concurrent"),
            output_dir     = self.get("output_dir"),
            passthru       = self.get("passthru"),
            resume         = self.get("resume"),
            domain_delay   = self.get("domain_delay"),
            blacklist      = tuple(p.strip() for p in str(self.get("blacklist")).split(",") if p.strip()),
        )

    def save_config(self, cfg: ReplicatorConfig) -> None:
        """Write all ReplicatorConfig fields back to QSettings."""
        self.set("max_retries",    cfg.max_retries)
        self.set("timeout",        cfg.timeout)
        self.set("user_agent",     cfg.user_agent)
        self.set("server_port",    cfg.server_port)
        self.set("crawl_depth",    cfg.crawl_depth)
        self.set("max_concurrent", cfg.max_concurrent)
        self.set("output_dir",     cfg.output_dir)
        self.set("passthru",       cfg.passthru)
        self.set("resume",         cfg.resume)
        self.set("domain_delay",   cfg.domain_delay)
        self.set("blacklist",      ",".join(cfg.blacklist))


# ---------------------------------------------------------------------------
# SettingsDialog
# ---------------------------------------------------------------------------

class SettingsDialog(QDialog):
    """
    Modal dialog for editing all tuneable parameters.
    Reads from AppSettings on open, writes back on OK.
    """

    def __init__(self, settings: AppSettings, theme: dict, parent=None) -> None:
        super().__init__(parent)
        self._cfg   = settings
        self._theme = theme
        self.setWindowTitle("Settings")
        self.setMinimumWidth(460)
        self._build()

    def _build(self) -> None:
        t = self._theme
        self.setStyleSheet(
            f"QDialog   {{ background: {t['bg']}; color: {t['text']}; }}"
            f"QLabel     {{ color: {t['text']}; }}"
            f"QGroupBox  {{ color: {t['text']}; border: 1px solid {t['border']};"
            f"             border-radius: 6px; margin-top: 10px; padding-top: 10px; }}"
            f"QGroupBox::title {{ subcontrol-origin: margin; left: 8px; }}"
            f"QSpinBox, QLineEdit, QComboBox {{"
            f"  background: {t['bg_widget']}; color: {t['text']};"
            f"  border: 1px solid {t['border']}; border-radius: 4px; padding: 4px; }}"
            f"QCheckBox  {{ color: {t['text']}; }}"
            f"QPushButton {{ background: {t['blue']}; color: white; border: none;"
            f"              border-radius: 4px; padding: 6px 16px; }}"
            f"QPushButton:hover {{ background: {t['blue_hover']}; }}"
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # ── Network ──────────────────────────────────────────────────────────
        net = QGroupBox("Network")
        ng  = QGridLayout(net)

        ng.addWidget(QLabel("Max Retries:"), 0, 0)
        self._retries = QSpinBox()
        self._retries.setRange(0, 20)
        self._retries.setValue(self._cfg.get("max_retries"))
        ng.addWidget(self._retries, 0, 1)

        ng.addWidget(QLabel("Timeout (s):"), 1, 0)
        self._timeout = QSpinBox()
        self._timeout.setRange(5, 120)
        self._timeout.setValue(self._cfg.get("timeout"))
        ng.addWidget(self._timeout, 1, 1)

        ng.addWidget(QLabel("Max Concurrent Downloads:"), 2, 0)
        self._concurrent = QSpinBox()
        self._concurrent.setRange(1, 20)
        self._concurrent.setValue(self._cfg.get("max_concurrent"))
        self._concurrent.setToolTip(
            "Max simultaneous HTTP connections per job.\n"
            "Higher = faster but more likely to trigger rate limiting."
        )
        ng.addWidget(self._concurrent, 2, 1)

        ng.addWidget(QLabel("Preview Server Port:"), 3, 0)
        self._port = QSpinBox()
        self._port.setRange(1024, 65535)
        self._port.setValue(self._cfg.get("server_port"))
        ng.addWidget(self._port, 3, 1)

        ng.addWidget(QLabel("User Agent:"), 4, 0)
        self._ua = QLineEdit(self._cfg.get("user_agent"))
        ng.addWidget(self._ua, 4, 1)

        layout.addWidget(net)

        # ── Crawl ────────────────────────────────────────────────────────────
        crawl = QGroupBox("Crawl")
        cg    = QGridLayout(crawl)

        cg.addWidget(QLabel("Crawl Depth:"), 0, 0)
        self._depth = QSpinBox()
        self._depth.setRange(0, 5)
        self._depth.setValue(self._cfg.get("crawl_depth"))
        self._depth.setToolTip(
            "0 = homepage only\n"
            "1 = homepage + one level of internal links\n"
            "2–5 = deeper crawl (can be very slow on large sites)"
        )
        cg.addWidget(self._depth, 0, 1)

        hint = QLabel("0 = homepage only  ·  1–5 = follow internal links N levels deep")
        hint.setStyleSheet(f"color: {self._theme['text_muted']}; font-size: 10px;")
        cg.addWidget(hint, 1, 0, 1, 2)

        layout.addWidget(crawl)

        # ── Politeness ───────────────────────────────────────────────────────
        pol = QGroupBox("Politeness")
        pg  = QGridLayout(pol)

        pg.addWidget(QLabel("Domain Delay (s):"), 0, 0)
        self._domain_delay = QDoubleSpinBox()
        self._domain_delay.setRange(0.0, 30.0)
        self._domain_delay.setSingleStep(0.5)
        self._domain_delay.setDecimals(1)
        self._domain_delay.setValue(float(self._cfg.get("domain_delay")))
        self._domain_delay.setToolTip(
            "Minimum seconds between requests to the same domain.\n"
            "0 = no throttling.  1-3 = polite crawling.\n"
            "robots.txt Crawl-delay is always respected as a floor."
        )
        pg.addWidget(self._domain_delay, 0, 1)

        pg.addWidget(QLabel("Asset Blacklist:"), 1, 0)
        self._blacklist = QLineEdit(str(self._cfg.get("blacklist")))
        self._blacklist.setToolTip(
            "Comma-separated glob patterns. Matching URLs are skipped.\n"
            "Example: *.mp4,*.mp3,*.zip,*/ads/*"
        )
        pg.addWidget(self._blacklist, 1, 1)

        bl_hint = QLabel("Comma-separated globs — matching URLs are never downloaded")
        bl_hint.setStyleSheet(f"color: {self._theme['text_muted']}; font-size: 10px;")
        pg.addWidget(bl_hint, 2, 0, 1, 2)

        layout.addWidget(pol)

        # ── Behaviour ────────────────────────────────────────────────────────
        beh = QGroupBox("Behaviour")
        bg  = QGridLayout(beh)

        self._min_tray = QCheckBox("Minimise to system tray instead of taskbar")
        self._min_tray.setChecked(self._cfg.get("min_to_tray"))
        bg.addWidget(self._min_tray, 0, 0, 1, 2)

        self._resume = QCheckBox("Resume mode (skip already-downloaded assets)")
        self._resume.setChecked(self._cfg.get("resume"))
        self._resume.setToolTip(
            "On resume, assets found on disk from a previous run are skipped.\n"
            "The site manifest is saved to .replicator_manifest.json in the output folder."
        )
        bg.addWidget(self._resume, 1, 0, 1, 2)

        self._verbose_log = QCheckBox("Verbose progress log (show every downloaded asset)")
        self._verbose_log.setChecked(self._cfg.get("verbose_log"))
        self._verbose_log.setToolTip(
            "When enabled, every downloaded asset (CSS, JS, image, font) is\n"
            "logged in the Progress pane. Disable for cleaner output on large sites."
        )
        bg.addWidget(self._verbose_log, 2, 0, 1, 2)

        layout.addWidget(beh)

        # ── Buttons ──────────────────────────────────────────────────────────
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._save_and_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _save_and_accept(self) -> None:
        self._cfg.set("max_retries",    self._retries.value())
        self._cfg.set("timeout",        self._timeout.value())
        self._cfg.set("max_concurrent", self._concurrent.value())
        self._cfg.set("server_port",    self._port.value())
        self._cfg.set("user_agent",     self._ua.text().strip())
        self._cfg.set("crawl_depth",    self._depth.value())
        self._cfg.set("min_to_tray",    self._min_tray.isChecked())
        self._cfg.set("resume",         self._resume.isChecked())
        self._cfg.set("domain_delay",   self._domain_delay.value())
        self._cfg.set("blacklist",       self._blacklist.text().strip())
        self._cfg.set("verbose_log",     self._verbose_log.isChecked())
        self.accept()
