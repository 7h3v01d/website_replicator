"""
ui/queue_panel.py
Self-contained queue widget — shows all download jobs and their status.
Has no knowledge of the core; it just displays QueueItem objects.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout, QWidget,
)

from ..core.models import QueueItem
from .theme import queue_list_sheet, secondary_btn_sheet


class QueuePanel(QWidget):
    """
    Displays all QueueItem objects as a coloured list.

    The owner (MainWindow) mutates items and calls refresh() / add_item().
    """

    def __init__(self, theme: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme  = theme
        self._items: list[QueueItem] = []
        self._build()

    # ── construction ──────────────────────────────────────────────────────────

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Header row
        hdr = QHBoxLayout()
        self._title_lbl = QLabel("Download Queue")
        self._title_lbl.setFont(QFont("Helvetica", 11, QFont.Weight.Bold))
        hdr.addWidget(self._title_lbl)
        hdr.addStretch()
        self._btn_clear = QPushButton("Clear Done")
        self._btn_clear.setFixedHeight(28)
        self._btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_clear.clicked.connect(self._clear_done)
        hdr.addWidget(self._btn_clear)
        layout.addLayout(hdr)

        # List
        self._list = QListWidget()
        self._list.setFont(QFont("Courier New", 9))
        layout.addWidget(self._list)

        self.restyle()

    # ── public API ─────────────────────────────────────────────────────────────

    def add_item(self, item: QueueItem) -> None:
        self._items.append(item)
        self._refresh()

    def update_item(self, item: QueueItem) -> None:
        """
        Update the display for a single *item* without rebuilding the whole list.
        Falls back to full refresh if the item's index can't be found.
        """
        try:
            idx = self._items.index(item)
        except ValueError:
            self._refresh()
            return

        t = self.theme
        status_colours = {
            QueueItem.PENDING:   t["text_muted"],
            QueueItem.ACTIVE:    t["info"],
            QueueItem.DONE:      t["success"],
            QueueItem.ERROR:     t["error"],
            QueueItem.CANCELLED: t["text_muted"],
        }
        list_item = self._list.item(idx)
        if list_item is None:
            self._refresh()
            return
        list_item.setText(item.label())
        list_item.setForeground(QColor(status_colours.get(item.status, t["text"])))

    def restyle(self) -> None:
        """Apply current theme without rebuilding the layout."""
        t = self.theme
        self._list.setStyleSheet(queue_list_sheet(t))
        self._btn_clear.setStyleSheet(secondary_btn_sheet(t))
        self._title_lbl.setStyleSheet(f"color: {t['text']};")
        self._refresh()

    def pending_items(self) -> list[QueueItem]:
        return [i for i in self._items if i.status == QueueItem.PENDING]

    def active_item(self) -> QueueItem | None:
        for i in self._items:
            if i.status == QueueItem.ACTIVE:
                return i
        return None

    # ── internals ─────────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        t = self.theme
        status_colours = {
            QueueItem.PENDING:   t["text_muted"],
            QueueItem.ACTIVE:    t["info"],
            QueueItem.DONE:      t["success"],
            QueueItem.ERROR:     t["error"],
            QueueItem.CANCELLED: t["text_muted"],
        }
        self._list.clear()
        for item in self._items:
            li = QListWidgetItem(item.label())
            li.setForeground(QColor(status_colours.get(item.status, t["text"])))
            self._list.addItem(li)

    def _clear_done(self) -> None:
        terminal = {QueueItem.DONE, QueueItem.ERROR, QueueItem.CANCELLED}
        self._items = [i for i in self._items if i.status not in terminal]
        self._refresh()
