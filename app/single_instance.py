from __future__ import annotations

import ctypes
import sys

from .constants import APP_BUILD_NAME


class SingleInstance:
    """Process-wide guard preventing a second launcher window."""

    ERROR_ALREADY_EXISTS = 183
    MUTEX_NAME = "Local\\SHL-Launcher-SingleInstance"

    def __init__(self) -> None:
        self._handle = None

    def acquire(self) -> bool:
        if not sys.platform.startswith("win"):
            return True
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, False, self.MUTEX_NAME)
        if not handle:
            return True
        if ctypes.get_last_error() == self.ERROR_ALREADY_EXISTS or kernel32.GetLastError() == self.ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle and sys.platform.startswith("win"):
            try:
                ctypes.windll.kernel32.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None

    @staticmethod
    def activate_existing() -> bool:
        if not sys.platform.startswith("win"):
            return False
        try:
            user32 = ctypes.windll.user32
            found: list[int] = []

            @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            def callback(hwnd, _lparam):
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                if buffer.value.startswith(APP_BUILD_NAME):
                    found.append(int(hwnd))
                    return False
                return True

            user32.EnumWindows(callback, 0)
            if found:
                user32.ShowWindow(found[0], 9)  # SW_RESTORE
                user32.SetForegroundWindow(found[0])
                return True
        except Exception:
            pass
        return False


__all__ = ["SingleInstance"]
