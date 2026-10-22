"""Headless self-check used by CI and by the packaged one-file executable.

Run with ``SHL.exe --self-check [output.json]``.  It creates the real main
window and the account dialog with the offscreen Qt platform, lets the UI
timers run for a moment (this is what caught the old GearSpinner crash), and
writes a small JSON report.  No network requests are made because startup
tasks are disabled.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path
from typing import Any


def _safe_print(text: str, *, err: bool = False) -> None:
    """Windowed builds may have sys.stdout/sys.stderr set to None."""

    stream = sys.stderr if err else sys.stdout
    if stream is None:
        return
    try:
        print(text, file=stream)
    except Exception:
        pass


def _prepare_environment() -> tuple[Path, Path | None]:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["SHL_DISABLE_STARTUP"] = "1"
    # tempfile.mkdtemp() uses mode 0o700, which becomes unreadable under some
    # Windows ACL configurations; a normal 0o777 workspace is safer here.
    work = Path(tempfile.gettempdir()) / f"shl-selfcheck-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True, exist_ok=True)
    os.environ["SHL_DATA_DIR"] = str(work / "data")
    os.environ["SHL_MINECRAFT_DIR"] = str(work / "game" / ".minecraft")
    os.environ.pop("SHL_MICROSOFT_CLIENT_ID", None)
    os.environ.pop("SHL_MS_CLIENT_ID", None)
    os.environ.pop("SHL_MS_OAUTH_PROVIDER", None)
    return work, None


def run_self_check(output: str | Path | None = None) -> int:
    started = time.time()
    work, _ = _prepare_environment()
    report: dict[str, Any] = {
        "ok": False,
        "version": "",
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "onefile": bool(os.environ.get("NUITKA_ONEFILE_DIRECTORY")),
        "checks": [],
    }

    from app.constants import APP_VERSION
    from app.paths import ensure_all, executable_dir
    from app.utils import setup_logging

    report["version"] = APP_VERSION
    metadata = executable_dir() / ".SHL"
    metadata_existed = metadata.exists()
    ensure_all()
    setup_logging()

    from PyQt6.QtCore import QEventLoop, QTimer
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")

    from app.ui.dialogs import AccountDialog
    from app.ui.main_window import MainWindow
    from app.ui.widgets import GearSpinner

    window = None
    dialog = None
    try:
        window = MainWindow()
        window.show()
        dialog = AccountDialog(window.theme, window.settings, window.microsoft, window)
        dialog.show()
        dialog._set_mode("microsoft")

        loop = QEventLoop()
        QTimer.singleShot(1400, loop.quit)
        loop.exec()
        app.processEvents()

        spinners = window.findChildren(GearSpinner) + dialog.findChildren(GearSpinner)
        for spinner in spinners:
            spinner._tick()
        report["checks"].append({"name": "qt_ui_constructed", "ok": True})
        task_ok = False
        try:
            task_key = window.begin_download_task("self-check")
            task_ok = (bool(window._download_tasks)
                       and hasattr(window, "_download_seq")
                       and window.tasks_page is not None)
            window.finish_download_task(task_key)
        except Exception:
            task_ok = False
        report["checks"].append({"name": "download_task_registry", "ok": task_ok})
        report["checks"].append({
            "name": "gear_spinner_timer",
            "ok": all(hasattr(item, "_angle") for item in spinners),
            "spinners": len(spinners),
        })
        report["checks"].append({
            "name": "login_ui",
            "ok": (hasattr(dialog, "ms_submit")
                   and hasattr(dialog, "_login_config")
                   and not hasattr(dialog, "client_id_edit")),
            "credentials_configured": bool(dialog._login_config.configured),
        })
        report["checks"].append({
            "name": "pages",
            "ok": all(hasattr(window, name) for name in (
                "home_page", "download_page", "versions_page", "content_page", "settings_page")),
        })

        from app.paths import cache_dir, data_dir, executable_dir, logs_dir, runtimes_dir
        from app.services.oauth_config import (
            build_config, ensure_local_oauth, oauth_file_path,
        )

        settings = window.settings
        game_dir = settings.minecraft_dir
        mode = str(settings.get("minecraft_dir_mode") or "")
        report["paths"] = {
            "data": str(data_dir()),
            "logs": str(logs_dir()),
            "cache": str(cache_dir()),
            "runtimes": str(runtimes_dir()),
            "game": str(game_dir),
            "game_mode": mode,
            "executable_dir": str(executable_dir()),
        }

        def writable(path: Path) -> bool:
            probe = path / ".selfcheck-write"
            try:
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
                return True
            except OSError:
                return False

        report["checks"].append({
            "name": "data_dirs_writable",
            "ok": all(writable(path) for path in (
                data_dir(), logs_dir(), cache_dir(), runtimes_dir())),
        })
        report["checks"].append({
            "name": "game_layout",
            "ok": game_dir.name.casefold() == ".minecraft" and mode in {
                "current", "official", "custom"},
        })
        report["checks"].append({
            "name": "no_game_dirs_next_to_exe",
            "ok": not any((executable_dir() / name).exists() for name in (
                "versions", "libraries", "assets", "mods", "saves",
                "resourcepacks", "shaderpacks")),
        })
        ensure_local_oauth(settings)
        bundled_app_id = bool(build_config().get("MICROSOFT_CLIENT_ID"))
        report["checks"].append({
            "name": "oauth_auto_generated",
            # A source checkout has no distribution app id, so generation is
            # not required there. A packaged build must generate the file.
            "ok": (not bundled_app_id) or oauth_file_path().exists(),
            "required": bundled_app_id,
            "path": str(oauth_file_path()),
            "configured": bool(window.microsoft.has_credentials()),
        })
        handlers = logging.getLogger("shl").handlers
        report["checks"].append({
            "name": "logging_ready",
            "ok": bool(handlers),
            "handler": getattr(handlers[0], "baseFilename", "") if handlers else "",
        })
        report["ok"] = all(item.get("ok") for item in report["checks"])
    except Exception as exc:  # noqa: BLE001 - report the real traceback
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
    finally:
        try:
            if dialog is not None:
                dialog.close()
            if window is not None:
                window.close()
            app.processEvents()
        except Exception:
            pass
        if not metadata_existed:
            shutil.rmtree(metadata, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)
        boot = os.environ.pop("SHL_SELFCHECK_BOOT", "")
        if boot:
            shutil.rmtree(boot, ignore_errors=True)

    report["elapsed_ms"] = int((time.time() - started) * 1000)
    target = Path(output) if output else (Path(tempfile.gettempdir()) / "shl-selfcheck.json")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    except OSError as exc:
        _safe_print(f"self-check: cannot write {target}: {exc}", err=True)
    _safe_print(json.dumps(report, ensure_ascii=False))
    return 0 if report.get("ok") else 1


__all__ = ["run_self_check"]
