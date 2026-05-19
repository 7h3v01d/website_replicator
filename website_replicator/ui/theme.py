"""
ui/theme.py
Light / dark theme palettes and per-widget stylesheet generators.
Keeping all colour strings here means changing a colour is a one-line edit.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------

THEMES: dict[str, dict[str, str]] = {
    "light": {
        "bg":           "#f0f4f8",
        "bg_widget":    "#ffffff",
        "bg_panel":     "#e8edf3",
        "border":       "#cbd5e1",
        "text":         "#1e293b",
        "text_muted":   "#64748b",
        "blue":         "#2563eb",
        "blue_dark":    "#1e40af",
        "blue_hover":   "#3b82f6",
        "success":      "#16a34a",
        "warning":      "#d97706",
        "error":        "#dc2626",
        "info":         "#0284c7",
        "disabled_bg":  "#94a3b8",
        "disabled_fg":  "#e2e8f0",
        "progress_bg":  "#e2e8f0",
        "queue_active": "#dbeafe",
        "queue_done":   "#dcfce7",
        "queue_error":  "#fee2e2",
        "queue_cancel": "#f1f5f9",
    },
    "dark": {
        "bg":           "#0f172a",
        "bg_widget":    "#1e293b",
        "bg_panel":     "#162032",
        "border":       "#334155",
        "text":         "#f1f5f9",
        "text_muted":   "#94a3b8",
        "blue":         "#3b82f6",
        "blue_dark":    "#2563eb",
        "blue_hover":   "#60a5fa",
        "success":      "#22c55e",
        "warning":      "#f59e0b",
        "error":        "#ef4444",
        "info":         "#38bdf8",
        "disabled_bg":  "#334155",
        "disabled_fg":  "#64748b",
        "progress_bg":  "#1e293b",
        "queue_active": "#1e3a5f",
        "queue_done":   "#14532d",
        "queue_error":  "#450a0a",
        "queue_cancel": "#1e293b",
    },
}


# ---------------------------------------------------------------------------
# Stylesheet generators
# ---------------------------------------------------------------------------

def window_sheet(t: dict) -> str:
    return (
        f"QMainWindow, QWidget {{ background-color: {t['bg']}; color: {t['text']}; }}"
        f"QScrollArea {{ background: transparent; border: none; }}"
        f"QTabWidget::pane {{ border: 1px solid {t['border']}; border-radius: 6px; }}"
        f"QTabBar::tab {{"
        f"  background: {t['bg_panel']}; color: {t['text']};"
        f"  border: 1px solid {t['border']}; border-bottom: none;"
        f"  padding: 6px 16px; border-radius: 4px 4px 0 0; }}"
        f"QTabBar::tab:selected {{ background: {t['bg_widget']}; }}"
        f"QStatusBar {{ background: {t['bg_panel']}; color: {t['text_muted']}; }}"
        f"QSplitter::handle {{ background: {t['border']}; }}"
    )


def line_edit_sheet(t: dict) -> str:
    return (
        f"QLineEdit {{ background: {t['bg_widget']}; color: {t['text']};"
        f" border: 1px solid {t['border']}; border-radius: 6px; padding: 6px 10px; }}"
    )


def text_area_sheet(t: dict) -> str:
    return (
        f"QTextEdit {{ background: {t['bg_widget']}; color: {t['text']};"
        f" border: 1px solid {t['border']}; border-radius: 6px; padding: 6px; }}"
    )


def progress_bar_sheet(t: dict) -> str:
    return (
        f"QProgressBar {{ border: 1px solid {t['border']}; border-radius: 4px;"
        f" background: {t['progress_bg']}; color: {t['text']}; }}"
        f"QProgressBar::chunk {{ background-color: {t['blue']}; border-radius: 4px; }}"
    )


def primary_btn_sheet(t: dict) -> str:
    return (
        f"QPushButton {{ background-color: {t['blue']}; color: white; border: none;"
        f" border-radius: 6px; padding: 8px 18px; }}"
        f"QPushButton:hover {{ background-color: {t['blue_hover']}; }}"
        f"QPushButton:disabled {{ background-color: {t['disabled_bg']};"
        f" color: {t['disabled_fg']}; }}"
    )


def secondary_btn_sheet(t: dict) -> str:
    return (
        f"QPushButton {{ background-color: {t['bg_panel']}; color: {t['text']};"
        f" border: 1px solid {t['border']}; border-radius: 6px; padding: 8px 14px; }}"
        f"QPushButton:hover {{ background-color: {t['border']}; }}"
        f"QPushButton:disabled {{ background-color: {t['disabled_bg']};"
        f" color: {t['disabled_fg']}; }}"
    )


def cancel_btn_sheet(t: dict) -> str:
    return (
        f"QPushButton {{ background-color: {t['error']}; color: white; border: none;"
        f" border-radius: 6px; padding: 8px 18px; }}"
        f"QPushButton:hover {{ background-color: #b91c1c; }}"
        f"QPushButton:disabled {{ background-color: {t['disabled_bg']};"
        f" color: {t['disabled_fg']}; }}"
    )


def queue_list_sheet(t: dict) -> str:
    return (
        f"QListWidget {{ background: {t['bg_widget']}; border: 1px solid {t['border']};"
        f" border-radius: 6px; color: {t['text']}; }}"
        f"QListWidget::item {{ padding: 4px 8px; }}"
        f"QListWidget::item:selected {{ background: {t['blue']}; color: white; }}"
    )
