"""
ui/worker.py
Qt thread bridge between the async core and the Qt event loop.

WorkerSignals  — all pyqtSignals the core communicates back through
AsyncWorker    — QThread subclass that runs one coroutine per job
"""

from __future__ import annotations

import asyncio
from typing import Coroutine

from PyQt6.QtCore import QObject, QThread, pyqtSignal


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

class WorkerSignals(QObject):
    """
    All signals emitted by background jobs.
    Qt automatically marshals cross-thread signal emissions to the main thread,
    so UI slots connected here are always called safely.
    """
    log_analysis     = pyqtSignal(str)          # append to analysis pane
    log_progress     = pyqtSignal(str)          # append to progress pane
    status           = pyqtSignal(str, str)     # (text, hex colour)
    progress_val     = pyqtSignal(int)          # 0-100
    analysis_done    = pyqtSignal(object)       # AnalysisResult | None
    replication_done = pyqtSignal(bool)         # success flag


# ---------------------------------------------------------------------------
# Async worker
# ---------------------------------------------------------------------------

class AsyncWorker(QThread):
    """
    Runs a single coroutine in its own asyncio event loop on a worker thread.

    Usage::

        worker = AsyncWorker(some_coroutine(...))
        worker.start()          # non-blocking
        worker.cancel()         # request graceful stop
    """

    def __init__(self, coro: Coroutine) -> None:
        super().__init__()
        self._coro = coro
        self._loop: asyncio.AbstractEventLoop | None = None

    def run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._coro)
        finally:
            self._loop.close()
            self._loop = None

    def cancel(self) -> None:
        """
        Ask the event loop to stop at the next safe opportunity.
        The coroutine itself must also check a cancellation flag —
        this only stops the loop from accepting new callbacks.
        """
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
