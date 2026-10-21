from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from PyQt6.QtCore import (
    QByteArray, QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QAction, QColor, QDesktopServices, QFont, QGuiApplication, QIcon, QLinearGradient,
    QPainter, QPainterPath, QPen, QPixmap, QRadialGradient,
)
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QPushButton, QSizeGrip, QSizePolicy, QStackedWidget,
    QVBoxLayout, QWidget,
)

from ..constants import APP_BUILD_NAME, APP_NAME, APP_NAME_EN, APP_VERSION, THEME_NAMES
from ..core.installer import InstallerService
from ..core.java import JavaInfo, JavaManager
from ..core.launch import GameSession, LaunchBuilder, LaunchError, LaunchPlan
from ..core.minecraft import InstalledVersion, MinecraftManager
from ..core.modpack import ModpackService, record_source
from ..core.mods import ModManager
from ..paths import (
    app_icon_path, cache_dir, data_dir, ensure_launcher_metadata,
    infer_game_dir_mode, logs_dir, project_root,
)
from ..services.microsoft import MicrosoftAuth, MicrosoftAuthError
from ..services.curseforge import CurseForgeError, CurseForgeService
from ..services.modrinth import ModrinthError, ModrinthService
from ..services.mojang import LoaderService, MojangService
from ..services.oauth_config import ensure_local_oauth
from ..storage import Settings, automatic_memory_mb, make_offline_account
from ..utils import (
    CancelledError, LauncherError, NetworkError, format_size, friendly_exception,
    open_path, read_json,
)
from . import icons
from .dialogs import (
    AccountDialog, ConfirmDialog, GameConsoleDialog, InstallDialog, OfflineLaunchNotice,
    ProgressJobDialog, VersionSettingsDialog,
)
from .pages.content import ContentPage
from .pages.download import DownloadPage
from .pages.home import HomePage
from .pages.settings import SettingsPage
from .pages.versions import VersionsPage
from .tasks import TaskThread, run_task, shutdown_tasks
from .theme import THEMES, Theme, ThemeManager
from .widgets import (
    Avatar, BrandMark, Pill, ToastHost, icon_button, label, make_button,
    refresh_theme_widgets, refresh_widget_icons, repolish, set_icon,
)


def _required_java_for_base(base_version: str) -> int:
    text = str(base_version or "")
    import re
    numbers = [int(x) for x in re.findall(r"\d+", text)[:3]]
    if numbers:
        if len(numbers) >= 2 and numbers[0] == 1:
            major = numbers[1]
            if major >= 21:
                return 21
            if major >= 20 and len(numbers) >= 3 and numbers[2] >= 5:
                return 21
            if major >= 18:
                return 17
            if major >= 17:
                return 16
        if numbers[0] >= 26:
            return 25
        if numbers[0] >= 21:
            return 21
    # Snapshot IDs use dates like 24w14a; date >= 2024 implies Java 21.
    match = re.match(r"(\d{2})w", text)
    if match:
        year = int(match.group(1))
        if year >= 26:
            return 25
        return 21 if year >= 24 else 17
    return 8


class ShellRoot(QWidget):
    """Flat content surface; no artwork, grain, arcs or brass rivets."""

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("WindowRoot")
        self.theme = theme
        self.maximized = False
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.update()

    def set_maximized(self, maximized: bool) -> None:
        self.maximized = maximized
        self.update()


class ResizeGrip(QWidget):
    def __init__(self, edges: Qt.Edge, cursor: Qt.CursorShape, parent: QWidget) -> None:
        super().__init__(parent)
        self.edges = edges
        self.setCursor(cursor)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle:
                handle.startSystemResize(self.edges)
                event.accept()
                return
        super().mousePressEvent(event)


