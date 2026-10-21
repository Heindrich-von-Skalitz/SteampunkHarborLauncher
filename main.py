from __future__ import annotations

import logging
import os
import sys
import traceback
from pathlib import Path

if getattr(sys, "frozen", False):
    ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
else:
    ROOT = Path(__file__).resolve().parent
# The launcher is source-first. Vendored BSD-2-Clause installation helper is
# loaded from /vendor so users do not need to install it globally.
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))

# The self-check must not create cache/data folders next to the executable.
# Set a disposable data directory before importing modules that initialise
# caches at import time.
_SELF_CHECK = "--self-check" in sys.argv
if _SELF_CHECK:
    import tempfile
    import uuid

    _boot = Path(tempfile.gettempdir()) / f"shl-selfcheck-boot-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    _boot.mkdir(parents=True, exist_ok=True)
    os.environ["SHL_DATA_DIR"] = str(_boot / "data")
    os.environ["SHL_MINECRAFT_DIR"] = str(_boot / "game")
    os.environ["SHL_DISABLE_STARTUP"] = "1"
    os.environ["SHL_SELFCHECK_BOOT"] = str(_boot)

from PyQt6.QtCore import Qt, QTimer  # noqa: E402
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox, QSplashScreen  # noqa: E402

from app.constants import APP_BUILD_NAME, APP_NAME, APP_NAME_EN, APP_VERSION  # noqa: E402
from app.paths import app_icon_path, ensure_all, logs_dir  # noqa: E402
from app.single_instance import SingleInstance  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.utils import setup_logging  # noqa: E402

LOG = logging.getLogger("shl")


def _install_exception_hook(app: QApplication) -> None:
    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        logging.getLogger("shl").error("Unhandled exception:\n%s", text)
        if QApplication.instance() is not None:
            QMessageBox.critical(
                None, "机械冒险港遇到了问题",
                f"启动器出现未处理的错误：\n\n{exc}\n\n"
                f"详细信息已写入日志：{logs_dir() / 'launcher.log'}")
    sys.excepthook = hook


def _set_app_user_model_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "SteampunkHarborLauncher.MechanicalHarbor.1")
    except Exception:
        pass


def _splash(app: QApplication) -> QSplashScreen | None:
    pixmap = QPixmap(str(app_icon_path()))
    if pixmap.isNull():
        return None
    splash_pixmap = QPixmap(380, 150)
    splash_pixmap.fill(QColor("#3FA34D"))
    painter = QPainter(splash_pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    icon = pixmap.scaled(72, 72, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.FastTransformation)
    painter.drawPixmap(28, 39, icon)
    painter.setPen(QColor("#FFFFFF"))
    font = painter.font()
    font.setFamily("Microsoft YaHei UI")
    font.setPixelSize(22)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(118, 63, APP_BUILD_NAME)
    font.setPixelSize(10)
    font.setBold(False)
    painter.setFont(font)
    painter.setPen(QColor(255, 255, 255, 220))
    painter.drawText(118, 88, "正在启动…")
    painter.setPen(QColor(255, 255, 255, 150))
    painter.drawText(118, 110, f"v{APP_VERSION}")
    painter.end()
    splash = QSplashScreen(splash_pixmap)
    splash.show()
    return splash


def main() -> int:
    if _SELF_CHECK:
        from app.selfcheck import run_self_check

        index = sys.argv.index("--self-check")
        output = sys.argv[index + 1] if index + 1 < len(sys.argv) else None
        return run_self_check(output)
    ensure_all()
    setup_logging()
    LOG.info("Launcher starting %s", APP_VERSION)
    guard = SingleInstance()
    already_running = not guard.acquire()
    _set_app_user_model_id()
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_BUILD_NAME)
    app.setApplicationDisplayName(APP_BUILD_NAME)
    app.setOrganizationName("Steampunk Harbor Works")
    app.setApplicationVersion(APP_VERSION)
    app.setWindowIcon(QIcon(str(app_icon_path())))
    app.setStyle("Fusion")
    _install_exception_hook(app)
    if already_running:
        guard.activate_existing()
        guard.release()
        return 0
    splash = _splash(app)
    window = MainWindow()
    window.show()
    if splash is not None:
        splash.finish(window)
    LOG.info("Launcher started %s", APP_VERSION)
    try:
        return app.exec()
    finally:
        guard.release()


def _emergency_log(text: str) -> None:
    """Write a crash log even when normal logging could not be opened."""

    import tempfile

    for path in (
        Path(tempfile.gettempdir()) / "SHL-crash.log",
        Path.cwd() / "SHL-crash.log",
    ):
        try:
            path.write_text(text, encoding="utf-8")
            return
        except OSError:
            continue


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        _emergency_log(traceback.format_exc())
        raise
