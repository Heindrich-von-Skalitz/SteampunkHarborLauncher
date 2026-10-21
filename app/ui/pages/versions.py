from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from ...core.minecraft import InstalledVersion
from ...utils import format_size, open_path, relative_time
from .. import icons
from ..theme import Theme
from ..widgets import (
    EmptyState, PageHeader, Pill, SearchBox, SegmentedControl,
    icon_button, label, make_button, repolish,
)


class VersionCard(QFrame):
    clicked = pyqtSignal(str)
    launch_requested = pyqtSignal(str)

    def __init__(self, version: InstalledVersion, theme: Theme,
                 selected: bool = False, is_current: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.version = version
        self.theme = theme
        self.is_current = is_current
        self.setObjectName("VersionCard")
        self.setProperty("selected", "true" if selected else "false")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(76)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 9, 12, 9)
        layout.setSpacing(12)
        icon_frame = QLabel()
        icon_frame.setPixmap(icons.pixmap(icons.loader_icon_name(version.primary_loader), theme.accent, 24))
        icon_frame.setFixedSize(30, 30)
        icon_frame.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_frame)
        col = QVBoxLayout()
        col.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(7)
        title = QLabel(version.label)
        title.setStyleSheet(f"color: {theme.text}; font-size: 13px; font-weight: 600;")
        title_row.addWidget(title)
        if is_current:
            current = Pill("当前", "accent")
            current.set_theme(theme)
            title_row.addWidget(current)
        if version.broken:
            broken = Pill("需要修复", "danger")
            broken.set_theme(theme)
            title_row.addWidget(broken)
        title_row.addStretch()
        col.addLayout(title_row)
        meta = QLabel(f"{version.loader_label} · Minecraft {version.base_version}")
        meta.setStyleSheet(f"color: {theme.text_muted}; font-size: 11px;")
        col.addWidget(meta)
        sub_row = QHBoxLayout()
        sub_row.setSpacing(10)
        if version.loader_versions_label:
            loader_label = QLabel(version.loader_versions_label)
            loader_label.setStyleSheet(f"color: {theme.accent}; font-size: 10px;")
            sub_row.addWidget(loader_label)
        played = QLabel("从未启动" if not version.last_played else f"上次启动 {relative_time(version.last_played)}")
        played.setStyleSheet(f"color: {theme.text_faint}; font-size: 10px;")
        sub_row.addWidget(played)
        sub_row.addStretch()
        col.addLayout(sub_row)
        layout.addLayout(col, 1)
        actions = QHBoxLayout()
        actions.setSpacing(4)
        play = icon_button("play", "启动此版本", icon_role="accent", size=16)
        play.clicked.connect(lambda: self.launch_requested.emit(version.id))
        actions.addWidget(play)
        detail = icon_button("chevron_right", "查看详情", icon_role="faint", size=14)
        detail.clicked.connect(lambda: self.clicked.emit(version.id))
        actions.addWidget(detail)
        layout.addLayout(actions)
        for widget in self.findChildren(QLabel):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.version.id)
        super().mouseReleaseEvent(event)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", "true" if selected else "false")
        repolish(self)