class AccountChip(QFrame):
    """Clickable account chip; does not start a window-move drag."""

    clicked = pyqtSignal()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
                event.position().toPoint()):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class TitleBar(QFrame):
    """Single-color top bar with plain navigation."""

    def __init__(self, shell: "MainWindow", theme: Theme) -> None:
        super().__init__(shell)
        self.shell = shell
        self.theme = theme
        self.setObjectName("TitleBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(54)
        self.setMouseTracking(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 8, 0)
        layout.setSpacing(8)

        self.brand_mark = BrandMark(theme.id, 30)
        layout.addWidget(self.brand_mark)
        self.brand_name = QLabel("SHL")
        self.brand_name.setObjectName("BrandName")
        self.brand_name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.brand_name)
        layout.addStretch(1)

        self.nav_layout = QHBoxLayout()
        self.nav_layout.setSpacing(2)
        self.nav_buttons: dict[str, QPushButton] = {}
        for key, text, icon_name in (
            ("home", "启动", "play"), ("download", "下载", "download_cloud"),
            ("versions", "版本", "layers"), ("content", "内容", "package"),
            ("settings", "设置", "sliders"),
        ):
            button = make_button(text, "topnav", icon_name, "on_accent", 15)
            button.setMinimumWidth(76)
            button.setFixedHeight(32)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, k=key: shell.navigate(k))
            self.nav_buttons[key] = button
            self.nav_layout.addWidget(button)
        layout.addLayout(self.nav_layout)
        layout.addStretch(1)

        self.account_chip = AccountChip()
        self.account_chip.setObjectName("AccountChip")
        self.account_chip.setAttribute(Qt.WidgetAttribute.WA_NoMousePropagation, True)
        self.account_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.account_chip.setFixedHeight(32)
        self.account_chip.setMinimumWidth(132)
        account_layout = QHBoxLayout(self.account_chip)
        account_layout.setContentsMargins(6, 3, 8, 3)
        account_layout.setSpacing(6)
        self.account_avatar = Avatar("Player", 24)
        account_layout.addWidget(self.account_avatar)
        self.account_name = QLabel("未登录")
        self.account_name.setObjectName("TopAccountName")
        account_layout.addWidget(self.account_name, 1)
        self.account_type = QLabel("离线")
        self.account_type.setObjectName("TopAccountType")
        self.account_type.hide()
        self.account_arrow = QLabel()
        self.account_arrow.setPixmap(icons.pixmap("chevron_down", theme.on_accent, 10))
        account_layout.addWidget(self.account_arrow)
        for child in (self.account_avatar, self.account_name, self.account_type,
                      self.account_arrow):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.account_chip.clicked.connect(shell.open_accounts)
        layout.addWidget(self.account_chip)
        layout.addSpacing(2)

        self.minimize_button = icon_button("minimize", "最小化", "topwindow", "on_accent", 14)
        self.minimize_button.setFixedSize(28, 28)
        self.minimize_button.clicked.connect(shell.showMinimized)
        self.maximize_button = icon_button("maximize", "最大化", "topwindow", "on_accent", 13)
        self.maximize_button.setFixedSize(28, 28)
        self.maximize_button.clicked.connect(shell.toggle_maximize)
        self.close_button = icon_button("close", "关闭", "topclose", "on_accent", 14)
        self.close_button.setFixedSize(28, 28)
        self.close_button.clicked.connect(shell.close)
        for button in (self.minimize_button, self.maximize_button, self.close_button):
            layout.addWidget(button)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.position().toPoint())
            if child is not None and (child is self.account_chip
                                      or self.account_chip.isAncestorOf(child)):
                super().mousePressEvent(event)
                return
            handle = self.window().windowHandle()
            if handle:
                handle.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.shell.toggle_maximize()
        super().mouseDoubleClickEvent(event)

    def update_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.brand_mark.set_theme(theme.id)
        self.account_arrow.setPixmap(icons.pixmap("chevron_down", theme.on_accent, 10))
        self.update()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.app = QApplication.instance()
        self.settings = Settings()
        self.data_dir = data_dir()
        self.project_dir = project_root()
        self._logs_dir = logs_dir()
        self.settings.ensure_minecraft_dirs()
        # Distribution builds carry their application id in the exe and
        # generate the local oauth.json on first launch; the player never has
        # to paste it and the file is not shipped separately.
        ensure_local_oauth(self.settings)
        ensure_launcher_metadata(
            self.settings.minecraft_dir,
            infer_game_dir_mode(self.settings.minecraft_dir))
        saved_theme = str(self.settings.get("theme") or "creeper")
        if saved_theme not in THEMES:
            # Old ornate palettes are retired in the clean UI; fall back to the
            # creeper-green default rather than showing a stale theme id.
            saved_theme = "creeper"
            self.settings.set("theme", saved_theme)
        self.theme_manager = ThemeManager(self.app, saved_theme)
        self.theme_id = self.theme_manager.current.id
        self.theme = self.theme_manager.current
        self.theme_manager.set_theme(self.theme_id)

        self.java_manager = JavaManager(self.settings)
        self.mojang = MojangService(self.settings)
        self.loaders = LoaderService(self.settings)
        self.minecraft = MinecraftManager(self.settings)
        self.installer = InstallerService(self.settings, self.minecraft, self.mojang, self.loaders)
        self.mod_manager = ModManager(self.settings, self.minecraft)
        self.modrinth = ModrinthService()
        self.curseforge = CurseForgeService(self.settings)
        self.microsoft = MicrosoftAuth(self.settings)
        self.modpack = ModpackService(self.settings, self.minecraft, self.installer, self.modrinth)
        self.launch_builder = LaunchBuilder(self.settings, self.minecraft)
        self.java_list: list[JavaInfo] = self.java_manager.cached()
        self.versions: list[InstalledVersion] = []
        self.manifest_versions: list[dict[str, Any]] = []
        self.latest_release = ""
        self.current_version_id = ""
        self.game_session: GameSession | None = None
        self.game_console: GameConsoleDialog | None = None
        self._launching = False
        self._java_scanning = False
        self._manifest_loading = False
        self._closing = False
        self._nav = ""
        self._console_visible = False
        self._init_state()
        self._init_window()
        self._build_ui()
        self._connect_pages()
        self._apply_theme_to_chrome()
        self._refresh_all_icons()
        self.navigate("home", remember=False)
        self._update_footer()
        QTimer.singleShot(220, self._startup_tasks)

    # ------------------------------------------------------------------ setup
    def _init_state(self) -> None:
        self.versions = self.minecraft.scan()
        selected = str(self.settings.selected_version or "")
        valid_ids = {v.id for v in self.versions}
        if selected not in valid_ids:
            selected = self.versions[0].id if self.versions else ""
        self.current_version_id = selected
        if selected:
            self.settings.set("selected_version", selected)
        latest_release = ""
        try:
            cached = read_json(cache_dir() / "version_manifest_v2.json", {})
            latest_release = str((cached.get("latest") or {}).get("release") or "") if isinstance(cached, dict) else ""
        except Exception:
            pass
        self.latest_release = latest_release

    def _init_window(self) -> None:
        self.setWindowTitle(f"{APP_BUILD_NAME} {APP_VERSION}")
        self.setWindowIcon(QIcon(str(app_icon_path())))
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setMinimumSize(1120, 700)
        geometry = self.settings.get("window_geometry") or ""
        restored = False
        if geometry:
            try:
                restored = self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
            except Exception:
                restored = False
        if not restored:
            width = int(self.settings.get("launcher_window_width") or 1368)
            height = int(self.settings.get("launcher_window_height") or 882)
            self.resize(width, height)
            screen = QGuiApplication.primaryScreen()
            if screen:
                rect = screen.availableGeometry()
                self.move(rect.center().x() - self.width() // 2,
                          rect.center().y() - self.height() // 2)

    def _build_ui(self) -> None:
        self.shell_root = ShellRoot(self.theme, self)
        self.setCentralWidget(self.shell_root)
        root_layout = QVBoxLayout(self.shell_root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.titlebar = TitleBar(self, self.theme)
        root_layout.addWidget(self.titlebar)
        self.stack = QStackedWidget()
        self.stack.setObjectName("PageStack")
        root_layout.addWidget(self.stack, 1)
        self.footer = QFrame(self.shell_root)
        self.footer.setObjectName("FooterBar")
        self.footer.setFixedHeight(27)
        footer_layout = QHBoxLayout(self.footer)
        footer_layout.setContentsMargins(19, 0, 19, 0)
        footer_layout.setSpacing(8)
        self.footer_dot = QLabel()
        self.footer_dot.setFixedSize(10, 10)
        footer_layout.addWidget(self.footer_dot)
        self.footer_status = QLabel("启动器正在准备…")
        self.footer_status.setStyleSheet(f"color: {self.theme.text_faint}; font-size: 9.5px;")
        footer_layout.addWidget(self.footer_status)
        footer_layout.addStretch()
        self.footer_source = QLabel("MOJANG · MODRINTH")
        self.footer_source.setStyleSheet(
            f"color: {self.theme.text_faint}; font-size: 8px; letter-spacing: 1px;")
        footer_layout.addWidget(self.footer_source)
        divider = QFrame()
        divider.setFixedWidth(1)
        divider.setFixedHeight(11)
        divider.setStyleSheet(f"background: {self.theme.border_soft};")
        footer_layout.addWidget(divider)
        footer_version = QLabel(f"v{APP_VERSION} · 主题免费")
        footer_version.setStyleSheet(f"color: {self.theme.text_faint}; font-size: 9px;")
        footer_layout.addWidget(footer_version)
        # A clean launcher has no decorative footer; keep the status labels for
        # internal updates but do not consume vertical space.
        self.footer.hide()

        self.home_page = HomePage(self, self.theme)
        self.download_page = DownloadPage(self, self.theme)
        self.versions_page = VersionsPage(self, self.theme)
        self.content_page = ContentPage(self, self.theme)
        self.settings_page = SettingsPage(self, self.theme)
        self.pages = {
            "home": self.home_page, "download": self.download_page,
            "versions": self.versions_page, "content": self.content_page,
            "settings": self.settings_page,
        }
        for page in self.pages.values():
            self.stack.addWidget(page)
        self.toast_host = ToastHost(self.shell_root)
        self.toast_host.setGeometry(self.shell_root.rect())
        self.toast_host.raise_()
        self._grips: list[ResizeGrip] = []
        self._create_grips()
        font = QFont(self.theme_manager.font_ui, 10)
        self.setFont(font)

    def _connect_pages(self) -> None:
        self.download_page.manifest_ready.connect(self._manifest_page_ready)
        self._update_account_chip()

    def _create_grips(self) -> None:
        specs = [
            (Qt.Edge.TopEdge, Qt.CursorShape.SizeVerCursor),
            (Qt.Edge.BottomEdge, Qt.CursorShape.SizeVerCursor),
            (Qt.Edge.LeftEdge, Qt.CursorShape.SizeHorCursor),
            (Qt.Edge.RightEdge, Qt.CursorShape.SizeHorCursor),
            (Qt.Edge.TopEdge | Qt.Edge.LeftEdge, Qt.CursorShape.SizeFDiagCursor),
            (Qt.Edge.TopEdge | Qt.Edge.RightEdge, Qt.CursorShape.SizeBDiagCursor),
            (Qt.Edge.BottomEdge | Qt.Edge.LeftEdge, Qt.CursorShape.SizeBDiagCursor),
            (Qt.Edge.BottomEdge | Qt.Edge.RightEdge, Qt.CursorShape.SizeFDiagCursor),
        ]
        for edges, cursor in specs:
            grip = ResizeGrip(edges, cursor, self.shell_root)
            grip.show()
            self._grips.append(grip)
        self._position_grips()

    def _position_grips(self) -> None:
        if not hasattr(self, "shell_root") or not hasattr(self, "_grips"):
            return
        if self.isMaximized() or self.isFullScreen():
            for grip in self._grips:
                grip.hide()
            return
        width, height = self.shell_root.width(), self.shell_root.height()
        size = 6
        corner = 13
        self._grips[0].setGeometry(corner, 0, width - corner * 2, size)
        self._grips[1].setGeometry(corner, height - size, width - corner * 2, size)
        self._grips[2].setGeometry(0, corner, size, height - corner * 2)
        self._grips[3].setGeometry(width - size, corner, size, height - corner * 2)
        self._grips[4].setGeometry(0, 0, corner, corner)
        self._grips[5].setGeometry(width - corner, 0, corner, corner)
        self._grips[6].setGeometry(0, height - corner, corner, corner)
        self._grips[7].setGeometry(width - corner, height - corner, corner, corner)
        for grip in self._grips:
            grip.show()
            grip.raise_()
        self.toast_host.raise_()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if hasattr(self, "toast_host"):
            self.toast_host.setGeometry(self.shell_root.rect())
        self._position_grips()

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if hasattr(self, "shell_root"):
            maximized = self.isMaximized()
            self.shell_root.set_maximized(maximized)
            if hasattr(self, "titlebar"):
                self.titlebar.maximize_button.setIcon(
                    icons.icon("restore" if maximized else "maximize",
                               self.theme.color("muted"), 14))
            self._position_grips()

    def _startup_tasks(self) -> None:
        if os.environ.get("SHL_DISABLE_STARTUP") == "1":
            return
        self._load_manifest_silent()
        self.rescan_java(silent=True)

    # ------------------------------------------------------------------ shell API
    def toast(self, text: str, kind: str = "info", duration: int = 3800) -> None:
        if hasattr(self, "toast_host"):
            self.toast_host.toast(text, kind, duration)

    def navigate(self, page_id: str, tab: str | None = None, remember: bool = True) -> None:
        page = self.pages.get(page_id)
        if not page:
            return
        self.stack.setCurrentWidget(page)
        self._nav = page_id
        for key, button in self.titlebar.nav_buttons.items():
            active = key == page_id
            button.setProperty("active", "true" if active else "false")
            self._set_nav_icon(key, active)
            repolish(button)
        if remember:
            self.settings.set("last_page", page_id, save=False)
        if page_id == "download":
            if tab:
                self.download_page.switcher.set_current(tab)
                self.download_page._switch_tab(tab)
            self.download_page.on_shown()
        elif page_id == "content":
            if tab:
                self.content_page.focus_tab(tab)
            self.content_page.refresh()
        elif page_id == "settings":
            if tab:
                self.settings_page.show_tab(tab)
            self.settings_page.refresh_paths()
            self.settings_page.refresh_java()
            self.settings_page.refresh_accounts()
        elif page_id == "versions":
            self.versions_page.selected_id = self.current_version_id
            self.versions_page.refresh()
        elif page_id == "home":
            self.home_page.refresh_versions()
            self.home_page.refresh_account()
            self.home_page.refresh_news()
        self._refresh_nav_styles()

    def _refresh_nav_styles(self) -> None:
        for key, button in self.titlebar.nav_buttons.items():
            active = key == self._nav
            button.setProperty("active", "true" if active else "false")
            self._set_nav_icon(key, active)
            repolish(button)

    def _set_nav_icon(self, key: str, active: bool) -> None:
        button = self.titlebar.nav_buttons.get(key)
        if button is None:
            return
        name = {"home": "play", "download": "download_cloud", "versions": "layers",
                "content": "package", "settings": "sliders"}[key]
        role = "accent_lo" if active else "on_accent"
        color = self.theme.color(role)
        set_icon(button, name, role, 15)
        button.setIcon(icons.icon(name, color, 15))
        button.setStyleSheet(f"color: {color};")

    def toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    # ------------------------------------------------------------------ versions / accounts
    def current_version(self) -> InstalledVersion | None:
        if not self.current_version_id:
            return None
        return next((v for v in self.versions if v.id == self.current_version_id),
                    self.minecraft.find(self.current_version_id))

    def set_current_version(self, version_id: str, notify: bool = False) -> None:
        if not version_id or version_id == self.current_version_id:
            return
        self.current_version_id = version_id
        self.settings.set("selected_version", version_id, save=False)
        if hasattr(self, "home_page"):
            self.home_page.refresh_versions()
            self.home_page.refresh_stats()
        if hasattr(self, "download_page"):
            self.download_page.refresh_dynamic()
        if hasattr(self, "versions_page"):
            self.versions_page.selected_id = version_id
            self.versions_page.refresh()
        if notify:
            self.toast(f"当前版本：{version_id}", "info", 2000)

    def rescan_versions(self) -> None:
        self.refresh_all_versions()
        self.toast("版本扫描完成", "success")

    def refresh_all_versions(self, select: str | None = None) -> None:
        old = select or self.current_version_id
        self.versions = self.minecraft.scan()
        ids = {v.id for v in self.versions}
        if old not in ids:
            old = self.versions[0].id if self.versions else ""
        self.current_version_id = old
        if old:
            self.settings.set("selected_version", old, save=False)
        if hasattr(self, "home_page"):
            self.home_page.refresh_versions()
            self.home_page.refresh_account()
            self.home_page.refresh_news()
            self.home_page.refresh_history()
        if hasattr(self, "download_page"):
            self.download_page.refresh_dynamic()
        if hasattr(self, "versions_page"):
            self.versions_page.selected_id = self.current_version_id
            self.versions_page.refresh()
        if hasattr(self, "content_page"):
            self.content_page.selected_id = self.current_version_id
            if self.stack.currentWidget() is self.content_page:
                self.content_page.refresh()
        if hasattr(self, "settings_page"):
            self.settings_page.refresh_paths()
        self._update_footer()

    def refresh_home_stats(self) -> None:
        if hasattr(self, "home_page"):
            self.home_page.refresh_stats()
            self.home_page.refresh_java()

    def refresh_content_counts(self) -> None:
        self.refresh_home_stats()

    def resourcepack_count(self, version: InstalledVersion) -> int:
        return self.minecraft.content_counts(version)["resourcepacks"]

    def logs_dir(self) -> Path:
        return self._logs_dir

    def automatic_memory_mb(self) -> int:
        return automatic_memory_mb()

    def make_offline_account(self, name: str) -> dict[str, Any]:
        return make_offline_account(name)

    # ------------------------------------------------------------------ Java
    def rescan_java(self, silent: bool = False) -> None:
        if self._java_scanning:
            return
        self._java_scanning = True
        if not silent:
            self.toast("正在重新检测 Java…", "info", 1600)
        self._set_footer("正在扫描 Java 运行时…")

        def job(progress, cancel):
            return self.java_manager.discover()

        run_task(self, job, on_done=lambda result: self._java_scanned(result, silent),
                 on_error=lambda message: self._java_scan_failed(message, silent),
                 name="java-scan")

    def _java_scanned(self, result: object, silent: bool) -> None:
        self._java_scanning = False
        if isinstance(result, list):
            self.java_list = [j for j in result if isinstance(j, JavaInfo)]
        else:
            self.java_list = self.java_manager.cached()
        if hasattr(self, "home_page"):
            self.home_page.refresh_java()
            self.home_page.refresh_stats()
        if hasattr(self, "settings_page"):
            self.settings_page.theme = self.theme
            self.settings_page.refresh_java()
        self._set_footer(f"Java {len(self.java_list)} 个 · 已就绪")
        if not self.java_list and not silent:
            self.toast("没有检测到可用的 Java，可在设置中下载", "warning")

    def _java_scan_failed(self, message: str, silent: bool) -> None:
        self._java_scanning = False
        self._set_footer("Java 扫描失败")
        if not silent:
            self.toast(message, "error")

    def java_for_version(self, version: InstalledVersion | None,
                         allow_error: bool = False) -> JavaInfo | None:
        if not version:
            return None
        required = version.java_major
        explicit = str(self.settings.version_profile(version.id).get("java_path") or "")
        if not explicit:
            configured = str(self.settings.get("java_path") or "")
            explicit = configured if configured else ""
        if explicit:
            path = Path(explicit)
            for info in self.java_list:
                if info.path == path and info.major >= required:
                    return info
        candidates = [info for info in self.java_list if info.major >= required]
        if candidates:
            return min(candidates, key=lambda info: (info.major, info.vendor))
        if allow_error:
            try:
                return self.java_manager.choose(required, explicit_path=explicit)
            except LauncherError:
                return None
        return None

    def java_for_install(self, base_version: str) -> JavaInfo | None:
        required = _required_java_for_base(base_version)
        candidates = [info for info in self.java_list if info.major >= required]
        if candidates:
            return min(candidates, key=lambda info: (info.major, info.vendor))
        try:
            return self.java_manager.choose(required)
        except LauncherError:
            return None

    def required_java_for(self, base_version: str) -> int:
        return _required_java_for_base(base_version)

    def detected_java_majors(self) -> list[int]:
        return sorted({int(info.major) for info in self.java_list if info.major})

    def ensure_java_for(self, required: int, purpose: str = "") -> JavaInfo | None:
        """Return a compatible Java, offering to download Temurin if missing.

        The old install dialog simply disabled the button with "no Java" even
        when Java 21 was installed, because a 26.x version requires Java 25.
        This method explains the version gap and downloads the required JRE.
        """

        required = max(8, int(required))
        candidates = [info for info in self.java_list if info.major >= required]
        if candidates:
            return min(candidates, key=lambda info: (info.major, info.vendor))
        try:
            chosen = self.java_manager.choose(required)
        except LauncherError:
            chosen = None
        if chosen is not None:
            return chosen

        detected = self.detected_java_majors()
        detected_text = "、".join(f"Java {major}" for major in detected) or "没有检测到 Java"
        prefix = f"{purpose}需要" if purpose else "需要"
        message = (
            f"{prefix} Java {required} 或更高版本。\n"
            f"当前检测到：{detected_text}。\n\n"
            f"是否现在下载 Eclipse Temurin {required}？下载完成后会自动继续。"
        )
        if not ConfirmDialog.ask(
                self, self.theme, f"需要 Java {required}", message,
                f"下载 Java {required}", danger=False):
            return None

        def job(progress, cancel):
            return self.java_manager.download_temurin(
                required,
                progress=lambda percent, text: progress((percent, text)),
                cancel=cancel,
            )

        dialog = ProgressJobDialog(
            self.theme, f"下载 Java {required}",
            f"Eclipse Temurin {required} JRE · 安装到启动器运行库",
            job, success_text="Java 运行时就绪。", width=660, height=500, parent=self)
        if dialog.run() != QDialog.DialogCode.Accepted:
            return None
        info = dialog.result_object
        if not isinstance(info, JavaInfo):
            self.toast("Java 下载没有返回有效运行时", "error")
            return None
        self.java_list = [
            item for item in self.java_list if item.identity != info.identity
        ] + [info]
        self.java_list.sort(key=lambda item: item.major, reverse=True)
        self.refresh_java()
        self.toast(f"Java {info.major} 已就绪：{info.vendor}", "success")
        return info

    def refresh_java(self, force: bool = False) -> None:
        if force:
            self.rescan_java()
            return
        if hasattr(self, "home_page"):
            self.home_page.refresh_java()
        if hasattr(self, "settings_page"):
            self.settings_page.refresh_java()

    # ------------------------------------------------------------------ launch
    def launch_current(self) -> None:
        if not self.current_version_id:
            self.toast("还没有安装版本，先安装一个版本吧", "warning")
            self.navigate("download")
            return
        self.launch_version(self.current_version_id)

    def launch_version(self, version_id: str) -> None:
        if self.game_session and self.game_session.running:
            self.toast("游戏已经在运行了，先结束当前进程再启动新版本", "warning")
            if self.game_console:
                self.game_console.show()
                self.game_console.raise_()
            return
        if self._launching:
            self.toast("正在准备启动，请稍候…", "info")
            return
        version = next((v for v in self.versions if v.id == version_id), None)
        if version is None:
            try:
                version = self.minecraft.inspect(version_id)
            except LauncherError as exc:
                self.toast(str(exc), "error")
                return
        self.set_current_version(version.id)
        account = self.settings.active_account()
        if not account:
            self.open_accounts()
            return
        if str(account.get("type") or "offline") != "microsoft":
            notice = OfflineLaunchNotice(self.theme, str(account.get("name") or "玩家"), self)
            notice.exec()
            if notice.action == "login":
                self.open_accounts()
                self.toast("登录微软正版后，请再次启动游戏。", "info", 3500)
                return
        if self.java_for_version(version, allow_error=True) is None:
            if not self.ensure_java_for(version.java_major, f"启动 {version.id}"):
                self.toast(f"启动已取消：需要 Java {version.java_major}+", "warning")
                return
        self._launching = True
        self._set_launch_ui(True)
        self._set_footer("正在构建启动参数…")
        self.home_page.launch_hint.setText("正在检查 Java 与版本文件")
        self.home_page.launch_subhint.setText("第一次启动会解压 Natives，请稍候")

        def job(progress, cancel):
            progress((10, "读取版本信息…"))
            java = self.java_for_version(version, allow_error=True)
            if not java:
                raise LaunchError(
                    f"没有找到满足 Java {version.java_major}+ 的运行环境。请前往设置检测 Java 或下载 Temurin 21 / 25。")
            progress((35, f"选择 Java {java.major}…"))
            plan = self.launch_builder.build(version, account, java)
            progress((80, "准备游戏进程…"))
            return plan

        run_task(self, job, on_done=self._launch_prepared,
                 on_error=self._launch_failed, on_progress=self._launch_progress,
                 name="launch-prepare")

    def _launch_progress(self, value: object) -> None:
        if isinstance(value, tuple) and len(value) > 1:
            self._set_footer(str(value[1]))
            self.home_page.launch_hint.setText(str(value[1]))

    def _launch_prepared(self, plan: object) -> None:
        if not isinstance(plan, LaunchPlan):
            self._launch_failed("启动参数构建返回异常。")
            return
        assert isinstance(plan, LaunchPlan)
        if self.game_console is not None:
            self.game_console.hide()
            self.game_console.deleteLater()
            self.game_console = None
        session = GameSession(plan, self)
        self.game_session = session
        session.started.connect(self._game_started)
        session.logged.connect(self._game_logged)
        session.exited.connect(self._game_exited)
        session.failed.connect(self._game_failed)
        session.finished.connect(self._game_finished)
        console = GameConsoleDialog(self.theme, plan, session, self)
        self.game_console = console
        console.hide()
        for warning in plan.warnings:
            console.append_line(f"[启动器] {warning}")
        if plan.warnings:
            self.toast(plan.warnings[0], "warning", 5000)
        session.start()
        mode = str(self.settings.get("open_console") or "on_error")
        if mode == "always" or not session.running:
            console.show()
            console.raise_()
            self._console_visible = True
        else:
            self._console_visible = False
        if not session.process:
            return

    def _game_started(self, pid: int) -> None:
        self._launching = False
        self.settings.record_launch(self.current_version_id, pid=pid)
        self._set_launch_ui(False)
        assert self.game_session is not None and self.game_session.plan is not None
        plan = self.game_session.plan
        self.home_page.launch_button.setEnabled(False)
        self.home_page.launch_button.setText("游戏运行中")
        self.home_page.launch_hint.setText(f"PID {pid} · Java {plan.java.major}")
        self.home_page.launch_subhint.setText("游戏窗口即将出现，可在日志窗口查看输出")
        self.toast(f"Minecraft 已启动 · PID {pid}", "success")
        self._set_footer(f"正在运行 · {plan.version.id} · PID {pid}")
        self.home_page.refresh_history()
        self.home_page.refresh_stats()
        mode = str(self.settings.get("launcher_visibility") or "keep")
        if mode == "minimize":
            QTimer.singleShot(900, self.showMinimized)

    def _game_logged(self, line: str) -> None:
        pass

    def _game_exited(self, code: int) -> None:
        self._launching = False
        self._set_launch_ui(False)
        if self.game_session and self.game_session.plan:
            version = self.game_session.plan.version.id
            self._set_footer(f"运行结束 · {version} · 退出代码 {code}")
        if code != 0 and str(self.settings.get("open_console") or "on_error") in {"always", "on_error"}:
            if self.game_console:
                self.game_console.show()
                self.game_console.raise_()
                self._console_visible = True
        self.toast("Minecraft 已退出" + (f"（代码 {code}）" if code else ""),
                   "info" if code == 0 else "warning")
        self.home_page.refresh_history()
        self.home_page.refresh_stats()
        self.game_session = None

    def _game_failed(self, message: str) -> None:
        self._launching = False
        self._set_launch_ui(False)
        self._set_footer("启动失败")
        self.toast(message, "error", 6500)
        if self.game_console:
            self.game_console.show()
            self.game_console.raise_()
            self._console_visible = True
        self.game_session = None

    def _game_finished(self) -> None:
        if self._launching:
            self._launching = False
            self._set_launch_ui(False)

    def _set_launch_ui(self, busy: bool) -> None:
        if hasattr(self, "home_page"):
            self.home_page.set_launching(busy)
        if hasattr(self, "titlebar"):
            for key, button in self.titlebar.nav_buttons.items():
                button.setEnabled(not busy)

    def _launch_failed(self, message: str) -> None:
        self._launching = False
        self._set_launch_ui(False)
        self.home_page.launch_hint.setText("启动参数准备失败")
        self.home_page.launch_subhint.setText("请检查 Java、玩家或版本文件")
        self._set_footer("启动失败")
        action_settings = "Java" in message or "java" in message or "玩家" in message or "账号" in message
        if action_settings:
            if ConfirmDialog.ask(self, self.theme, "无法启动", message, "前往设置", danger=False):
                self.navigate("settings", tab="java")
        elif "支持库" in message or "客户端" in message or "Natives" in message:
            if ConfirmDialog.ask(self, self.theme, "版本文件不完整", message, "补全文件", danger=False):
                self.repair_version(self.current_version_id)
        else:
            QMessageBox.warning(self, "无法启动", message)

    def open_game_console(self) -> None:
        if self.game_console:
            self.game_console.show()
            self.game_console.raise_()
        else:
            self.toast("当前没有游戏日志窗口；启动游戏后会自动创建。", "info")

    # ------------------------------------------------------------------ installs
    def start_install(self, version_id: str, loader: str = "vanilla",
                      loader_version: str = "", display_name: str = "") -> None:
        java_path = ""
        if loader in {"forge", "neoforge"}:
            required = self.required_java_for(version_id)
            java = self.ensure_java_for(required, f"{version_id} 的 {loader} 安装器")
            if not java:
                self.toast(f"安装已取消：需要 Java {required}+", "warning")
                return
            java_path = str(java.path)
        dialog = InstallDialog(self.theme, self.installer, version_id, loader=loader,
                               loader_version=loader_version, java_path=java_path,
                               parent=self)
        accepted = dialog.run()
        result = dialog.result_object
        if accepted == QDialog.DialogCode.Accepted and result is not None:
            installed_id = getattr(result, "id", version_id)
            if display_name:
                self.settings.save_version_profile(
                    installed_id, {"display_name": display_name})
            self.refresh_all_versions(select=installed_id)
            self.toast(f"{version_id} 装配完成", "success")
            self.home_page.refresh_news()
        else:
            self.toast("安装没有完成", "info")

    def repair_version(self, version_id: str) -> None:
        def job(progress, cancel):
            state = {"percent": 0}
            def report(pct, message):
                state["percent"] = pct
                progress((pct, message))
            def log(message):
                progress((state["percent"], message))
            return self.installer.repair(version_id, progress=report, log=log, cancel=cancel)

        dialog = ProgressJobDialog(
            self.theme, "补全版本文件", f"检查并修复 {version_id} 的客户端与依赖库",
            job, success_text="版本文件已补全。", width=690, height=500, parent=self)
        if dialog.run() == QDialog.DialogCode.Accepted:
            self.refresh_all_versions(select=version_id)
            self.toast(f"{version_id} 已补全", "success")

    def delete_version(self, version_id: str) -> None:
        if not ConfirmDialog.ask(
                self, self.theme, "移入回收站",
                f"要把版本「{version_id}」移到启动器回收站吗？你仍可在数据目录中手动恢复。",
                "移入回收站", danger=True):
            return
        try:
            self.minecraft.delete_version(version_id)
        except (LauncherError, OSError) as exc:
            self.toast(str(exc), "error")
            return
        self.refresh_all_versions()
        self.toast(f"已移除 {version_id}", "success")

    def open_version_settings(self, version_id: str) -> None:
        version = next((v for v in self.versions if v.id == version_id), None)
        if not version:
            version = self.minecraft.find(version_id)
        if not version:
            return
        dialog = VersionSettingsDialog(
            self.theme, self.settings, self.minecraft, version, self.java_manager,
            parent=self, on_repair=self.repair_version, on_delete=self.delete_version)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        if not accepted:
            return
        action = getattr(dialog, "requested_action", None)
        self.refresh_all_versions(select=version_id)
        if action == "repair":
            QTimer.singleShot(0, lambda: self.repair_version(version_id))
        elif action == "delete":
            QTimer.singleShot(0, lambda: self.delete_version(version_id))
        else:
            self.toast("版本设置已保存", "success")

    # ------------------------------------------------------------------ modrinth
    def install_modrinth_project(self, hit: dict[str, Any], project_type: str,
                                 card: Any = None, page: Any = None,
                                 selected_version: dict[str, Any] | None = None) -> None:
        version = self.current_version()
        if project_type != "modpack" and not version:
            self.toast("请先选择一个已安装版本", "warning")
            if card:
                card.set_installing(False)
            return
        if project_type == "mod" and version and not version.loaders:
            if not ConfirmDialog.ask(
                    self, self.theme, "当前版本没有加载器",
                    "这个版本是原版，没有安装 Fabric / Forge / NeoForge。"
                    "放入模组后游戏可能无法启动。仍要下载吗？", "仍然下载"):
                if card:
                    card.set_installing(False)
                return
        base_version = version.base_version if version else self.latest_release
        loader = ""
        if project_type == "mod" and version and version.loaders:
            loader = version.primary_loader
        if card:
            card.set_installing(True, "查询版本…")
        if project_type == "modpack":
            self._install_modpack_project(
                hit, base_version, card, selected_version=selected_version)
            return
        if project_type in {"resourcepack", "shader"}:
            directory = (self.minecraft.resourcepacks_dir(version, create=True)
                         if project_type == "resourcepack"
                         else self.minecraft.shaderpacks_dir(version, create=True))
        else:
            directory = self.minecraft.mods_dir(version, create=True)
        game_version = base_version
        destination_dir = directory

        def job(progress, cancel):
            progress((6, "查询兼容文件…"))
            if selected_version:
                chosen = selected_version
            else:
                versions = self.modrinth.versions(
                    str(hit.get("project_id") or hit.get("slug")), game_version=game_version,
                    loader=loader)
                chosen = self.modrinth.pick_version(versions)
            if not chosen:
                suffix = f"（{game_version}" + (f" · {loader}" if loader else "") + "）"
                raise LauncherError(
                    f"Modrinth 上没有适用于当前版本{suffix}的{ModrinthService.project_type_label(project_type)}文件。")
            file = self.modrinth.primary_file(chosen)
            if not file:
                raise LauncherError("这个项目版本没有可下载文件。")
            filename = Path(str(file.get("filename") or "download.bin")).name
            target = destination_dir / filename
            progress((22, f"下载 {filename}…"))

            def dl(done, total):
                pct = 22 + int(done / max(1, total) * 70)
                progress((pct, f"下载 {filename} · {format_size(done)} / {format_size(total)}"))

            from ..utils import download_file
            download_file(
                self.modrinth.file_urls(file), target,
                sha1=(file.get("hashes") or {}).get("sha1"),
                size=file.get("size") or None, progress=dl, cancel=cancel,
                context=filename,
            )
            progress((96, "写入来源记录…"))
            if project_type == "mod" and version:
                record_source(version, target.name, hit, chosen, file)
            return {"target": target, "version": chosen, "file": file, "filename": filename, "project": hit}

        def finish(result: object) -> None:
            data = result if isinstance(result, dict) else {}
            title = str(hit.get("title") or "内容")
            self.toast(f"{title} 已放入{'模组' if project_type == 'mod' else '资源目录'}", "success")
            if card:
                card.set_installing(False)
            if project_type == "mod":
                self.content_page.refresh()
            else:
                self.content_page.refresh()
            self.refresh_home_stats()

        def fail(message: str) -> None:
            if card:
                card.set_installing(False)
            self.toast(message, "error", 6000)

        run_task(self, job, on_done=finish, on_error=fail, name="modrinth-install")

    def install_curseforge_project(self, hit: dict[str, Any], project_type: str,
                                   card: Any = None, page: Any = None,
                                   selected_file: dict[str, Any] | None = None) -> None:
        if project_type == "modpack":
            self.toast("CurseForge 整合包请先打开项目页下载；当前自动安装支持模组/资源包/光影。",
                       "warning", 6000)
            if card:
                card.set_installing(False)
            return
        version = self.current_version()
        if not version:
            self.toast("请先选择一个已安装版本", "warning")
            if card:
                card.set_installing(False)
            return
        if not self.curseforge.enabled:
            self.toast("CurseForge 需要 API Key：设置 → 下载与网络 中填写。", "warning", 7000)
            if card:
                card.set_installing(False)
            return
        if project_type == "mod" and not version.loaders:
            if not ConfirmDialog.ask(
                    self, self.theme, "当前版本没有加载器",
                    "这个版本是原版，没有安装 Fabric / Forge / NeoForge。放入模组后游戏可能无法启动。仍要下载吗？",
                    "仍然下载"):
                if card:
                    card.set_installing(False)
                return
        if project_type == "resourcepack":
            directory = self.minecraft.resourcepacks_dir(version, create=True)
        elif project_type == "shader":
            directory = self.minecraft.shaderpacks_dir(version, create=True)
        else:
            directory = self.minecraft.mods_dir(version, create=True)
        loader = version.primary_loader if project_type == "mod" and version.loaders else ""
        if card:
            card.set_installing(True, "查询版本…")

        def job(progress, cancel):
            progress((6, "查询 CurseForge 文件…"))
            if selected_file:
                chosen = selected_file
            else:
                files = self.curseforge.files(
                    str(hit.get("project_id") or ""),
                    game_version=version.base_version, loader=loader)
                chosen = self.curseforge.primary_file(files)
            if not chosen:
                suffix = f"（{version.base_version}" + (f" · {loader}" if loader else "") + "）"
                raise CurseForgeError(
                    f"CurseForge 上没有适用于当前版本{suffix}的"
                    f"{CurseForgeService.project_type_label(project_type)}文件。")
            filename = Path(str(chosen.get("fileName") or "download.bin")).name
            target = directory / filename
            progress((22, f"下载 {filename}…"))

            def dl(done, total):
                pct = 22 + int(done / max(1, total) * 70)
                progress((pct, f"下载 {filename} · {format_size(done)} / {format_size(total)}"))

            self.curseforge.download(chosen, target, progress=dl, cancel=cancel)
            return {"target": target, "filename": filename, "project": hit}

        def finish(result: object) -> None:
            data = result if isinstance(result, dict) else {}
            title = str(hit.get("title") or "内容")
            self.toast(f"{title} 已放入“模组”" if project_type == "mod"
                       else f"{title} 已放入资源目录", "success")
            if card:
                card.set_installing(False)
            self.content_page.refresh()
            self.refresh_home_stats()

        def fail(message: str) -> None:
            if card:
                card.set_installing(False)
            self.toast(message, "error", 7000)

        run_task(self, job, on_done=finish, on_error=fail, name="curseforge-install")

    def _install_modpack_project(self, hit: dict[str, Any], base_version: str,
                                 card: Any = None,
                                 selected_version: dict[str, Any] | None = None) -> None:
        java = self.java_for_install(base_version)
        java_path = str(java.path) if java else ""
        project_id = str(hit.get("project_id") or hit.get("slug") or "")
        target_path = cache_dir() / "downloads" / f"{project_id}.mrpack"

        def job(progress, cancel):
            progress((4, "查询整合包版本…"))
            if selected_version:
                chosen = selected_version
            else:
                versions = self.modrinth.versions(project_id, game_version=base_version)
                chosen = self.modrinth.pick_version(versions)
            if not chosen:
                raise LauncherError(f"没有适用于 Minecraft {base_version} 的整合包文件。")
            file = self.modrinth.primary_file(chosen)
            if not file:
                raise LauncherError("整合包没有可下载文件。")
            target_path.parent.mkdir(parents=True, exist_ok=True)
            progress((10, "下载整合包…"))

            def dl(done, total):
                progress((10 + int(done / max(1, total) * 20),
                          f"下载整合包 · {format_size(done)} / {format_size(total)}"))

            from ..utils import download_file
            download_file(
                self.modrinth.file_urls(file), target_path,
                sha1=(file.get("hashes") or {}).get("sha1"),
                size=file.get("size") or None, progress=dl, cancel=cancel,
                context="整合包",
            )
            progress((33, "安装整合包依赖与文件…"))

            def pack_progress(pct, message):
                progress((33 + int(pct * 0.65), message))

            def pack_log(message):
                progress((60, message))

            return self.modpack.import_modpack(
                target_path, java_path=java_path, progress=pack_progress,
                log=pack_log, cancel=cancel,
            )

        dialog = ProgressJobDialog(
            self.theme, f"安装整合包 · {hit.get('title', 'Modrinth')}",
            f"目标版本 {base_version} · 请保持启动器开启",
            job, success_text="整合包已停靠进版本。", width=720, height=540, parent=self)
        accepted = dialog.run()
        if card:
            card.set_installing(False)
        if accepted == QDialog.DialogCode.Accepted:
            installed = dialog.result_object
            installed_id = getattr(installed, "id", "")
            self.refresh_all_versions(select=installed_id or None)
            self.toast("整合包导入完成", "success")
        else:
            self.toast("整合包安装没有完成", "info")

    def _choose_java_for_modpack(self, base_version: str) -> str:
        java = self.java_for_install(base_version)
        return str(java.path) if java else ""

    def import_local_modpack(self, path: Path) -> None:
        path = Path(path)
        if not path.exists():
            self.toast("整合包文件不存在", "error")
            return
        # Read only the dependency metadata up front so the Java selection and
        # user-facing label match the pack.
        base_version = self.current_version().base_version if self.current_version() else self.latest_release
        pack_title = path.stem
        try:
            import zipfile
            with zipfile.ZipFile(path) as archive:
                if "modrinth.index.json" in archive.namelist():
                    import json
                    data = json.loads(archive.read("modrinth.index.json").decode("utf-8-sig"))
                    deps = data.get("dependencies") or {}
                    base_version = str(deps.get("minecraft") or base_version)
                    pack_title = str(data.get("name") or pack_title)
        except Exception:
            pass
        java = self.java_for_install(base_version)
        java_path = str(java.path) if java else ""

        def job(progress, cancel):
            state = {"percent": 2}
            def report(pct, message):
                state["percent"] = pct
                progress((pct, message))
            def log(message):
                progress((state["percent"], message))
            return self.modpack.import_modpack(
                path, java_path=java_path, progress=report, log=log, cancel=cancel)

        dialog = ProgressJobDialog(
            self.theme, "导入整合包", f"{pack_title} · 目标 Minecraft {base_version}",
            job, success_text="整合包已靠港，可在版本管理中查看。",
            width=740, height=550, parent=self)
        if dialog.run() == QDialog.DialogCode.Accepted:
            installed = dialog.result_object
            installed_id = getattr(installed, "id", "")
            self.refresh_all_versions(select=installed_id or None)
            self.toast("整合包导入完成", "success")
        else:
            self.toast("整合包导入没有完成", "info")

    # ------------------------------------------------------------------ accounts
    def open_accounts(self) -> None:
        dialog = AccountDialog(self.theme, self.settings, self.microsoft, self)
        dialog.accounts_changed.connect(self._accounts_changed)
        dialog.exec()
        self._accounts_changed()

    def _accounts_changed(self) -> None:
        self._update_account_chip()
        if hasattr(self, "home_page"):
            self.home_page.refresh_account()
        if hasattr(self, "settings_page"):
            self.settings_page.refresh_accounts()

    def _update_account_chip(self) -> None:
        if not hasattr(self, "titlebar"):
            return
        account = self.settings.active_account() or {}
        name = str(account.get("name") or "未登录")
        self.titlebar.account_name.setText(name)
        self.titlebar.account_type.setText("微软正版" if account.get("type") == "microsoft" else "离线玩家")
        self.titlebar.account_avatar.set_account(account)

    def refresh_active_account(self) -> None:
        account = self.settings.active_account()
        if not account:
            return
        if account.get("type") != "microsoft":
            self.toast("当前是离线玩家，无需刷新微软登录", "info")
            return
        self.toast("正在刷新微软令牌…", "info")

        def job(progress, cancel):
            return self.microsoft.refresh_account(account)

        def done(result: object) -> None:
            if isinstance(result, dict):
                self.settings.update_account(result)
                self.settings.set_active_account(result["id"])
                self._accounts_changed()
                self.toast(f"已刷新微软正版：{result.get('name')}", "success")
            else:
                self.toast("玩家刷新没有返回有效结果", "warning")

        def fail(message: str) -> None:
            self.toast(f"微软登录刷新失败：{message}", "error", 6000)

        run_task(self, job, on_done=done, on_error=fail, name="ms-refresh")

    # ------------------------------------------------------------------ theme
    def apply_theme(self, theme_id: str) -> None:
        if theme_id not in THEMES:
            return
        self.theme_id = theme_id
        self.theme = self.theme_manager.set_theme(theme_id)
        self.settings.set("theme", theme_id)
        self.shell_root.set_theme(self.theme)
        self.titlebar.update_theme(self.theme)
        for page in (self.home_page, self.download_page, self.versions_page,
                     self.content_page, self.settings_page):
            try:
                page.theme = self.theme
                page.set_theme(self.theme)
            except Exception as exc:
                self.toast(f"刷新页面主题失败：{exc}", "error")
        self._apply_theme_to_chrome()
        self._refresh_all_icons()
        self._update_account_chip()
        self.settings_page.refresh_theme_gallery()
        self.toast(f"已切换主题：{self.theme.name}", "success", 2200)

    def _apply_theme_to_chrome(self) -> None:
        t = self.theme
        self.footer_status.setStyleSheet(f"color: {t.text_faint}; font-size: 9.5px;")
        self.footer_source.setStyleSheet(
            f"color: {t.text_faint}; font-size: 8px; letter-spacing: 1px;")
        for child in self.footer.findChildren(QLabel):
            if child not in (self.footer_status, self.footer_source):
                child.setStyleSheet(f"color: {t.text_faint}; font-size: 9px;")
        self.footer.setStyleSheet(
            f"QFrame#FooterBar {{ background: transparent; border-top: 1px solid {t.border_soft}; }}")
        self.footer_dot.setPixmap(icons.pixmap("check_circle", t.success, 10))

    def _refresh_all_icons(self) -> None:
        for page in self.pages.values():
            refresh_theme_widgets(page, self.theme)
        refresh_theme_widgets(self.titlebar, self.theme)
        refresh_theme_widgets(self.footer, self.theme)
        refresh_widget_icons(self, self.theme)
        title_icon_color = self.theme.on_accent
        self.titlebar.minimize_button.setIcon(icons.icon("minimize", title_icon_color, 14))
        self.titlebar.maximize_button.setIcon(
            icons.icon("restore" if self.isMaximized() else "maximize", title_icon_color, 13))
        self.titlebar.close_button.setIcon(icons.icon("close", title_icon_color, 14))
        self._refresh_nav_styles()
        self.toast_host.set_theme(self.theme)

    # ------------------------------------------------------------------ data source
    def _load_manifest_silent(self) -> None:
        if self._manifest_loading:
            return
        self._manifest_loading = True

        def job(progress, cancel):
            manifest = self.mojang.get_manifest()
            latest = manifest.get("latest") or {}
            return [v for v in manifest.get("versions", []) if isinstance(v, dict)], latest

        def done(result: object) -> None:
            self._manifest_loading = False
            if isinstance(result, tuple):
                versions, latest = result
                self.manifest_versions = versions
                self.latest_release = str((latest or {}).get("release") or "")
                if hasattr(self, "home_page"):
                    self.home_page.refresh_news()
                if hasattr(self, "download_page"):
                    self.download_page.game_tab.latest_id.setText(self.latest_release or "未知版本")
                self._update_footer()

        def fail(message: str) -> None:
            self._manifest_loading = False
            self.latest_release = ""
            if hasattr(self, "home_page"):
                self.home_page.refresh_news()
            self._set_footer("下载线离线 · 可使用已安装版本")

        run_task(self, job, on_done=done, on_error=fail, name="manifest-bootstrap")

    def _manifest_page_ready(self) -> None:
        if hasattr(self.download_page, "game_tab"):
            latest = self.download_page.game_tab.latest_id.text()
            if latest and latest != "读取中…":
                self.latest_release = latest
                if hasattr(self, "home_page"):
                    self.home_page.refresh_news()

    def _set_footer(self, text: str) -> None:
        if hasattr(self, "footer_status"):
            self.footer_status.setText(text)

    def _update_footer(self) -> None:
        if not hasattr(self, "footer_status"):
            return
        count = len(self.versions)
        java_count = len(self.java_list)
        state = "正在运行" if self.game_session and self.game_session.running else "已就绪"
        self._set_footer(f"{state} · {count} 个版本 · {java_count} 个 Java")
        self._apply_theme_to_chrome()

    # ------------------------------------------------------------------ close
    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self.game_session and self.game_session.running:
            if not ConfirmDialog.ask(
                    self, self.theme, "游戏仍在运行",
                    "关闭启动器不会结束正在运行的 Minecraft。确定要退出启动器吗？",
                    "退出启动器"):
                event.ignore()
                return
        try:
            self.settings.set("window_geometry", bytes(self.saveGeometry().toBase64()).decode("ascii"))
            self.settings.set("launcher_window_width", self.width(), save=False)
            self.settings.set("launcher_window_height", self.height(), save=False)
            self.settings.save()
        except OSError:
            pass
        if self.game_console:
            self.game_console.hide()
        shutdown_tasks()
        event.accept()
