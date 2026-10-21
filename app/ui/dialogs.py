from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import QPoint, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QGuiApplication, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QSlider, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)
from PyQt6.QtCore import QUrl

from ..constants import APP_AUTHOR, APP_NAME, APP_VERSION
from ..core import skins as skin_model
from ..core.java import JavaInfo, JavaManager
from ..core.launch import GameSession, LaunchPlan
from ..core.minecraft import InstalledVersion, MinecraftManager
from ..services.microsoft import MicrosoftAuth, MicrosoftAuthError
from ..services.oauth_config import ensure_local_oauth
from ..storage import Settings, offline_uuid, valid_player_name
from ..utils import LauncherError, format_size, open_path
from . import icons
from .tasks import TaskThread, run_task
from .theme import Theme
from .widgets import (
    Avatar, Card, GearSpinner, InfoBanner, Pill, SectionCard, SegmentedControl,
    SettingRow, icon_button, label, make_button, repolish,
)


class BaseDialog(QDialog):
    def __init__(self, theme: Theme, title: str, subtitle: str = "",
                 width: int = 680, height: int = 520, icon_name: str = "info",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setModal(True)
        self.setMinimumSize(min(width, 520), min(height, 360))
        self.resize(width, height)
        self._drag_pos: QPoint | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.surface = QFrame()
        self.surface.setObjectName("DialogSurface")
        self.surface.setStyleSheet(
            f"QFrame#DialogSurface {{ background: {theme.surface}; border: none;"
            f" border-radius: 8px; }}")
        root.addWidget(self.surface)
        surface_layout = QVBoxLayout(self.surface)
        surface_layout.setContentsMargins(0, 0, 0, 0)
        surface_layout.setSpacing(0)

        self.header = QFrame()
        self.header.setFixedHeight(56)
        self.header.setCursor(Qt.CursorShape.ArrowCursor)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(20, 10, 12, 8)
        header_layout.setSpacing(10)
        icon_frame = QFrame()
        icon_frame.setFixedSize(28, 28)
        icon_frame.setStyleSheet(
            f"QFrame {{ background: {theme.surface_alt}; border: none; border-radius: 6px; }}")
        icon_layout = QHBoxLayout(icon_frame)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon_label = QLabel()
        icon_label.setPixmap(icons.pixmap(icon_name, theme.accent, 16))
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_layout.addWidget(icon_label)
        header_layout.addWidget(icon_frame)
        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(
            f"color: {theme.text}; font-size: 14px; font-weight: 600;")
        text_col.addWidget(self.title_label)
        if subtitle:
            self.subtitle_label = QLabel(subtitle)
            self.subtitle_label.setStyleSheet(f"color: {theme.text_muted}; font-size: 11px;")
            text_col.addWidget(self.subtitle_label)
        header_layout.addLayout(text_col, 1)
        self.minimize_button = icon_button("minimize", "最小化", icon_role="muted", size=14)
        self.minimize_button.clicked.connect(self.showMinimized)
        header_layout.addWidget(self.minimize_button)
        self.close_button = icon_button("close", "关闭", icon_role="muted", size=14)
        self.close_button.clicked.connect(self.reject)
        header_layout.addWidget(self.close_button)
        surface_layout.addWidget(self.header)
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {theme.border_soft};")
        surface_layout.addWidget(divider)

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(20, 16, 20, 16)
        self.body_layout.setSpacing(12)
        surface_layout.addWidget(self.body, 1)
        self.footer = QHBoxLayout()
        self.footer.setSpacing(8)
        self.footer.setContentsMargins(20, 0, 20, 16)
        self.footer.addStretch()
        surface_layout.addLayout(self.footer)
        self.header.installEventFilter(self)

    def done(self, result: int) -> None:  # type: ignore[override]
        # Detach the header filter before the C++ widget can outlive this
        # Python wrapper (otherwise a later mouse event can call eventFilter
        # on an object whose __dict__ has already been cleared).
        header = getattr(self, "header", None)
        if header is not None:
            header.removeEventFilter(self)
        super().done(result)

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        # The header filter can outlive the Python wrapper when a dialog is
        # closed and its C++ child is still alive.  Never assume __dict__ is
        # intact here; this exact path produced
        # "'AccountDialog' object has no attribute 'header'".
        header = getattr(self, "header", None)
        close_button = getattr(self, "close_button", None)
        if header is not None and watched is header:
            if event.type() == event.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                if close_button is None or not close_button.geometry().contains(
                        event.position().toPoint()):
                    handle = self.windowHandle()
                    if handle:
                        handle.startSystemMove()
                        return True
            if event.type() == event.Type.MouseButtonDblClick:
                return True
        return super().eventFilter(watched, event)

    def add_footer_button(self, text: str, role: str = "secondary",
                          icon_name: str = "", callback: Callable | None = None) -> QPushButton:
        button = make_button(text, role, icon_name or None,
                            "accent" if role != "secondary" else "muted")
        if callback:
            button.clicked.connect(callback)
        self.footer.addWidget(button)
        return button

    def set_busy(self, busy: bool) -> None:
        if getattr(self, "close_button", None) is not None:
            self.close_button.setEnabled(not busy)


class ConfirmDialog(BaseDialog):
    def __init__(self, theme: Theme, title: str, message: str, *,
                 confirm_text: str = "确认", cancel_text: str = "取消",
                 danger: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(theme, title, "请确认这次操作", width=480, height=290,
                         icon_name="alert", parent=parent)
        self.body_layout.setContentsMargins(24, 22, 24, 8)
        self.message = label(message, "muted", wrap=True)
        self.message.setMinimumHeight(58)
        self.body_layout.addWidget(self.message)
        self.body_layout.addStretch()
        cancel = self.add_footer_button(cancel_text, "secondary", callback=self.reject)
        confirm = self.add_footer_button(confirm_text, "danger" if danger else "primary",
                                         callback=self.accept)
        confirm.setMinimumWidth(96)

    @classmethod
    def ask(cls, parent: QWidget | None, theme: Theme, title: str, message: str,
            confirm_text: str = "确认", danger: bool = False) -> bool:
        dialog = cls(theme, title, message, confirm_text=confirm_text, danger=danger, parent=parent)
        return dialog.exec() == QDialog.DialogCode.Accepted


class OfflineLaunchNotice(BaseDialog):
    """A dismissible offline note shown only when no Microsoft account exists.

    Closing the dialog with the title-bar X (or Esc) behaves like
    "continue offline": the note is informational and must not trap the
    player. Choosing the account action opens the account manager instead.
    """

    def __init__(self, theme: Theme, player_name: str, parent: QWidget | None = None) -> None:
        super().__init__(theme, "离线启动提示", "",
                         width=590, height=360, icon_name="shield", parent=parent)
        self.action = "offline"
        self.body_layout.setContentsMargins(22, 18, 22, 12)
        self.body_layout.setSpacing(11)
        self.body_layout.addWidget(label(f"当前玩家：{player_name}", "section"))
        self.body_layout.addWidget(label(
            "你正在使用离线玩家启动 Minecraft。离线模式不会验证 Mojang 账号，"
            "仅适合单人游戏或未开启正版验证的服务器。",
            "muted", wrap=True))
        warning = InfoBanner(
            "离线玩家不会获得正版皮肤与 Realms 权限。可以继续离线启动，"
            "也可以打开玩家管理切换微软账号。", "warning", "alert")
        self.body_layout.addWidget(warning)
        self.body_layout.addStretch()
        self.continue_button = self.add_footer_button(
            "继续离线启动", "secondary", "play", self._continue_offline)
        self.login_button = self.add_footer_button(
            "打开玩家管理", "primary", "shield", self._login)
        self.login_button.setDefault(True)

    def _continue_offline(self) -> None:
        self.action = "offline"
        self.accept()

    def _login(self) -> None:
        self.action = "login"
        self.accept()


class ProgressJobDialog(BaseDialog):
    """Modal background job with a progress bar and streaming status log."""
    completed = pyqtSignal(object)

    def __init__(self, theme: Theme, title: str, subtitle: str,
                 job: Callable[[Callable[[object], None], Callable[[], bool]], Any],
                 *, success_text: str = "操作完成", width: int = 660, height: int = 500,
                 parent: QWidget | None = None) -> None:
        super().__init__(theme, title, subtitle, width=width, height=height,
                         icon_name="gear", parent=parent)
        self.job = job
        self.success_text = success_text
        self.thread: TaskThread | None = None
        self.result_object: object | None = None
        self._done = False
        self._last_message = ""
        top = QHBoxLayout()
        top.setSpacing(10)
        self.spinner = GearSpinner(18)
        top.addWidget(self.spinner)
        self.status = label("正在准备…", "cardTitle")
        top.addWidget(self.status, 1)
        self.percent = label("0%", "mono")
        top.addWidget(self.percent)
        self.body_layout.addLayout(top)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.body_layout.addWidget(self.progress)
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setPlaceholderText("这里会显示下载与安装进度…")
        self.log_box.setStyleSheet(
            f"QPlainTextEdit {{ background: {theme.input}; border: 1px solid {theme.border_soft};"
            f" border-radius: 10px; padding: 10px; font-family: 'Cascadia Mono', Consolas;"
            f" font-size: 11px; color: {theme.text_muted}; }}")
        self.body_layout.addWidget(self.log_box, 1)
        self.cancel_button = self.add_footer_button("取消", "secondary", callback=self.cancel_job)
        self.done_button = self.add_footer_button("关闭", "primary", callback=self.reject)
        self.done_button.hide()
        self.finished.connect(self._on_finished)

    def _start_task(self) -> None:
        if self.thread and self.thread.isRunning():
            return
        self.close_button.setEnabled(False)
        self.thread = run_task(
            self, self.job,
            on_progress=self._progress,
            on_done=self._completed,
            on_error=self._error,
            on_cancelled=self._cancelled,
            name="progress-dialog",
        )

    def start(self) -> None:
        self.show()
        self._start_task()

    def run(self) -> int:
        """Start the job in the modal event loop and wait for the result."""
        QTimer.singleShot(0, self._start_task)
        return self.exec()

    def _append(self, text: str) -> None:
        if not text:
            return
        text = text.strip()
        if not text or text == self._last_message:
            return
        self._last_message = text
        self.log_box.appendPlainText(text)
        cursor = self.log_box.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.log_box.setTextCursor(cursor)

    def _progress(self, value: object) -> None:
        if isinstance(value, tuple) and len(value) >= 2:
            percent, message = value[0], str(value[1])
        elif isinstance(value, dict):
            percent = value.get("percent", self.progress.value())
            message = str(value.get("message") or value.get("status") or "")
        elif isinstance(value, (int, float)):
            percent, message = int(value), ""
        else:
            percent, message = self.progress.value(), str(value)
        try:
            self.progress.setValue(max(0, min(100, int(percent))))
            self.percent.setText(f"{self.progress.value()}%")
        except (TypeError, ValueError):
            pass
        if message:
            self.status.setText(message[:100])
            self._append(message)

    def _completed(self, result: object) -> None:
        self._done = True
        self.result_object = result
        self.close_button.setEnabled(True)
        self.progress.setValue(100)
        self.percent.setText("100%")
        self.status.setText(self.success_text)
        self.spinner.hide()
        self._append(self.success_text)
        self.cancel_button.hide()
        self.done_button.show()
        self.done_button.clicked.disconnect()
        self.done_button.clicked.connect(self.accept)
        self.completed.emit(result)

    def _error(self, message: str) -> None:
        self._done = True
        self.close_button.setEnabled(True)
        self.status.setText("操作失败")
        self.spinner.hide()
        self._append(message)
        self.progress.setValue(0)
        self.cancel_button.hide()
        self.done_button.show()
        self.done_button.setText("关闭")
        self.progress.setStyleSheet(
            f"QProgressBar::chunk {{ background: {self.theme.danger}; border-radius: 4px; }}")

    def _cancelled(self) -> None:
        self._done = True
        self.close_button.setEnabled(True)
        self.status.setText("已取消")
        self.spinner.hide()
        self._append("操作已取消。")
        self.cancel_button.hide()
        self.done_button.show()
        self.progress.setValue(0)

    def cancel_job(self) -> None:
        if self.thread and self.thread.isRunning():
            self.status.setText("正在取消…")
            self.cancel_button.setEnabled(False)
            self.thread.cancel()

    def reject(self) -> None:
        if self.thread and self.thread.isRunning() and not self._done:
            self.cancel_job()
            return
        super().reject()

    def _on_finished(self) -> None:
        if self.thread and self.thread.isRunning():
            self.thread.cancel()
            self.thread.wait(2500)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self.thread and self.thread.isRunning() and not self._done:
            self.cancel_job()
            event.ignore()
            return
        super().closeEvent(event)


class InstallDialog(ProgressJobDialog):
    def __init__(self, theme: Theme, installer: Any, version_id: str, *,
                 loader: str = "vanilla", loader_version: str = "", java_path: str = "",
                 parent: QWidget | None = None) -> None:
        names = {"vanilla": "原版", "fabric": "Fabric", "quilt": "Quilt",
                 "forge": "Forge", "neoforge": "NeoForge"}
        title = f"安装 Minecraft {version_id}"
        subtitle = f"{names.get(loader, loader)} · 请保持启动器开启"
        def job(progress, cancel):
            state = {"percent": 0}
            def report(percent, message):
                state["percent"] = percent
                progress((percent, message))
            def log(message):
                progress((state["percent"], message))
            return installer.install(
                version_id, loader=loader, loader_version=loader_version,
                java_path=java_path, progress=report, log=log, cancel=cancel,
            )
        super().__init__(theme, title, subtitle, job,
                         success_text="安装完成，已加入版本管理。",
                         width=700, height=520, parent=parent)


class AccountDialog(BaseDialog):
    accounts_changed = pyqtSignal()

    def __init__(self, theme: Theme, settings: Settings, auth: MicrosoftAuth,
                 parent: QWidget | None = None) -> None:
        super().__init__(theme, "玩家管理", "离线与微软正版玩家都在本机管理", width=910,
                         height=700, icon_name="user", parent=parent)
        self.settings = settings
        self.auth = auth
        self.selected_id = (settings.active_account() or {}).get("id", "")
        self.editing_id = ""
        self._creating = False
        self._custom_skin = False
        self._custom_skin_source: Path | None = None
        self._default_model_manual = False
        self.login_thread: TaskThread | None = None
        self.device_flow: dict[str, Any] = {}
        self._device_login_url = ""
        self._device_opened = False
        self._login_config = ensure_local_oauth(settings)
        self._build()
        self._refresh_roster()
        selected = next((a for a in settings.accounts() if a.get("id") == self.selected_id), None)
        if selected and selected.get("type") == "offline":
            self._load_offline_account(selected)
        else:
            self._clear_offline_form()

    def _build(self) -> None:
        content = QHBoxLayout()
        content.setSpacing(18)
        left = QVBoxLayout()
        left.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(label("玩家列表", "section"), 1)
        add_button = icon_button("plus", "新增离线玩家", icon_role="accent", size=16)
        add_button.clicked.connect(self._new_account_mode)
        head.addWidget(add_button)
        left.addLayout(head)
        left.addWidget(label("点击玩家即可切换当前玩家。", "faint", wrap=True))
        self.roster_scroll = QScrollArea()
        self.roster_scroll.setWidgetResizable(True)
        self.roster_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.roster_host = QWidget()
        self.roster_layout = QVBoxLayout(self.roster_host)
        self.roster_layout.setContentsMargins(0, 0, 4, 0)
        self.roster_layout.setSpacing(7)
        self.roster_layout.addStretch()
        self.roster_scroll.setWidget(self.roster_host)
        left.addWidget(self.roster_scroll, 1)
        self.remove_button = make_button("移除所选玩家", "ghost", "trash", "danger", 14)
        self.remove_button.clicked.connect(self._remove_selected)
        left.addWidget(self.remove_button)
        left_widget = QWidget()
        left_widget.setFixedWidth(270)
        left_widget.setLayout(left)
        content.addWidget(left_widget)
        divider = QFrame()
        divider.setFixedWidth(1)
        divider.setStyleSheet(f"background: {self.theme.border_soft};")
        content.addWidget(divider)
        right = QVBoxLayout()
        right.setSpacing(12)
        self.mode_tabs = QWidget()
        tabs_layout = QHBoxLayout(self.mode_tabs)
        tabs_layout.setContentsMargins(0, 0, 0, 0)
        tabs_layout.setSpacing(7)
        self.offline_tab = make_button("离线玩家", "segment", "user", "muted", 15)
        self.ms_tab = make_button("微软正版", "segment", "globe", "muted", 15)
        for button in (self.offline_tab, self.ms_tab):
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            tabs_layout.addWidget(button)
        tabs_layout.addStretch()
        self.offline_tab.clicked.connect(lambda: self._set_mode("offline"))
        self.ms_tab.clicked.connect(lambda: self._set_mode("microsoft"))
        right.addWidget(self.mode_tabs)
        self.stack = QStackedWidget()
        self.offline_page = self._build_offline_page()
        self.ms_page = self._build_ms_page()
        self.stack.addWidget(self.offline_page)
        self.stack.addWidget(self.ms_page)
        right.addWidget(self.stack, 1)
        content.addLayout(right, 1)
        self.body_layout.addLayout(content, 1)
        self.add_footer_button("关闭", "secondary", callback=self.accept)
        self._set_mode("offline")

    def _build_offline_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(label("使用离线玩家", "section"))
        layout.addWidget(label(
            "离线玩家不需要网络，可用于单人游戏与未开启正版验证的服务器。"
            "玩家名将被写入本地 UUID，不会上传到 Mojang。", "muted", wrap=True))
        banner = InfoBanner(
            "离线模式以本地皮肤显示头像；不会改变服务器或其他玩家看到的皮肤。",
            "info", "shield")
        layout.addWidget(banner)

        row = QHBoxLayout()
        row.setSpacing(18)
        form = QVBoxLayout()
        form.setSpacing(7)
        form.addWidget(label("玩家名", "cardTitle"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("3-16 位字母、数字或下划线")
        self.name_edit.setMaxLength(16)
        self.name_edit.textChanged.connect(self._validate_name)
        form.addWidget(self.name_edit)
        self.name_hint = label("使用英文、数字或下划线，例如 Player。", "faint", wrap=True)
        form.addWidget(self.name_hint)

        form.addWidget(label("皮肤", "cardTitle"))
        self.skin_status = label("默认皮肤", "faint", wrap=True)
        form.addWidget(self.skin_status)
        self.skin_model_control = SegmentedControl(
            [("classic", "Steve 经典"), ("slim", "Alex 纤细")], "classic")
        self.skin_model_control.setToolTip(
            "默认皮肤也可以手动选择 Steve/Alex；选择自定义皮肤后可指定模型。")
        self.skin_model_control.changed.connect(self._skin_model_changed)
        form.addWidget(self.skin_model_control)
        skin_row = QHBoxLayout()
        skin_row.setSpacing(7)
        self.choose_skin_button = make_button(
            "选择皮肤图片…", "secondary", "image", "muted", 14)
        self.choose_skin_button.clicked.connect(self._choose_skin)
        skin_row.addWidget(self.choose_skin_button)
        self.clear_skin_button = make_button(
            "恢复默认皮肤", "ghost", "refresh", "accent", 14)
        self.clear_skin_button.clicked.connect(self._clear_custom_skin)
        skin_row.addWidget(self.clear_skin_button)
        skin_row.addStretch()
        form.addLayout(skin_row)
        form.addWidget(label(
            "默认皮肤可手动选择 Steve/Alex；自定义 PNG 支持 64×64 或 64×32。",
            "faint", wrap=True))
        form.addStretch()

        form_host = QWidget()
        form_host.setLayout(form)
        row.addWidget(form_host, 1)

        preview_col = QVBoxLayout()
        preview_col.setSpacing(5)
        self.avatar_preview = Avatar("Player", 96)
        preview_col.addWidget(self.avatar_preview, 0, Qt.AlignmentFlag.AlignHCenter)
        preview_col.addWidget(label("2D 头像正面", "faint"), 0, Qt.AlignmentFlag.AlignHCenter)
        preview_col.addStretch()
        row.addLayout(preview_col)
        layout.addLayout(row, 1)

        self.offline_submit = make_button("添加离线玩家", "primary", "user", "on_accent", 17)
        self.offline_submit.clicked.connect(self._create_offline)
        layout.addWidget(self.offline_submit, 0, Qt.AlignmentFlag.AlignLeft)
        return page

    def _build_ms_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(11)
        layout.addWidget(label("微软正版登录", "section"))
        layout.addWidget(label(
            "点击一次，在浏览器中完成微软账户登录。启动器不会接触密码，"
            "也不需要安装 Azure 工具或填写重定向地址；登录后自动读取正版皮肤与 CLASSIC/SLIM 模型。",
            "muted", wrap=True))

        self.ms_banner = InfoBanner("点击下方按钮生成一次性登录代码。", "info", "key")
        layout.addWidget(self.ms_banner)

        self.device_card = QFrame()
        self.device_card.setObjectName("SubCard")
        device_layout = QVBoxLayout(self.device_card)
        device_layout.setContentsMargins(16, 13, 16, 13)
        device_layout.setSpacing(6)
        device_layout.addWidget(label("在浏览器中输入代码", "faint"))
        code_row = QHBoxLayout()
        self.device_code = QLabel("—")
        self.device_code.setStyleSheet(
            f"color: {self.theme.accent_hi}; font-family: 'Cascadia Mono', Consolas;"
            f" font-size: 26px; font-weight: 700; letter-spacing: 4px;")
        code_row.addWidget(self.device_code, 1)
        self.copy_code_button = icon_button("copy", "复制代码", icon_role="accent", size=16)
        self.copy_code_button.clicked.connect(self._copy_code)
        code_row.addWidget(self.copy_code_button)
        device_layout.addLayout(code_row)
        self.verify_uri = label("", "muted", wrap=True)
        device_layout.addWidget(self.verify_uri)
        open_row = QHBoxLayout()
        self.open_login_button = make_button("打开微软登录页", "secondary", "external", "accent", 14)
        self.open_login_button.clicked.connect(self._open_verification)
        open_row.addWidget(self.open_login_button)
        open_row.addStretch()
        device_layout.addLayout(open_row)
        self.device_card.hide()
        layout.addWidget(self.device_card)

        status_row = QHBoxLayout()
        self.ms_spinner = GearSpinner(16)
        self.ms_spinner.hide()
        status_row.addWidget(self.ms_spinner)
        self.ms_status = label("", "faint", wrap=True)
        status_row.addWidget(self.ms_status, 1)
        layout.addLayout(status_row)
        layout.addStretch()

        button_row = QHBoxLayout()
        self.ms_submit = make_button("使用微软账户登录", "primary", "key", "on_accent", 17)
        self.ms_submit.clicked.connect(self._start_device_login)
        button_row.addWidget(self.ms_submit)
        self.ms_cancel = make_button("取消等待", "ghost", "close", "danger", 14)
        self.ms_cancel.clicked.connect(self._cancel_ms_login)
        self.ms_cancel.hide()
        button_row.addWidget(self.ms_cancel)
        button_row.addStretch()
        layout.addLayout(button_row)

        self._refresh_login_config()
        return page

    def _refresh_login_config(self) -> None:
        config = ensure_local_oauth(self.settings)
        self._login_config = config
        if config.configured:
            self.ms_submit.setEnabled(True)
        else:
            self.ms_submit.setEnabled(False)
            self.ms_status.setText(
                "登录应用未配置。完整分发版会在首次启动时自动生成 oauth.json。")

    def _set_mode(self, mode: str) -> None:
        is_offline = mode == "offline"
        self.stack.setCurrentIndex(0 if is_offline else 1)
        for button, active in ((self.offline_tab, is_offline), (self.ms_tab, not is_offline)):
            button.setProperty("active", "true" if active else "false")
            repolish(button)
        if not is_offline:
            self._refresh_login_config()

    def _new_account_mode(self) -> None:
        self._creating = True
        self.selected_id = ""
        self._refresh_roster()
        self._clear_offline_form()
        self.name_edit.setFocus()
        self._set_mode("offline")

    def _refresh_roster(self) -> None:
        while self.roster_layout.count() > 1:
            item = self.roster_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        active_id = str(self.settings.get("account_id") or "")
        for account in self.settings.accounts():
            row = self._account_row(account, account.get("id") == active_id)
            self.roster_layout.insertWidget(self.roster_layout.count() - 1, row)
        selected = next((a for a in self.settings.accounts() if a.get("id") == self.selected_id), None)
        if not selected and self.settings.accounts() and not getattr(self, "_creating", False):
            self.selected_id = self.settings.accounts()[0].get("id", "")
        self.remove_button.setEnabled(bool(self.selected_id))

    def _account_row(self, account: dict[str, Any], active: bool) -> QWidget:
        row = QFrame()
        row.setObjectName("CardAlt" if active else "SubCard")
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(9)
        avatar = Avatar(str(account.get("name") or "P"), 32)
        avatar.set_account(account)
        layout.addWidget(avatar)
        col = QVBoxLayout()
        col.setSpacing(1)
        name = QLabel(str(account.get("name") or "未命名"))
        name.setStyleSheet(f"color: {self.theme.text}; font-size: 12px; font-weight: 600;")
        col.addWidget(name)
        kind = "微软正版" if account.get("type") == "microsoft" else "离线玩家"
        meta = QLabel(kind + (" · 当前" if active else ""))
        meta.setStyleSheet(
            f"color: {self.theme.accent if active else self.theme.text_faint}; font-size: 10px;")
        col.addWidget(meta)
        layout.addLayout(col, 1)
        if active:
            dot = QLabel()
            dot.setPixmap(icons.pixmap("check_circle", self.theme.success, 14))
            layout.addWidget(dot)
        for child in row.findChildren(QWidget):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.mouseReleaseEvent = lambda event, aid=account.get("id"): self._select_account(aid)
        return row

    def _select_account(self, account_id: str) -> None:
        self._creating = False
        self.selected_id = account_id
        self.settings.set_active_account(account_id)
        self._refresh_roster()
        account = next((a for a in self.settings.accounts() if a.get("id") == account_id), None)
        if account and account.get("type") == "offline":
            self._load_offline_account(account)
        else:
            self._clear_offline_form()
        self.accounts_changed.emit()

    def _clear_offline_form(self) -> None:
        self.editing_id = ""
        self._creating = False
        self._custom_skin = False
        self._custom_skin_source = None
        self._default_model_manual = False
        self.name_edit.blockSignals(True)
        self.name_edit.clear()
        self.name_edit.blockSignals(False)
        self.name_hint.setText("使用英文、数字或下划线，例如 Player。")
        self.name_hint.setProperty("role", "faint")
        repolish(self.name_hint)
        self.offline_submit.setText("添加离线玩家")
        self._update_offline_preview()

    def _load_offline_account(self, account: dict[str, Any]) -> None:
        self._creating = False
        self.editing_id = str(account.get("id") or "")
        self.name_edit.blockSignals(True)
        self.name_edit.setText(str(account.get("name") or ""))
        self.name_edit.blockSignals(False)
        custom_path = skin_model.custom_skin_path(account)
        self._custom_skin = custom_path is not None
        self._custom_skin_source = custom_path
        chosen = skin_model.normalize_model(account.get("skin_model"))
        self._default_model_manual = bool(chosen)
        if chosen:
            self.skin_model_control.set_current(chosen)
        elif self._custom_skin:
            self.skin_model_control.set_current(self._current_offline_model())
        self.offline_submit.setText("保存玩家")
        self._update_offline_preview()

    def _current_offline_model(self) -> str:
        name = self.name_edit.text().strip()
        seed = offline_uuid(name) if valid_player_name(name) else (name or "Player")
        return skin_model.model_for_seed(seed)

    def _update_offline_preview(self) -> None:
        custom = bool(self._custom_skin and self._custom_skin_source)
        # Default skins are selectable too; only the "clear custom skin"
        # button depends on a chosen PNG.
        self.skin_model_control.setEnabled(True)
        self.clear_skin_button.setEnabled(custom)
        current = skin_model.normalize_model(self.skin_model_control.current())
        if not self._default_model_manual and not custom:
            current = self._current_offline_model()
            self.skin_model_control.set_current(current)
        elif not current:
            current = self._current_offline_model()
            self.skin_model_control.set_current(current)
        if custom:
            self.avatar_preview.set_skin_file(self._custom_skin_source, current)
            self.skin_status.setText(f"自定义皮肤 · {skin_model.MODEL_LABELS[current]}")
        else:
            name = self.name_edit.text().strip()
            if valid_player_name(name):
                temp = {
                    "type": "offline", "name": name,
                    "uuid": offline_uuid(name),
                    "skin_model": current if self._default_model_manual else "",
                }
                self.avatar_preview.set_account(temp)
                source = "手动选择" if self._default_model_manual else "由玩家 ID 自动选择"
                self.skin_status.setText(
                    f"默认皮肤 · {source}：{skin_model.MODEL_LABELS[current]}")
            else:
                self.avatar_preview.set_default_skin(current)
                self.skin_status.setText("默认皮肤 · 输入玩家名后自动选择 Steve/Alex 模型")

    def _choose_skin(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择皮肤图片", "", "Minecraft 皮肤 (*.png)")
        if not path:
            return
        from .skin_render import load_skin_pixmap
        pixmap = load_skin_pixmap(path)
        if pixmap.isNull():
            self.skin_status.setText("皮肤必须是 64×64 或 64×32 的 PNG 图片。")
            return
        self._custom_skin = True
        self._custom_skin_source = Path(path)
        self._default_model_manual = True
        if not self.skin_model_control.current():
            self.skin_model_control.set_current(self._current_offline_model())
        self._update_offline_preview()

    def _clear_custom_skin(self) -> None:
        self._custom_skin = False
        self._custom_skin_source = None
        self._default_model_manual = False
        self.skin_model_control.set_current(self._current_offline_model())
        self._update_offline_preview()

    def _skin_model_changed(self, model: str) -> None:
        self._default_model_manual = True
        self._update_offline_preview()

    def _validate_name(self, text: str) -> bool:
        cleaned = text.strip()
        if not cleaned:
            self.name_hint.setText("请输入玩家名。")
            self.name_hint.setProperty("role", "danger")
        elif not valid_player_name(cleaned):
            self.name_hint.setText("只能使用 3-16 位英文字母、数字或下划线。")
            self.name_hint.setProperty("role", "danger")
        else:
            self.name_hint.setText("这个名字会显示在游戏中。")
            self.name_hint.setProperty("role", "faint")
        repolish(self.name_hint)
        self.offline_submit.setEnabled(valid_player_name(cleaned))
        if hasattr(self, "avatar_preview"):
            self._update_offline_preview()
        return valid_player_name(cleaned)

    def _save_skin_fields(self, account: dict[str, Any]) -> bool:
        model = self.skin_model_control.current()
        if model not in skin_model.MODELS:
            model = self._current_offline_model()
        if not self._custom_skin or self._custom_skin_source is None:
            account["skin_file"] = ""
            account["skin_model"] = model if self._default_model_manual else ""
            return True
        try:
            account["skin_file"] = skin_model.copy_custom_skin(
                str(account.get("id") or "player"), self._custom_skin_source)
        except OSError as exc:
            self.name_hint.setText(f"皮肤文件保存失败：{exc}")
            self.name_hint.setProperty("role", "danger")
            repolish(self.name_hint)
            return False
        account["skin_model"] = model
        return True

    def _create_offline(self) -> None:
        name = self.name_edit.text().strip()
        if not valid_player_name(name):
            self._validate_name(name)
            return
        other = next((a for a in self.settings.accounts()
                      if a.get("type") == "offline"
                      and str(a.get("name", "")).lower() == name.lower()
                      and a.get("id") != self.editing_id), None)
        if other:
            self.name_hint.setText(f"已经存在同名玩家「{other.get('name')}」。")
            self.name_hint.setProperty("role", "danger")
            repolish(self.name_hint)
            return
        from ..storage import make_offline_account
        if self.editing_id:
            account = next((a for a in self.settings.accounts()
                            if a.get("id") == self.editing_id), None)
            if not account:
                self._clear_offline_form()
                return
            account = dict(account)
            old_name = str(account.get("name") or "")
            account["name"] = name
            if old_name != name:
                account["uuid"] = offline_uuid(name)
                account["access_token"] = offline_uuid(name)
            if not self._save_skin_fields(account):
                return
            account["skin_url"] = ""
            self.settings.update_account(account)
        else:
            account = make_offline_account(name)
            if not self._save_skin_fields(account):
                return
            self.settings.add_account(account)
        self.settings.set_active_account(account["id"])
        self.selected_id = account["id"]
        self._creating = False
        self.editing_id = account["id"]
        self._refresh_roster()
        self.accounts_changed.emit()
        self.accept()

    def _remove_selected(self) -> None:
        if not self.selected_id:
            return
        account = next((a for a in self.settings.accounts() if a.get("id") == self.selected_id), None)
        if not account:
            return
        if len(self.settings.accounts()) <= 1:
            self.ms_status.setText("至少保留一个玩家。可先新增一个离线玩家再移除当前玩家。")
            self._set_mode("offline")
            return
        if not ConfirmDialog.ask(
                self, self.theme, "移除玩家",
                f"要移除「{account.get('name')}」吗？这不会删除游戏存档。",
                "移除", danger=True):
            return
        self.settings.remove_account(self.selected_id)
        self.selected_id = str(self.settings.get("account_id") or "")
        self._creating = False
        self._refresh_roster()
        remaining = next((a for a in self.settings.accounts()
                          if a.get("id") == self.selected_id), None)
        if remaining and remaining.get("type") == "offline":
            self._load_offline_account(remaining)
        else:
            self._clear_offline_form()
        self.accounts_changed.emit()

    # ------------------------------------------------------------------ microsoft
    def _start_device_login(self) -> None:
        if self.login_thread and self.login_thread.isRunning():
            return
        self._refresh_login_config()
        config = self._login_config
        if not config.configured:
            self.ms_status.setText("登录应用未配置。请使用完整分发版，或检查本机 oauth.json。")
            self.ms_banner.set_tone("warning", "alert")
            self.ms_banner.set_text("登录应用是分发版自动生成的配置，不需要玩家填写。")
            return
        self.device_flow = {}
        self.device_card.hide()
        self.ms_submit.setEnabled(False)
        self.ms_cancel.show()
        self.ms_spinner.show()
        self.ms_status.setText("正在向微软请求一次性设备代码…")
        self.ms_banner.set_tone("info", "key")
        self.ms_banner.set_text(f"正在连接 {config.provider.label}…")

        def job(progress, cancel):
            return self.auth.login_with_device_code(
                config.client_id, progress=progress, cancel=cancel)

        self.login_thread = run_task(
            self, job, on_progress=self._ms_progress, on_done=self._ms_done,
            on_error=self._ms_error, on_cancelled=self._ms_cancelled, name="microsoft-login")

    def _ms_progress(self, value: object) -> None:
        if not isinstance(value, dict):
            return
        phase = value.get("phase")
        if phase == "device":
            flow = value.get("flow")
            if not isinstance(flow, dict):
                flow = {}
            self.device_flow = flow
            code = str(flow.get("user_code") or "—")
            uri = str(flow.get("verification_uri") or flow.get("verification_url") or "")
            self._device_login_url = str(flow.get("verification_uri_complete") or uri)
            self.device_code.setText(code)
            self.verify_uri.setText(self._device_login_url or uri)
            self.device_card.show()
            self.ms_status.setText("已打开浏览器；未自动打开时可点击“打开微软登录页”。")
            self.ms_banner.set_tone("info", "key")
            self.ms_banner.set_text("已生成一次性登录代码；代码将在约 15 分钟后失效。")
            QGuiApplication.clipboard().setText(code)
            if self._device_login_url and not self._device_opened:
                self._device_opened = True
                QDesktopServices.openUrl(QUrl(self._device_login_url))
        elif phase == "account":
            self.ms_status.setText(str(value.get("name") or "正在验证 Minecraft 档案…"))
        elif phase == "done":
            self.ms_status.setText("验证完成，正在保存玩家…")

    def _ms_done(self, account: object) -> None:
        if not isinstance(account, dict):
            self._ms_error("微软登录没有返回有效的玩家信息。")
            return
        self.settings.add_account(account)
        self.settings.set_active_account(account["id"])
        self.selected_id = account["id"]
        self.ms_submit.setEnabled(True)
        self.ms_spinner.hide()
        self.ms_cancel.hide()
        self.ms_status.setText(f"欢迎回来，{account.get('name')}。")
        self.ms_banner.set_tone("success", "check_circle")
        self.ms_banner.set_text("微软正版已登录，并已设置为当前玩家。")
        self._refresh_roster()
        self.accounts_changed.emit()
        QTimer.singleShot(850, self.accept)

    def _ms_error(self, message: str) -> None:
        self.ms_submit.setEnabled(True)
        self.ms_spinner.hide()
        self.ms_cancel.hide()
        self.ms_status.setText(message)
        self.ms_banner.set_tone("danger", "alert")
        self.ms_banner.set_text(message.split("\n")[0][:260])
        self.ms_banner.set_theme(self.theme)

    def _ms_cancelled(self) -> None:
        self.ms_submit.setEnabled(True)
        self.ms_spinner.hide()
        self.ms_cancel.hide()
        self.ms_status.setText("已取消微软登录。")
        self.ms_banner.set_tone("info", "key")
        self.ms_banner.set_text("你可以随时重新开始设备代码登录。")

    def _cancel_ms_login(self) -> None:
        if self.login_thread and self.login_thread.isRunning():
            self.login_thread.cancel()
            self.ms_status.setText("正在取消…")

    def _copy_code(self) -> None:
        QGuiApplication.clipboard().setText(self.device_code.text())
        self.ms_status.setText("代码已复制。")

    def _open_verification(self) -> None:
        url = self._device_login_url or self.verify_uri.text().strip()
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def reject(self) -> None:
        if self.login_thread and self.login_thread.isRunning():
            self.login_thread.cancel()
            self.login_thread.wait(1600)
        super().reject()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self.login_thread and self.login_thread.isRunning():
            self.login_thread.cancel()
            self.login_thread.wait(1200)
        super().closeEvent(event)


class VersionSettingsDialog(BaseDialog):
    def __init__(self, theme: Theme, settings: Settings, manager: MinecraftManager,
                 version: InstalledVersion, java_manager: JavaManager,
                 parent: QWidget | None = None,
                 on_repair: Callable[[str], None] | None = None,
                 on_delete: Callable[[str], None] | None = None) -> None:
        super().__init__(theme, f"版本设置 · {version.id}",
                         f"{version.display_type} · {version.loader_label}",
                         width=760, height=760, icon_name="sliders", parent=parent)
        self.settings = settings
        self.manager = manager
        self.version = version
        self.java_manager = java_manager
        self.on_repair = on_repair
        self.on_delete = on_delete
        self.requested_action: str | None = None
        profile = settings.version_profile(version.id)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(12)

        identity = Card(padding=16, spacing=12)
        top = QHBoxLayout()
        cover = QLabel()
        cover.setPixmap(icons.pixmap(icons.loader_icon_name(version.primary_loader), self.theme.accent, 32))
        cover.setFixedSize(44, 44)
        cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top.addWidget(cover, 0, Qt.AlignmentFlag.AlignTop)
        text_col = QVBoxLayout()
        text_col.setSpacing(3)
        title = label(version.id, "section")
        text_col.addWidget(title)
        text_col.addWidget(label(f"Minecraft {version.base_version} · {version.loader_versions_label or version.loader_label}",
                                 "muted", wrap=True))
        badges = QHBoxLayout()
        badges.addWidget(Pill(f"Java {version.java_major}+", "accent"))
        badges.addWidget(Pill("版本隔离" if manager.is_isolated(version) else "共享目录", "secondary"))
        self.isolation_badge = badges.itemAt(1).widget()
        badges.addStretch()
        text_col.addLayout(badges)
        text_col.addStretch()
        top.addLayout(text_col, 1)
        identity.body.addLayout(top)
        layout.addWidget(identity)

        launch_card = SectionCard("启动参数", "修改仅影响当前版本，留空则继承全局设置", "play")
        java_row = QWidget()
        java_layout = QHBoxLayout(java_row)
        java_layout.setContentsMargins(0, 8, 0, 8)
        java_layout.setSpacing(8)
        text_col2 = QVBoxLayout()
        text_col2.setSpacing(1)
        text_col2.addWidget(label("Java 运行时", "cardTitle"))
        text_col2.addWidget(label(f"此版本建议 Java {version.java_major}+", "faint"))
        java_layout.addLayout(text_col2, 1)
        self.java_combo = QComboBox()
        self.java_combo.setMinimumWidth(260)
        self.java_combo.addItem("自动选择", "")
        java_infos = java_manager.cached()
        for info in java_infos:
            self.java_combo.addItem(f"Java {info.major} · {info.vendor}", str(info.path))
        current_java = str(profile.get("java_path") or "")
        index = self.java_combo.findData(current_java)
        if current_java and index < 0:
            self.java_combo.addItem(f"自定义 · {Path(current_java).name}", current_java)
            index = self.java_combo.count() - 1
        self.java_combo.setCurrentIndex(max(0, index if current_java else 0))
        java_layout.addWidget(self.java_combo)
        browse = icon_button("folder_open", "选择 java.exe", icon_role="accent", size=15)
        browse.clicked.connect(self._browse_java)
        java_layout.addWidget(browse)
        launch_card.add_widget(java_row)
        self._add_divider(launch_card)

        mem_control = QWidget()
        mem_layout = QVBoxLayout(mem_control)
        mem_layout.setContentsMargins(0, 0, 0, 0)
        mem_layout.setSpacing(6)
        mem_row = QHBoxLayout()
        self.mem_mode = QComboBox()
        self.mem_mode.addItem("继承全局设置", "inherit")
        self.mem_mode.addItem("为此版本单独分配", "custom")
        self.mem_mode.setCurrentIndex(1 if profile.get("memory_mode") == "custom" else 0)
        self.mem_mode.currentIndexChanged.connect(self._memory_mode_changed)
        mem_row.addWidget(self.mem_mode)
        self.mem_value = label("", "accent")
        mem_row.addWidget(self.mem_value)
        mem_row.addStretch()
        mem_layout.addLayout(mem_row)
        self.mem_slider = QSlider(Qt.Orientation.Horizontal)
        self.mem_slider.setRange(1024, max(2048, min(settings.total_ram_mb, 32768)))
        current_mem = int(profile.get("memory_mb") or settings.memory_mb_for(version.id))
        self.mem_slider.setSingleStep(512)
        self.mem_slider.setPageStep(1024)
        self.mem_slider.setValue(max(1024, min(self.mem_slider.maximum(), (current_mem // 512) * 512)))
        self.mem_slider.valueChanged.connect(self._update_mem_value)
        mem_layout.addWidget(self.mem_slider)
        self._update_mem_value()
        launch_card.add_widget(mem_control)
        self._add_divider(launch_card)

        iso_control = QComboBox()
        iso_control.addItem("自动判断", "auto")
        iso_control.addItem("启用版本隔离", "on")
        iso_control.addItem("不隔离", "off")
        iso_control.setCurrentIndex({"auto": 0, "on": 1, "off": 2}.get(profile.get("isolation"), 0))
        self.iso_combo = iso_control
        launch_card.add_row(SettingRow(
            "版本隔离", "隔离后，模组、存档与配置保存在 versions/<版本名> 内。",
            iso_control, "layers"))
        window = QComboBox()
        window.addItem("继承全局设置", "inherit")
        window.addItem("窗口模式（854×480）", "windowed")
        window.addItem("最大化窗口（有边框）", "maximized")
        window.addItem("全屏", "fullscreen")
        window.addItem("自定义分辨率", "custom")
        window.setCurrentIndex(max(0, window.findData(profile.get("window_mode") or "inherit")))
        self.window_combo = window
        launch_card.add_row(SettingRow(
            "游戏窗口", "独立覆盖窗口大小与显示方式。", window, "monitor"))
        self.window_size_row = QWidget()
        window_size_layout = QHBoxLayout(self.window_size_row)
        window_size_layout.setContentsMargins(0, 6, 0, 8)
        window_size_layout.setSpacing(7)
        self.version_width = QSpinBox()
        self.version_width.setRange(640, 7680)
        self.version_width.setValue(int(profile.get("width") or self.settings.get("window_width") or 1280))
        self.version_height = QSpinBox()
        self.version_height.setRange(480, 4320)
        self.version_height.setValue(int(profile.get("height") or self.settings.get("window_height") or 720))
        window_size_layout.addWidget(label("宽", "faint"))
        window_size_layout.addWidget(self.version_width)
        window_size_layout.addWidget(label("高", "faint"))
        window_size_layout.addWidget(self.version_height)
        window_size_layout.addStretch()
        launch_card.add_widget(self.window_size_row)
        self.window_combo.currentIndexChanged.connect(
            lambda _index: self.window_size_row.setVisible(self.window_combo.currentData() == "custom"))
        self.window_size_row.setVisible(self.window_combo.currentData() == "custom")
        self.jvm_args_edit = QLineEdit(str(profile.get("jvm_args") or ""))
        self.jvm_args_edit.setPlaceholderText("例如 -XX:+UseG1GC")
        launch_card.add_row(SettingRow(
            "JVM 参数", "追加到 Java 启动参数末尾，留空继承全局。", self.jvm_args_edit, "cpu"))
        self.game_args_edit = QLineEdit(str(profile.get("game_args") or ""))
        self.game_args_edit.setPlaceholderText("例如 --fullscreen")
        launch_card.add_row(SettingRow(
            "游戏参数", "追加到游戏参数末尾，留空继承全局。", self.game_args_edit, "terminal"))
        self.server_edit = QLineEdit(str(profile.get("server") or ""))
        self.server_edit.setPlaceholderText("例如 play.example.com:25565")
        launch_card.add_row(SettingRow(
            "自动进入服务器", "启动后直接连接服务器，留空则进入主菜单。", self.server_edit, "server"))
        layout.addWidget(launch_card)

        note_card = SectionCard("版本名称与备注", "显示名称支持中文与任意 Unicode 字符", "log")
        self.display_name_edit = QLineEdit(str(profile.get("display_name") or ""))
        self.display_name_edit.setPlaceholderText("例如：机械冒险港·主世界")
        note_card.add_widget(label("显示名称", "faint"))
        note_card.add_widget(self.display_name_edit)
        self.note_edit = QLineEdit(str(profile.get("note") or ""))
        self.note_edit.setPlaceholderText("例如：主力生存存档，勿升级")
        note_card.add_widget(label("备注", "faint"))
        note_card.add_widget(self.note_edit)
        layout.addWidget(note_card)

        actions = Card(padding=16, spacing=10)
        actions.body.addWidget(label("维护", "section"))
        action_row = QHBoxLayout()
        open_btn = make_button("打开版本文件夹", "secondary", "folder", "muted", 15)
        open_btn.clicked.connect(lambda: open_path(version.path))
        action_row.addWidget(open_btn)
        if self.on_repair:
            repair_btn = make_button("补全版本文件", "secondary", "download", "muted", 15)
            repair_btn.clicked.connect(self._repair)
            action_row.addWidget(repair_btn)
        if self.on_delete:
            delete_btn = make_button("删除此版本", "danger", "trash", "danger", 15)
            delete_btn.clicked.connect(self._delete)
            action_row.addWidget(delete_btn)
        action_row.addStretch()
        actions.body.addLayout(action_row)
        layout.addWidget(actions)
        layout.addStretch()
        scroll.setWidget(host)
        self.body_layout.addWidget(scroll, 1)
        self.save_button = self.add_footer_button("保存设置", "primary", "save", self._save)
        self.add_footer_button("取消", "secondary", callback=self.reject)
        self._memory_mode_changed()

    @staticmethod
    def _add_divider(card: SectionCard) -> None:
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet("background: rgba(255,255,255,0.045);")
        card.add_widget(divider)

    def _memory_mode_changed(self) -> None:
        custom = self.mem_mode.currentData() == "custom"
        self.mem_slider.setEnabled(custom)
        self._update_mem_value()

    def _update_mem_value(self) -> None:
        if self.mem_mode.currentData() == "custom":
            self.mem_value.setText(f"{self.mem_slider.value()} MB")
        else:
            self.mem_value.setText(f"全局 · {self.settings.memory_mb_for(self.version.id)} MB")

    def _browse_java(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 java.exe", "", "Java 可执行文件 (java.exe java);;所有文件 (*)")
        if not path:
            return
        info = self.java_manager.probe(Path(path), source="版本指定")
        if not info:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "无法使用这个 Java", "所选文件无法作为 Java 运行。")
            return
        self.java_manager.add_manual(path)
        self.java_combo.addItem(f"Java {info.major} · {info.vendor}", str(info.path))
        self.java_combo.setCurrentIndex(self.java_combo.count() - 1)

    def _save(self) -> None:
        values = {
            "java_path": self.java_combo.currentData() or "",
            "memory_mode": self.mem_mode.currentData() or "inherit",
            "memory_mb": self.mem_slider.value() if self.mem_mode.currentData() == "custom" else 0,
            "isolation": self.iso_combo.currentData() or "auto",
            "window_mode": self.window_combo.currentData() if self.window_combo.currentData() != "inherit" else "inherit",
            "width": self.version_width.value() if self.window_combo.currentData() == "custom" else 0,
            "height": self.version_height.value() if self.window_combo.currentData() == "custom" else 0,
            "jvm_args": self.jvm_args_edit.text().strip(),
            "game_args": self.game_args_edit.text().strip(),
            "server": self.server_edit.text().strip(),
            "note": self.note_edit.text().strip(),
            "display_name": self.display_name_edit.text().strip(),
        }
        self.settings.save_version_profile(self.version.id, values)
        self.accept()

    def _repair(self) -> None:
        self.requested_action = "repair"
        self.accept()

    def _delete(self) -> None:
        self.requested_action = "delete"
        self.accept()


class GameConsoleDialog(BaseDialog):
    def __init__(self, theme: Theme, plan: LaunchPlan, session: GameSession,
                 parent: QWidget | None = None) -> None:
        super().__init__(theme, "游戏日志", f"Minecraft {plan.version.id} · Java {plan.java.major}",
                         width=860, height=600, icon_name="terminal", parent=parent)
        self.setModal(False)
        self.plan = plan
        self.session = session
        self._last_line = ""
        status = QFrame()
        status.setObjectName("SubCard")
        status_layout = QHBoxLayout(status)
        status_layout.setContentsMargins(13, 9, 13, 9)
        status_layout.setSpacing(8)
        self.state_dot = QLabel()
        self.state_dot.setPixmap(icons.pixmap("check_circle", theme.success, 14))
        status_layout.addWidget(self.state_dot)
        self.status_label = label("游戏进程正在运行", "cardTitle")
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()
        status_layout.addWidget(label(f"PID {session.process.pid if session.process else '—'}", "mono"))
        self.body_layout.addWidget(status)
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumBlockCount(10000)
        self.log_box.setStyleSheet(
            f"QPlainTextEdit {{ background: {theme.input}; border: 1px solid {theme.border_soft};"
            f" border-radius: 6px; padding: 9px; color: {theme.text};"
            f" font-family: 'Cascadia Mono', Consolas; font-size: 11px; }}")
        self.body_layout.addWidget(self.log_box, 1)
        self.open_log = self.add_footer_button("打开日志目录", "secondary", "folder", self._open_logs)
        copy = self.add_footer_button("复制启动命令", "ghost", "copy", self._copy_command)
        self.stop_button = self.add_footer_button("结束游戏", "danger", "stop", self._stop_game)
        close = self.add_footer_button("隐藏窗口", "secondary", callback=self.hide)
        session.logged.connect(self.append_line)
        session.started.connect(self._started)
        session.exited.connect(self._exited)
        session.failed.connect(self._failed)
        if session.process and session.process.poll() is not None:
            self._exited(session.process.returncode or 0)
        elif session.process:
            self._started(session.process.pid)
        self.log_box.appendPlainText(f"$ {plan.display_command}\n")

    def append_line(self, line: str) -> None:
        self.log_box.appendPlainText(line)
        self._last_line = line
        cursor = self.log_box.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.log_box.setTextCursor(cursor)

    def _started(self, pid: int) -> None:
        self.state_dot.setPixmap(icons.pixmap("check_circle", self.theme.success, 14))
        self.status_label.setText(f"Minecraft 已启动 · PID {pid}")
        self.stop_button.setEnabled(True)

    def _exited(self, code: int) -> None:
        self.state_dot.setPixmap(icons.pixmap("info", self.theme.text_faint, 14))
        if code == 0:
            self.status_label.setText("游戏已正常退出")
        else:
            self.status_label.setText(f"游戏已退出（代码 {code}）")
        self.stop_button.setEnabled(False)
        self.append_line(f"=== 游戏进程已退出（代码 {code}）===")

    def _failed(self, message: str) -> None:
        self.state_dot.setPixmap(icons.pixmap("alert", self.theme.danger, 14))
        self.status_label.setText("启动失败")
        self.append_line(message)
        self.stop_button.setEnabled(False)

    def _open_logs(self) -> None:
        try:
            open_path(self.plan.log_path.parent)
        except OSError:
            pass

    def _copy_command(self) -> None:
        QGuiApplication.clipboard().setText(self.plan.display_command)
        self.status_label.setText("启动命令已复制到剪贴板")

    def _stop_game(self) -> None:
        if not self.session.running:
            return
        if ConfirmDialog.ask(
                self, self.theme, "结束游戏",
                "强制结束 Minecraft 可能导致未保存的存档丢失。确定要结束游戏进程吗？",
                "结束进程", danger=True):
            self.session.terminate()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        event.ignore()
        self.hide()