class VersionsPage(QWidget):
    def __init__(self, shell: Any, theme: Theme) -> None:
        super().__init__()
        self.shell = shell
        self.theme = theme
        self.selected_id = shell.current_version_id
        self.filter_key = "all"
        self.search_text = ""
        self.cards: dict[str, VersionCard] = {}
        self._build()
        self.refresh()
        self._update_detail()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 22)
        outer.setSpacing(14)
        self.header = PageHeader("", "版本管理",
                                 "管理已安装的 Minecraft 版本与加载器。")
        self.search = SearchBox("搜索版本、加载器或基础版本…")
        self.search.setFixedWidth(238)
        self.search.textChanged.connect(self._search)
        self.header.add_action(self.search)
        self.refresh_button = icon_button("refresh", "重新扫描版本", icon_role="muted", size=16)
        self.refresh_button.clicked.connect(self.shell.rescan_versions)
        self.header.add_action(self.refresh_button)
        outer.addWidget(self.header)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        self.filters = SegmentedControl(
            [("all", "全部"), ("release", "正式版"), ("modded", "加载器"),
             ("snapshot", "快照")], "all")
        self.filters.changed.connect(self._filter)
        controls.addWidget(self.filters)
        controls.addStretch()
        self.count_label = label("", "faint")
        controls.addWidget(self.count_label)
        outer.addLayout(controls)

        content = QHBoxLayout()
        content.setSpacing(15)
        self.list_scroll = QScrollArea()
        self.list_scroll.setWidgetResizable(True)
        self.list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 4, 0)
        self.list_layout.setSpacing(8)
        self.list_layout.addStretch()
        self.list_scroll.setWidget(self.list_host)
        content.addWidget(self.list_scroll, 7)
        self.empty_state = EmptyState(
            "还没有安装版本", "前往下载页安装一个 Minecraft 版本，或导入整合包。",
            "download_cloud", "前往下载")
        self.empty_state.action_button.clicked.connect(lambda: self.shell.navigate("download"))
        self.empty_state.hide()
        content.addWidget(self.empty_state, 7)

        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.detail_scroll.setFixedWidth(320)
        self.detail = QFrame()
        self.detail.setObjectName("SectionBox")
        detail_layout = QVBoxLayout(self.detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(0)
        detail_body = QVBoxLayout()
        detail_body.setContentsMargins(18, 16, 18, 17)
        detail_body.setSpacing(8)
        self.detail_type = label("未选择版本", "faint")
        detail_body.addWidget(self.detail_type)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self.detail_block = QLabel()
        self.detail_block.setFixedSize(23, 23)
        title_row.addWidget(self.detail_block)
        self.detail_title = label("", "section", wrap=True)
        title_row.addWidget(self.detail_title, 1)
        detail_body.addLayout(title_row)
        self.detail_meta = label("", "muted", wrap=True)
        detail_body.addWidget(self.detail_meta)
        detail_body.addSpacing(3)
        self.detail_loader = Pill("原版", "accent")
        self.detail_loader.set_theme(self.theme)
        detail_body.addWidget(self.detail_loader, 0, Qt.AlignmentFlag.AlignLeft)
        detail_body.addSpacing(3)
        self.info_box = QVBoxLayout()
        self.info_box.setSpacing(6)
        detail_body.addLayout(self.info_box)
        detail_body.addStretch()
        self.detail_actions = QVBoxLayout()
        self.detail_actions.setSpacing(7)
        self.launch_button = make_button("启动游戏", "primary", "play", "on_accent", 15)
        self.launch_button.clicked.connect(self._launch_selected)
        self.detail_actions.addWidget(self.launch_button)
        row = QHBoxLayout()
        row.setSpacing(7)
        self.settings_button = make_button("版本设置", "secondary", "sliders", "muted", 15)
        self.settings_button.clicked.connect(self._open_settings)
        row.addWidget(self.settings_button)
        self.folder_button = make_button("打开目录", "secondary", "folder", "muted", 15)
        self.folder_button.clicked.connect(self._open_folder)
        row.addWidget(self.folder_button)
        self.detail_actions.addLayout(row)
        self.repair_button = make_button("补全版本文件", "ghost", "refresh", "accent", 14)
        self.repair_button.clicked.connect(self._repair)
        self.detail_actions.addWidget(self.repair_button)
        detail_body.addLayout(self.detail_actions)
        detail_layout.addLayout(detail_body, 1)
        self.detail_scroll.setWidget(self.detail)
        content.addWidget(self.detail_scroll, 3)
        outer.addLayout(content, 1)
        self.detail_scroll.hide()

    # ------------------------------------------------------------------ list
    def refresh(self) -> None:
        versions = list(getattr(self.shell, "versions", []))
        if self.selected_id not in {v.id for v in versions}:
            self.selected_id = getattr(self.shell, "current_version_id", "") or (versions[0].id if versions else "")
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.cards.clear()
        if not versions:
            self.empty_state.action_button.setText("前往下载")
            try:
                self.empty_state.action_button.clicked.disconnect()
            except TypeError:
                pass
            self.empty_state.action_button.clicked.connect(lambda: self.shell.navigate("download"))
            self.empty_state.show()
            self.list_scroll.hide()
            self.detail_scroll.hide()
            self.count_label.setText("还没有安装版本")
            return
        self._render_cards(versions)
        self.empty_state.setVisible(not versions)
        self.list_scroll.setVisible(bool(versions))
        self.detail_scroll.setVisible(bool(versions) and bool(self.selected_id))
        self.count_label.setText(f"{len(versions)} 个版本 · 点击卡片查看详情")
        self._update_detail()

    def _render_cards(self, versions: list[InstalledVersion]) -> None:
        query = self.search_text.lower().strip()
        filtered: list[InstalledVersion] = []
        for version in versions:
            if self.filter_key == "release" and version.version_type != "release":
                continue
            if self.filter_key == "snapshot" and version.version_type != "snapshot":
                continue
            if self.filter_key == "modded" and not version.loaders:
                continue
            haystack = " ".join([version.id, version.label, version.base_version, version.loader_label,
                                 version.loader_versions_label]).lower()
            if query and query not in haystack:
                continue
            filtered.append(version)
        for version in filtered:
            card = VersionCard(version, self.theme, selected=version.id == self.selected_id,
                               is_current=version.id == getattr(self.shell, "current_version_id", ""))
            card.clicked.connect(self._select)
            card.launch_requested.connect(self.shell.launch_version)
            self.list_layout.insertWidget(self.list_layout.count() - 1, card)
            self.cards[version.id] = card
        if not filtered and versions:
            self.empty_state.setVisible(True)
            self.empty_state.action_button.setText("清除筛选")
            try:
                self.empty_state.action_button.clicked.disconnect()
            except TypeError:
                pass
            self.empty_state.action_button.clicked.connect(self._clear_filters)
            self.list_scroll.setVisible(False)
        elif filtered:
            self.empty_state.setVisible(False)
            self.list_scroll.setVisible(True)
            self.empty_state.action_button.setText("前往下载")
        self.count_label.setText(
            f"{len(filtered)} / {len(versions)} 个版本" if filtered != versions else f"{len(versions)} 个版本")

    def _select(self, version_id: str) -> None:
        self.selected_id = version_id
        for key, card in self.cards.items():
            card.set_selected(key == version_id)
        self.shell.set_current_version(version_id, notify=False)
        self._update_detail()

    def _update_detail(self) -> None:
        version = self.shell.minecraft.find(self.selected_id)
        if not version:
            self.detail_scroll.hide()
            return
        self.detail_scroll.show()
        self.detail_type.setText(f"{version.display_type} · {version.loader_label}")
        self.detail_title.setText(version.label)
        self.detail_block.setPixmap(
            icons.pixmap(icons.loader_icon_name(version.primary_loader), self.theme.accent, 20))
        self.detail_meta.setText(f"Minecraft {version.base_version}")
        self.detail_loader.setText(version.loader_label)
        self.detail_loader.set_tone("accent" if version.loaders else "muted")
        self.detail_loader.set_theme(self.theme)
        while self.info_box.count():
            item = self.info_box.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
            elif item.layout():
                while item.layout().count():
                    child = item.layout().takeAt(0).widget()
                    if child:
                        child.deleteLater()
        info_rows = [
            ("Java", f"Java {version.java_major}+"),
            ("目录", "版本隔离" if self.shell.minecraft.is_isolated(version) else "共享目录"),
            ("最后启动", relative_time(version.last_played) if version.last_played else "从未启动"),
            ("占用", self.shell.minecraft.pretty_size(version.id)),
        ]
        for key, value in info_rows:
            row = QHBoxLayout()
            row.setSpacing(8)
            key_label = label(key, "faint")
            row.addWidget(key_label)
            row.addStretch()
            value_label = label(value, "muted")
            row.addWidget(value_label)
            wrapper = QWidget()
            wrapper.setLayout(row)
            self.info_box.addWidget(wrapper)
        profile_note = self.shell.settings.version_profile(version.id).get("note")
        if profile_note:
            self.info_box.addSpacing(2)
            note = label(str(profile_note), "faint", wrap=True)
            self.info_box.addWidget(note)
        self.launch_button.setText("启动游戏" if version.id == self.shell.current_version_id else "设为当前并启动")
        self.launch_button.setEnabled(not version.broken)

    # ------------------------------------------------------------------ actions
    def _launch_selected(self) -> None:
        if self.selected_id:
            self.shell.launch_version(self.selected_id)

    def _open_settings(self) -> None:
        if self.selected_id:
            self.shell.open_version_settings(self.selected_id)

    def _open_folder(self) -> None:
        version = self.shell.minecraft.find(self.selected_id)
        if version:
            open_path(version.path)

    def _repair(self) -> None:
        if self.selected_id:
            self.shell.repair_version(self.selected_id)

    def _filter(self, key: str) -> None:
        self.filter_key = key
        self.refresh()

    def _search(self, text: str) -> None:
        self.search_text = text
        self.refresh()

    def _clear_filters(self) -> None:
        self.filter_key = "all"
        self.search_text = ""
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.filters.set_current("all")
        self.filter_key = "all"
        self.refresh()

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.detail_loader.set_theme(theme)
        if self.isVisible():
            self.refresh()
