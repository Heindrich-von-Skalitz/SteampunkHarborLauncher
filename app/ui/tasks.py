from __future__ import annotations

import traceback
from typing import Any, Callable

from PyQt6.QtCore import QThread, pyqtSignal

from ..utils import CancelledError, friendly_exception


_ACTIVE_TASKS: set["TaskThread"] = set()


class TaskThread(QThread):
    progressed = pyqtSignal(object)
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, function: Callable[[Callable[[object], None], Callable[[], bool]], Any],
                 parent=None, name: str = "task") -> None:
        super().__init__(parent)
        self.function = function
        self.setObjectName(name)
        self._cancel_requested = False
        _ACTIVE_TASKS.add(self)
        self.finished.connect(lambda: _ACTIVE_TASKS.discard(self))

    def emit_progress(self, value: object) -> None:
        self.progressed.emit(value)

    def is_cancelled(self) -> bool:
        return self._cancel_requested or self.isInterruptionRequested()

    def cancel(self) -> None:
        self._cancel_requested = True
        self.requestInterruption()

    def run(self) -> None:
        try:
            result = self.function(self.emit_progress, self.is_cancelled)
            if not self.is_cancelled():
                self.completed.emit(result)
        except CancelledError:
            self.cancelled.emit()
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(friendly_exception(exc))


def run_task(owner: Any, function: Callable, *, on_done: Callable | None = None,
             on_error: Callable | None = None, on_progress: Callable | None = None,
             on_cancelled: Callable | None = None, name: str = "task") -> TaskThread:
    """Start a background task and keep a reference on the owning widget."""
    thread = TaskThread(function, owner, name=name)
    tasks = getattr(owner, "_shl_tasks", None)
    if tasks is None:
        tasks = []
        setattr(owner, "_shl_tasks", tasks)
    tasks.append(thread)

    def cleanup() -> None:
        try:
            if thread in tasks:
                tasks.remove(thread)
        except Exception:
            pass

    if on_progress:
        thread.progressed.connect(on_progress)
    if on_done:
        thread.completed.connect(on_done)
    if on_error:
        thread.failed.connect(on_error)
    if on_cancelled:
        thread.cancelled.connect(on_cancelled)
    thread.finished.connect(cleanup)
    thread.start()
    return thread


def shutdown_tasks(wait_ms: int = 1800) -> None:
    """Cancel and drain background workers before the window is destroyed."""
    tasks = [task for task in list(_ACTIVE_TASKS) if task.isRunning()]
    for task in tasks:
        task.cancel()
    per_task = max(120, wait_ms // max(1, len(tasks)))
    for task in tasks:
        if not task.wait(per_task):
            # These are network/install workers at application shutdown. A
            # forced stop is preferable to destroying a live QThread.
            task.terminate()
            task.wait(350)
