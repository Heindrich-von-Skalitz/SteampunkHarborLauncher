from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QSignalBlocker, QUrl, Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ...utils import open_path, relative_time
from ..theme import Theme
from ..widgets import Avatar, Card, Pill, ScrollPage, icon_button, label, make_button, set_icon


class HomePage(ScrollPage):
    """A deliberately plain launch screen: account, version, one launch button."""

    def __init__(self, shell: Any, theme: Theme) -> None:
        super().__init__(margins=(26, 20, 26, 24), spacing=14)
        self.shell = shell
        self.theme = theme
        self.setObjectName("HomePage")
        self._build()
        self.refresh_versions()
        self.refresh_account()
        self.refresh_java()
        self.refresh_stats()
        self.refresh_history()
        self.refresh_news()

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)
        text = QVBoxLayout()
        text.setSpacing(3)
        self.title_label = label("启动游戏", "pageTitle")
        text.addWidget(self.title_label)
        self.greeting_sub = label("选择游戏版本，然后开始游戏。", "subtitle")
        text.addWidget(self.greeting_sub)
        header_layout.addLayout(text, 1)
        refresh = icon_button("refresh", "重新扫描版本", icon_role="muted", size=16)
        refresh.clicked.connect(self.shell.rescan_versions)
        header_layout.addWidget(refresh)
        self.add(header)

        self.account_card = Card(padding=14, spacing=10)
        account_row = QHBoxLayout()
        account_row.setSpacing(11)
        self.avatar = Avatar("Player", 40)
        account_row.addWidget(self.avatar)
        account_text = QVBoxLayout()
        account_text.setSpacing(1)
        account_text.addWidget(label("当前玩家", "faint"))
        self.account_name = QLabel("未登录")
        self.account_name.setStyleSheet("font-size: 15px; font-weight: 600;")
        account_text.addWidget(self.account_name)
        account_row.addLayout(account_text)
        self.account_type_pill = Pill("离线", "secondary")
        self.account_type_pill.set_theme(self.theme)
        account_row.addWidget(self.account_type_pill)
        account_row.addStretch()
        self.buy_button = make_button("购买 Minecraft 正版", "ghost", "globe", "accent", 13)
        self.buy_button.setToolTip("打开 Minecraft 官方购买页面")
        self.buy_button.clicked.connect(self._open_purchase)
        account_row.addWidget(self.buy_button)
        manage = make_button("管理玩家", "secondary", "user", "muted", 14)
        manage.clicked.connect(self.shell.open_accounts)
        account_row.addWidget(manage)
        self.account_card.body.addLayout(account_row)
        self.add(self.account_card)

        launch_card = Card(padding=20, spacing=12)
        version_row = QHBoxLayout()
        version_row.setSpacing(9)
        version_row.addWidget(label("游戏版本", "muted"))
        self.version_combo = QComboBox()
        self.version_combo.setMinimumWidth(300)
        self.version_combo.setFixedHeight(34)
        self.version_combo.currentIndexChanged.connect(self._version_changed)
        version_row.addWidget(self.version_combo)
        self.version_settings_button = make_button("版本设置", "secondary", "sliders", "muted", 14)
        self.version_settings_button.clicked.connect(self._open_version_settings)
        version_row.addWidget(self.version_settings_button)
        version_row.addStretch()
        launch_card.body.addLayout(version_row)

        self.update_button = make_button("", "ghost", "download_cloud", "accent", 13)
        self.update_button.clicked.connect(lambda: self.shell.navigate("download"))
        self.update_button.hide()
        launch_card.body.addWidget(self.update_button, 0, Qt.AlignmentFlag.AlignLeft)

        launch_row = QHBoxLayout()
        launch_row.setSpacing(14)
        self.launch_button = QPushButton("启动游戏")
        self.launch_button.setProperty("role", "launch")
        self.launch_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.launch_button.setFixedSize(196, 48)
        set_icon(self.launch_button, "play", "on_accent", 18)
        self.launch_button.clicked.connect(self.shell.launch_current)
        launch_row.addWidget(self.launch_button)
        hint_col = QVBoxLayout()
        hint_col.setSpacing(2)
        self.launch_hint = QLabel("准备就绪")
        self.launch_hint.setProperty("role", "muted")
        hint_col.addWidget(self.launch_hint)
        self.launch_subhint = QLabel("启动器会先检查 Java 与版本文件")
        self.launch_subhint.setProperty("role", "faint")
        hint_col.addWidget(self.launch_subhint)
        launch_row.addLayout(hint_col, 1)
        launch_card.body.addLayout(launch_row)

        status_row = QHBoxLayout()
        status_row.setSpacing(16)
        self.java_status = label("Java 检测中", "faint")
        self.memory_status = label("内存 —", "faint")
        self.isolation_status = label("版本隔离 —", "faint")
        status_row.addWidget(self.java_status)
        status_row.addWidget(self.memory_status)
        status_row.addWidget(self.isolation_status)
        status_row.addStretch()
        launch_card.body.addLayout(status_row)
        self.add(launch_card)

        bottom = QHBoxLayout()
        bottom.setSpacing(14)
        history_card = Card(padding=16, spacing=9)
        history_head = QHBoxLayout()
        history_head.addWidget(label("最近启动", "section"), 1)
        all_logs = make_button("查看日志", "ghost", "log", "accent", 13)
        all_logs.clicked.connect(self._open_logs)
        history_head.addWidget(all_logs)
        history_card.body.addLayout(history_head)
        self.history_box = QVBoxLayout()
        self.history_box.setSpacing(6)
        history_card.body.addLayout(self.history_box)
        self.history_empty = label("还没有启动记录。", "faint")
        history_card.body.addWidget(self.history_empty)
        bottom.addWidget(history_card, 3)

        actions_card = Card(padding=16, spacing=9)
        actions_card.body.addWidget(label("快速操作", "section"))
        actions = (
            ("游戏目录", "folder", lambda: open_path(self.shell.settings.minecraft_dir)),
            ("模组与资源", "package", lambda: self.shell.navigate("content", tab="mods")),
            ("版本管理", "layers", lambda: self.shell.navigate("versions")),
        )
        for text_value, icon_name, action in actions:
            button = make_button(text_value, "subtle", icon_name, "muted", 15)
            button.setMinimumHeight(36)
            button.clicked.connect(action)
            actions_card.body.addWidget(button)
        bottom.addWidget(actions_card, 2)
        self.add_layout(bottom)
        self.add_stretch()
        self.greeting_sub.setText("选择游戏版本，然后开始游戏。")

    # ------------------------------------------------------------------ state
    def _open_purchase(self) -> None:
        QDesktopServices.openUrl(QUrl("https://www.minecraft.net/zh-hans/choose-your-game"))

    def refresh_versions(self) -> None:
        versions = list(getattr(self.shell, "versions", []))
        current = getattr(self.shell, "current_version_id", "")
        blocker = QSignalBlocker(self.version_combo)
        self.version_combo.clear()
        if not versions:
            self.version_combo.addItem("尚未安装游戏版本", "")
            self.version_combo.setEnabled(False)
            self.launch_button.setEnabled(False)
            self.launch_button.setText("安装游戏版本")
            self.launch_hint.setText("还没有安装游戏版本")
            self.launch_subhint.setText("前往下载页安装一个版本")
            self.version_settings_button.setEnabled(False)
            self.refresh_stats()
            return
        self.version_combo.setEnabled(True)
        self.version_settings_button.setEnabled(True)
        for version in versions:
            text = version.id
            if version.loaders:
                text += f"  ·  {version.loader_label}"
            self.version_combo.addItem(text, version.id)
        index = self.version_combo.findData(current)
        if index < 0:
            index = 0
            current = str(self.version_combo.itemData(0) or "")
            self.shell.set_current_version(current, notify=False)
        self.version_combo.setCurrentIndex(index)
        del blocker
        session = getattr(self.shell, "game_session", None)
        running = bool(session is not None and session.running)
        self.launch_button.setEnabled(not running)
        self.launch_button.setText("游戏运行中" if running else "启动游戏")
        self._update_launch_state()
        self.refresh_stats()
        self.refresh_history()

    def refresh_account(self) -> None:
        account = self.shell.settings.active_account() or {}
        name = str(account.get("name") or "未登录")
        self.account_name.setText(name)
        self.avatar.set_account(account)
        if account.get("type") == "microsoft":
            self.account_type_pill.setText("微软正版")
            self.account_type_pill.set_tone("accent")
        else:
            self.account_type_pill.setText("离线玩家")
            self.account_type_pill.set_tone("secondary")
        self.account_type_pill.set_theme(self.theme)
        self.buy_button.setVisible(account.get("type") != "microsoft")

    def refresh_java(self) -> None:
        java_infos = list(getattr(self.shell, "java_list", []))
        version = self.shell.current_version()
        if not version:
            self.java_status.setText("Java —")
            return
        required = version.java_major
        usable = [item for item in java_infos if item.major >= required]
        if usable:
            chosen = self.shell.java_for_version(version, allow_error=False)
            self.java_status.setText(f"Java {chosen.major}" if chosen else f"Java {required}+")
        elif java_infos:
            self.java_status.setText(f"需要 Java {required}+")
        else:
            self.java_status.setText("Java 检测中")

    def refresh_stats(self) -> None:
        version = self.shell.current_version()
        if not version:
            self.memory_status.setText("内存 —")
            self.isolation_status.setText("版本隔离 —")
            return
        memory = self.shell.settings.memory_mb_for(version.id)
        self.memory_status.setText(f"内存 {memory / 1024:.0f} GB" if memory >= 1024 else f"内存 {memory} MB")
        isolated = self.shell.minecraft.is_isolated(version)
        self.isolation_status.setText("版本隔离 开" if isolated else "版本隔离 关")
        self.refresh_java()
        self._update_launch_state()

    def refresh_history(self) -> None:
        while self.history_box.count():
            item = self.history_box.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        history = list(self.shell.settings.get("history") or [])
        visible = [item for item in history if isinstance(item, dict)][:3]
        self.history_empty.setVisible(not visible)
        for record in visible:
            row = QHBoxLayout()
            row.setSpacing(8)
            version = QLabel(str(record.get("version") or "未知版本"))
            version.setStyleSheet("font-size: 11px;")
            row.addWidget(version)
            row.addStretch()
            time_label = label(relative_time(record.get("time")), "faint")
            row.addWidget(time_label)
            wrapper = QWidget()
            wrapper.setLayout(row)
            self.history_box.addWidget(wrapper)

    def refresh_news(self) -> None:
        latest = getattr(self.shell, "latest_release", "") or ""
        current = self.shell.current_version()
        if latest and (not current or current.base_version != latest):
            self.update_button.setText(f"发现新版本 {latest} · 前往下载")
            self.update_button.show()
        else:
            self.update_button.hide()

    def set_launching(self, busy: bool) -> None:
        if busy:
            self.launch_button.setEnabled(False)
            self.launch_button.setText("启动中…")
            self.launch_hint.setText("正在检查游戏文件")
            self.launch_subhint.setText("游戏窗口即将出现")
        else:
            session = getattr(self.shell, "game_session", None)
            running = bool(session is not None and session.running)
            self.launch_button.setEnabled(bool(self.version_combo.currentData()) and not running)
            self.launch_button.setText("游戏运行中" if running else "启动游戏")
            self.launch_hint.setText("准备就绪")
            self.launch_subhint.setText("启动器会先检查 Java 与版本文件")
            self.refresh_stats()

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.account_type_pill.set_theme(theme)

    def _update_launch_state(self) -> None:
        version = self.shell.current_version()
        if not version:
            return
        session = getattr(self.shell, "game_session", None)
        if session is not None and session.running:
            return
        java = self.shell.java_for_version(version, allow_error=False)
        required = version.java_major
        if not java:
            self.launch_hint.setText(f"需要 Java {required}+")
            self.launch_subhint.setText("前往设置检测 Java，或下载 Temurin")
        else:
            memory = self.shell.settings.memory_mb_for(version.id)
            self.launch_hint.setText(f"Java {java.major} · {memory} MB")
            self.launch_subhint.setText("点击启动游戏，启动器会自动检查版本文件")

    def _version_changed(self, index: int) -> None:
        value = self.version_combo.itemData(index)
        if value:
            self.shell.set_current_version(str(value), notify=False)
            self.refresh_java()
            self.refresh_stats()
            self.refresh_history()

    def _open_version_settings(self) -> None:
        version = self.shell.current_version()
        if version:
            self.shell.open_version_settings(version.id)

    def _open_logs(self) -> None:
        console = getattr(self.shell, "game_console", None)
        if console is not None:
            console.show()
            console.raise_()
            return
        version = self.shell.current_version()
        try:
            if version:
                open_path(self.shell.minecraft.logs_dir(version))
            else:
                open_path(self.shell.minecraft.root / "logs")
        except OSError as exc:
            self.shell.toast(f"无法打开日志目录：{exc}", "error")
