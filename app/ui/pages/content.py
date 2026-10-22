from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from ...core.minecraft import InstalledVersion
from ...core.mods import ModInfo, ResourceInfo, SaveInfo
from ...utils import LauncherError, format_size, now_stamp, open_path, relative_time
from .. import icons
from ..dialogs import BaseDialog, ConfirmDialog, ProgressJobDialog
from ..theme import Theme
from ..widgets import (
    Card, DropZone, EmptyState, InfoBanner, PageHeader, Pill, ProjectIcon,
    SearchBox, SegmentedControl, ToggleSwitch, icon_button, label, make_button,
    repolish,
)


class ModRow(QFrame):
    toggled = pyqtSignal(object, bool)
    delete_requested = pyqtSignal(object)

    def __init__(self, mod: ModInfo, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.mod = mod
        self.theme = theme
        self.setObjectName("ProjectCard")
        self.setFixedHeight(72)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(13, 9, 12, 9)
        layout.setSpacing(11)
        icon = ProjectIcon(40)
        icon.set_fallback(mod.name)
        icon.set_theme(theme)
        if mod.icon_path:
            icon.set_local_icon(mod.icon_path)
        layout.addWidget(icon)
        body = QVBoxLayout()
        body.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(7)
        name = QLabel(mod.name)
        name.setStyleSheet(f"color: {theme.text}; font-size: 12px; font-weight: 600;")
        top.addWidget(name)
        if mod.version:
            version = QLabel(mod.version)
            version.setStyleSheet(f"color: {theme.text_faint}; font-size: 10px;")
            top.addWidget(version)
        loader_pill = Pill(mod.loader_label, "accent" if mod.loader != "unknown" else "muted")
        loader_pill.set_theme(theme)
        top.addWidget(loader_pill)
        if not mod.enabled:
            disabled_pill = Pill("已停用", "muted")
            disabled_pill.set_theme(theme)
            top.addWidget(disabled_pill)
        if mod.incompatible:
            incompatible = Pill("加载器不匹配", "danger")
            incompatible.set_theme(theme)
            top.addWidget(incompatible)
        top.addStretch()
        body.addLayout(top)
        authors = QLabel(f"{mod.author_label} · {mod.filename} · {mod.display_size}")
        authors.setStyleSheet(f"color: {theme.text_faint}; font-size: 9px;")
        body.addWidget(authors)
        desc = QLabel(mod.description or mod.parse_error or "没有可读取的描述。")
        desc.setStyleSheet(f"color: {theme.text_muted}; font-size: 10px;")
        desc.setToolTip(mod.description or mod.parse_error)
        desc.setMaximumHeight(17)
        body.addWidget(desc)
        layout.addLayout(body, 1)
        self.switch = ToggleSwitch(mod.enabled)
        self.switch.setToolTip("启用 / 停用这个模组")
        self.switch.toggled.connect(lambda checked: self.toggled.emit(mod, checked))
        layout.addWidget(self.switch)
        delete = icon_button("trash", "移除此模组", icon_role="danger", size=14)
        delete.clicked.connect(lambda: self.delete_requested.emit(mod))
        layout.addWidget(delete)


class ResourceRow(QFrame):
    delete_requested = pyqtSignal(object)
    open_requested = pyqtSignal(object)
    toggled = pyqtSignal(object, bool)

    def __init__(self, resource: ResourceInfo, theme: Theme, kind: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.resource = resource
        self.setObjectName("VersionCard")
        self.setFixedHeight(66)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(13, 8, 12, 8)
        layout.setSpacing(10)
        icon = ProjectIcon(38)
        icon.set_fallback(resource.name)
        icon.set_theme(theme)
        if resource.icon_path:
            icon.set_local_icon(resource.icon_path)
        layout.addWidget(icon)
        body = QVBoxLayout()
        body.setSpacing(1)
        name = QLabel(resource.name)
        name.setStyleSheet(f"color: {theme.text}; font-size: 12px; font-weight: 600;")
        body.addWidget(name)
        desc = QLabel(resource.description or ("光影包" if kind == "shaderpacks" else "资源包"))
        desc.setStyleSheet(f"color: {theme.text_faint}; font-size: 9px;")
        desc.setMaximumHeight(17)
        body.addWidget(desc)
        layout.addLayout(body, 1)
        size = QLabel(format_size(resource.size))
        size.setStyleSheet(f"color: {theme.text_faint}; font-size: 10px;")
        layout.addWidget(size)
        if kind == "resourcepacks":
            self.switch = ToggleSwitch(resource.enabled)
            self.switch.setToolTip("勾选后进入游戏自动加载这个材质包")
            self.switch.toggled.connect(lambda checked: self.toggled.emit(resource, checked))
            layout.addWidget(self.switch)
        open_button = icon_button("folder", "打开所在目录", icon_role="muted", size=14)
        open_button.clicked.connect(lambda: self.open_requested.emit(resource))
        layout.addWidget(open_button)
        delete = icon_button("trash", "移除", icon_role="danger", size=14)
        delete.clicked.connect(lambda: self.delete_requested.emit(resource))
        layout.addWidget(delete)


class SaveRow(QFrame):
    open_requested = pyqtSignal(object)

    def __init__(self, save: SaveInfo, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.save = save
        self.setObjectName("VersionCard")
        self.setFixedHeight(66)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(13, 8, 12, 8)
        layout.setSpacing(10)
        icon = ProjectIcon(38)
        icon.set_fallback(save.name)
        icon.set_theme(theme)
        if save.icon_path:
            icon.set_local_icon(save.icon_path)
        layout.addWidget(icon)
        body = QVBoxLayout()
        body.setSpacing(1)
        name = QLabel(save.name)
        name.setStyleSheet(f"color: {theme.text}; font-size: 12px; font-weight: 600;")
        body.addWidget(name)
        info_parts = [relative_time(save.modified)]
        if save.game_version:
            info_parts.append(f"版本 {save.game_version}")
        info_parts.append(format_size(save.size))
        info = QLabel(" · ".join(info_parts))
        info.setStyleSheet(f"color: {theme.text_faint}; font-size: 9px;")
        body.addWidget(info)
        layout.addLayout(body, 1)
        open_button = make_button("打开存档", "secondary", "folder", "muted", 13)
        open_button.clicked.connect(lambda: self.open_requested.emit(save))
        layout.addWidget(open_button)


class ExportPackDialog(BaseDialog):
    def __init__(self, theme: Theme, version: InstalledVersion,
                 parent: QWidget | None = None) -> None:
        super().__init__(theme, "导出整合包", "导出为 Modrinth .mrpack，其他玩家可一键补全",
                         width=620, height=520, icon_name="archive", parent=parent)
        self.result_data: dict[str, Any] = {}
        body = self.body_layout
        info = InfoBanner(
            f"将打包版本 {version.id} 的模组与配置。已知 Modrinth 来源会写入下载清单，"
            "其余文件直接放入覆盖目录。", "info", "package")
        body.addWidget(info)
        body.addWidget(label("整合包名称", "cardTitle"))
        self.name_edit = QLineEdit(f"{version.id} 游戏包")
        body.addWidget(self.name_edit)
        row = QHBoxLayout()
        row.setSpacing(10)
        left = QVBoxLayout()
        left.setSpacing(4)
        left.addWidget(label("包版本", "cardTitle"))
        self.version_edit = QLineEdit("1.0.0")
        left.addWidget(self.version_edit)
        right = QVBoxLayout()
        right.setSpacing(4)
        right.addWidget(label("包含配置", "cardTitle"))
        config_row = QHBoxLayout()
        self.include_config = ToggleSwitch(True)
        config_row.addWidget(self.include_config)
        config_row.addWidget(label("config / options.txt", "faint"))
        config_row.addStretch()
        right.addLayout(config_row)
        row.addLayout(left, 1)
        row.addLayout(right, 1)
        body.addLayout(row)
        body.addWidget(label("简介", "cardTitle"))
        self.summary_edit = QLineEdit()
        self.summary_edit.setPlaceholderText("一句话介绍这次版本")
        body.addWidget(self.summary_edit)
        body.addWidget(label("保存位置", "cardTitle"))
        path_row = QHBoxLayout()
        self.path_edit = QLineEdit(default_output_path(version.id))
        path_row.addWidget(self.path_edit, 1)
        browse = make_button("选择", "secondary", "folder", "muted", 14)
        browse.clicked.connect(self._browse)
        path_row.addWidget(browse)
        body.addLayout(path_row)
        body.addStretch()
        self.add_footer_button("取消", "secondary", callback=self.reject)
        self.export_button = self.add_footer_button("开始打包", "primary", "archive", self._accept)

    def _browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "保存整合包", self.path_edit.text(),
                                              "Modrinth 整合包 (*.mrpack);;Zip (*.zip)")
        if path:
            self.path_edit.setText(path)

    def _accept(self) -> None:
        if not self.name_edit.text().strip():
            self.name_edit.setFocus()
            return
        self.result_data = {
            "name": self.name_edit.text().strip(),
            "pack_version": self.version_edit.text().strip() or "1.0.0",
            "summary": self.summary_edit.text().strip(),
            "include_config": self.include_config.isChecked(),
            "output": self.path_edit.text().strip(),
        }
        self.accept()


def default_output_path(version_id: str) -> str:
    home = Path.home()
    desktop = home / "Desktop"
    folder = desktop if desktop.exists() else home / "Documents"
    safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in version_id)
    return str(folder / f"{safe}-游戏包-{now_stamp()}.mrpack")


class ContentPage(QWidget):
    def __init__(self, shell: Any, theme: Theme) -> None:
        super().__init__()
        self.shell = shell
        self.theme = theme
        self.selected_id = shell.current_version_id
        self.active_tab = "mods"
        self.search_text = ""
        self.mods: list[ModInfo] = []
        self.issues: list[str] = []
        self.setAcceptDrops(True)
        self._build()
        self.refresh()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 18)
        outer.setSpacing(13)
        self.header = PageHeader("", "内容管理",
                                 "管理模组、资源包、光影与存档。")
        self.import_button = make_button("导入模组", "secondary", "upload", "muted", 15)
        self.import_button.clicked.connect(self._add_for_tab)
        self.header.add_action(self.import_button)
        self.export_button = make_button("导出整合包", "primary", "archive", "on_accent", 15)
        self.export_button.clicked.connect(self._export_pack)
        self.header.add_action(self.export_button)
        outer.addWidget(self.header)

        toolbar = QFrame()
        toolbar.setObjectName("SectionBox")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(14, 9, 14, 9)
        toolbar_layout.setSpacing(10)
        toolbar_layout.addWidget(label("当前版本", "faint"))
        self.version_combo = QComboBox()
        self.version_combo.setMinimumWidth(270)
        self.version_combo.currentIndexChanged.connect(self._version_changed)
        toolbar_layout.addWidget(self.version_combo)
        self.open_folder_button = make_button("打开游戏目录", "ghost", "folder", "muted", 14)
        self.open_folder_button.clicked.connect(self._open_game_folder)
        toolbar_layout.addWidget(self.open_folder_button)
        self.import_pack_button = make_button("导入整合包", "ghost", "archive", "accent", 14)
        self.import_pack_button.clicked.connect(self._import_modpack)
        toolbar_layout.addWidget(self.import_pack_button)
        refresh = icon_button("refresh", "重新扫描内容", icon_role="muted", size=15)
        refresh.clicked.connect(self.refresh)
        toolbar_layout.addWidget(refresh)
        toolbar_layout.addStretch()
        self.count_pills = {}
        for key, title in (("mods", "模组"), ("resourcepacks", "资源包"),
                           ("shaderpacks", "光影"), ("saves", "存档")):
            pill = Pill(f"{title} 0", "muted")
            pill.set_theme(self.theme)
            self.count_pills[key] = pill
            toolbar_layout.addWidget(pill)
        outer.addWidget(toolbar)

        controls = QHBoxLayout()
        controls.setSpacing(9)
        self.tabs = SegmentedControl([
            ("mods", "模组"), ("resourcepacks", "资源包"),
            ("shaderpacks", "光影"), ("saves", "存档"),
        ], "mods")
        self.tabs.changed.connect(self._switch_tab)
        controls.addWidget(self.tabs)
        controls.addStretch()
        self.search = SearchBox("搜索已安装内容…")
        self.search.setFixedWidth(245)
        self.search.textChanged.connect(self._search)
        controls.addWidget(self.search)
        self.add_mod_button = make_button("添加文件", "secondary", "plus", "muted", 14)
        self.add_mod_button.clicked.connect(self._add_for_tab)
        controls.addWidget(self.add_mod_button)
        outer.addLayout(controls)

        self.stack = QStackedWidget()
        self.mods_page = self._build_mods_page()
        self.resource_page = self._build_resource_page("resourcepacks")
        self.shader_page = self._build_resource_page("shaderpacks")
        self.saves_page = self._build_saves_page()
        self.stack.addWidget(self.mods_page)
        self.stack.addWidget(self.resource_page)
        self.stack.addWidget(self.shader_page)
        self.stack.addWidget(self.saves_page)
        outer.addWidget(self.stack, 1)
        self.empty_page = EmptyState(
            "内容里还没有东西", "选择或安装一个版本，然后放入模组、资源包与光影。",
            "package", "前往下载")
        self.empty_page.set_theme(self.theme)
        self.empty_page.action_button.clicked.connect(lambda: self.shell.navigate("download"))
        self.empty_page.hide()
        outer.addWidget(self.empty_page, 1)

    def _build_mods_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        self.issues_banner = InfoBanner("", "warning", "alert")
        self.issues_banner.hide()
        layout.addWidget(self.issues_banner)
        self.mods_scroll = QScrollArea()
        self.mods_scroll.setWidgetResizable(True)
        self.mods_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.mods_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.mods_host = QWidget()
        self.mods_layout = QVBoxLayout(self.mods_host)
        self.mods_layout.setContentsMargins(0, 0, 4, 0)
        self.mods_layout.setSpacing(7)
        self.mods_layout.addStretch()
        self.mods_scroll.setWidget(self.mods_host)
        layout.addWidget(self.mods_scroll, 1)
        self.mods_empty = EmptyState(
            "还没有模组", "从 Modrinth 获取，或把 .jar 文件拖进内容工坊。", "package",
            "去下载模组")
        self.mods_empty.set_theme(self.theme)
        self.mods_empty.action_button.clicked.connect(
            lambda: self.shell.navigate("download", tab="mod"))
        self.mods_empty.hide()
        layout.addWidget(self.mods_empty)
        return page

    def _build_resource_page(self, kind: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget()
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 4, 0)
        host_layout.setSpacing(7)
        host_layout.addStretch()
        scroll.setWidget(host)
        layout.addWidget(scroll, 1)
        if kind == "resourcepacks":
            self.resource_host = host
            self.resource_layout = host_layout
            self.resource_scroll = scroll
            self.resource_empty = EmptyState("还没有资源包", "把资源包放入 resourcepacks 目录，或从下载页获取。",
                                             "image", "去下载资源包")
            self.resource_empty.set_theme(self.theme)
            self.resource_empty.action_button.clicked.connect(
                lambda: self.shell.navigate("download", tab="resourcepack"))
        else:
            self.shader_host = host
            self.shader_layout = host_layout
            self.shader_scroll = scroll
            self.shader_empty = EmptyState("还没有光影包", "光影让世界重新有颜色。把光影包放入 shaderpacks 目录。",
                                           "sparkle", "去下载光影")
            self.shader_empty.set_theme(self.theme)
            self.shader_empty.action_button.clicked.connect(
                lambda: self.shell.navigate("download", tab="shader"))
        empty = self.resource_empty if kind == "resourcepacks" else self.shader_empty
        empty.hide()
        layout.addWidget(empty)
        return page

    def _build_saves_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget()
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 4, 0)
        host_layout.setSpacing(7)
        host_layout.addStretch()
        scroll.setWidget(host)
        layout.addWidget(scroll, 1)
        self.saves_host = host
        self.saves_layout = host_layout
        self.saves_scroll = scroll
        self.saves_empty = EmptyState("还没有存档", "进入游戏创建世界，存档会显示在这里。", "map")
        self.saves_empty.set_theme(self.theme)
        self.saves_empty.hide()
        layout.addWidget(self.saves_empty)
        return page

    # ------------------------------------------------------------------ refresh
    def refresh(self) -> None:
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        versions = list(getattr(self.shell, "versions", []))
        for version in versions:
            self.version_combo.addItem(icons.icon("layers", self.theme.text_muted, 14),
                                       f"{version.id}   ·   {version.loader_label}", version.id)
        current = self.selected_id or getattr(self.shell, "current_version_id", "")
        index = self.version_combo.findData(current)
        if index < 0 and versions:
            index = 0
            current = versions[0].id
        if index >= 0:
            self.version_combo.setCurrentIndex(index)
        self.version_combo.blockSignals(False)
        self.selected_id = str(self.version_combo.currentData() or "")
        version = self.shell.minecraft.find(self.selected_id)
        has_version = version is not None
        self.import_button.setEnabled(has_version)
        self.export_button.setEnabled(has_version)
        self.open_folder_button.setEnabled(has_version)
        self.add_mod_button.setEnabled(has_version)
        self.version_combo.setEnabled(bool(versions))
        self.stack.setVisible(has_version)
        self.empty_page.setVisible(not has_version)
        if not has_version:
            return
        counts = self.shell.minecraft.content_counts(version)
        counts["resourcepacks"] = self.shell.resourcepack_count(version)
        labels = {"mods": "模组", "resourcepacks": "资源包", "shaderpacks": "光影", "saves": "存档"}
        for key, pill in self.count_pills.items():
            pill.setText(f"{labels[key]} {counts[key]}")
            pill.set_tone("accent" if counts[key] else "muted")
        self.mods, self.issues = self.shell.mod_manager.scan(version)
        self._render_mods()
        self._render_resources(version)
        self._render_saves(version)
        self._update_import_labels()

    def _render_mods(self) -> None:
        while self.mods_layout.count() > 1:
            item = self.mods_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        query = self.search_text.lower()
        shown = [m for m in self.mods
                 if not query or query in (m.name + " " + m.filename + " " + m.mod_id).lower()]
        for mod in shown:
            row = ModRow(mod, self.theme)
            row.toggled.connect(self._toggle_mod)
            row.delete_requested.connect(self._delete_mod)
            self.mods_layout.insertWidget(self.mods_layout.count() - 1, row)
        self.mods_empty.setVisible(not shown)
        if not shown:
            self.mods_empty.action_button.setText("重扫内容" if self.search_text and self.mods else "去下载模组")
        if self.issues:
            self.issues_banner.show()
            self.issues_banner.set_text("；".join(self.issues[:3]) +
                                        (f"（另有 {len(self.issues) - 3} 项）" if len(self.issues) > 3 else ""))
        else:
            self.issues_banner.hide()

    def _render_resources(self, version: InstalledVersion) -> None:
        for kind, host_layout, empty in (
            ("resourcepacks", self.resource_layout, self.resource_empty),
            ("shaderpacks", self.shader_layout, self.shader_empty),
        ):
            while host_layout.count() > 1:
                item = host_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            resources = self.shell.mod_manager.scan_resources(version, kind)
            query = self.search_text.lower()
            if query:
                resources = [r for r in resources if query in r.name.lower()]
            for resource in resources:
                row = ResourceRow(resource, self.theme, kind)
                row.open_requested.connect(self._open_resource)
                row.delete_requested.connect(lambda r, k=kind: self._delete_resource(r, k))
                if kind == "resourcepacks":
                    row.toggled.connect(self._toggle_resourcepack)
                host_layout.insertWidget(host_layout.count() - 1, row)
            empty.setVisible(not resources)

    def _render_saves(self, version: InstalledVersion) -> None:
        while self.saves_layout.count() > 1:
            item = self.saves_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        saves = self.shell.mod_manager.scan_saves(version)
        query = self.search_text.lower()
        if query:
            saves = [s for s in saves if query in s.name.lower()]
        for save in saves:
            row = SaveRow(save, self.theme)
            row.open_requested.connect(lambda s: open_path(s.path))
            self.saves_layout.insertWidget(self.saves_layout.count() - 1, row)
        self.saves_empty.setVisible(not saves)

    # ------------------------------------------------------------------ actions
    def _version_changed(self, index: int) -> None:
        version_id = self.version_combo.itemData(index)
        if version_id:
            self.selected_id = str(version_id)
            self.shell.set_current_version(self.selected_id, notify=False)
            self.refresh()

    def _switch_tab(self, key: str, force: bool = False) -> None:
        if not force and key == self.active_tab:
            return
        self.active_tab = key
        mapping = {"mods": self.mods_page, "resourcepacks": self.resource_page,
                   "shaderpacks": self.shader_page, "saves": self.saves_page}
        self.stack.setCurrentWidget(mapping.get(key, self.mods_page))
        self.tabs.blockSignals(True)
        self.tabs.set_current(key)
        self.tabs.blockSignals(False)
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.search_text = ""
        self._render_all_after_search()
        self._update_import_labels()
        self.search.setPlaceholderText("搜索已安装内容…" if key != "saves" else "搜索存档…")

    def _update_import_labels(self) -> None:
        if self.active_tab == "mods":
            self.import_button.setText("导入模组")
            self.add_mod_button.setText("添加文件")
        elif self.active_tab == "resourcepacks":
            self.import_button.setText("导入资源包")
            self.add_mod_button.setText("添加文件")
        elif self.active_tab == "shaderpacks":
            self.import_button.setText("导入光影")
            self.add_mod_button.setText("添加文件")
        else:
            self.import_button.setText("打开存档目录")
            self.add_mod_button.setText("打开文件夹")

    def _render_all_after_search(self) -> None:
        version = self.shell.minecraft.find(self.selected_id)
        if not version:
            return
        self._render_mods()
        self._render_resources(version)
        self._render_saves(version)

    def _search(self, text: str) -> None:
        self.search_text = text.strip()
        self._render_all_after_search()

    def _toggle_mod(self, mod: ModInfo, checked: bool) -> None:
        try:
            self.shell.mod_manager.toggle(mod)
            self.shell.toast(f"已{'启用' if checked else '停用'} {mod.name}", "success")
        except (LauncherError, OSError) as exc:
            self.shell.toast(str(exc), "error")
        self.refresh()
        self.shell.refresh_content_counts()

    def _toggle_resourcepack(self, resource: Any, checked: bool) -> None:
        version = self.shell.current_version()
        if not version:
            self.shell.toast("请先选择一个版本", "warning")
            return
        try:
            self.shell.mod_manager.set_resourcepack_enabled(version, resource, checked)
            self.shell.toast(
                f"已{'加载' if checked else '取消加载'}材质包 {resource.name}", "success")
        except (LauncherError, OSError) as exc:
            self.shell.toast(str(exc), "error")
        self.refresh()

    def _delete_mod(self, mod: ModInfo) -> None:
        if not ConfirmDialog.ask(
                self, self.theme, "移除模组",
                f"把「{mod.name}」移到启动器回收站吗？你之后可以手动恢复。",
                "移除", danger=True):
            return
        try:
            self.shell.mod_manager.delete(mod)
            self.shell.toast(f"已移除 {mod.name}", "success")
        except (LauncherError, OSError) as exc:
            self.shell.toast(str(exc), "error")
        self.refresh()
        self.shell.refresh_content_counts()

    def _import_modpack(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入整合包", "", "Minecraft 整合包 (*.mrpack *.zip);;所有文件 (*)")
        if path:
            self.shell.import_local_modpack(Path(path))

    def _import_mods(self) -> None:
        if self.active_tab == "saves":
            self._open_path("saves")
            return
        if self.active_tab in {"resourcepacks", "shaderpacks"}:
            self._import_resource(self.active_tab)
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "选择模组文件", "", "Minecraft Mod (*.jar)")
        if not paths:
            return
        version = self.shell.minecraft.find(self.selected_id)
        if not version:
            return
        imported = 0
        errors = []
        for path in paths:
            try:
                self.shell.mod_manager.import_file(Path(path), version)
                imported += 1
            except (LauncherError, OSError) as exc:
                errors.append(f"{Path(path).name}：{exc}")
        if imported:
            self.shell.toast(f"已导入 {imported} 个模组", "success")
        if errors:
            self.shell.toast(errors[0], "error")
        self.refresh()
        self.shell.refresh_content_counts()

    def _add_for_tab(self) -> None:
        if self.active_tab == "mods":
            self._import_mods()
        elif self.active_tab in {"resourcepacks", "shaderpacks"}:
            self._import_resource(self.active_tab)
        else:
            self._open_path("saves")

    def _import_resource(self, kind: str) -> None:
        version = self.shell.minecraft.find(self.selected_id)
        if not version:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "选择资源包", "",
                                                "Minecraft 资源包 (*.zip);;所有文件 (*.*)")
        if not paths:
            return
        folder = (self.shell.minecraft.resourcepacks_dir(version, create=True)
                  if kind == "resourcepacks" else self.shell.minecraft.shaderpacks_dir(version, create=True))
        count = 0
        for path in paths:
            source = Path(path)
            target = folder / source.name
            if target.exists():
                self.shell.toast(f"{source.name} 已存在", "warning")
                continue
            try:
                shutil.copy2(source, target)
                count += 1
            except OSError as exc:
                self.shell.toast(f"导入失败：{exc}", "error")
        if count:
            self.shell.toast(f"已导入 {count} 个文件", "success")
            self.refresh()

    def _open_resource(self, resource: ResourceInfo) -> None:
        parent = resource.path.parent if resource.path.is_file() else resource.path
        open_path(parent)

    def _delete_resource(self, resource: ResourceInfo, kind: str) -> None:
        if not ConfirmDialog.ask(
                self, self.theme, "移除内容",
                f"要从磁盘删除「{resource.name}」吗？此操作不可撤销。",
                "删除", danger=True):
            return
        try:
            if resource.path.is_dir():
                shutil.rmtree(resource.path)
            else:
                resource.path.unlink()
            self.shell.toast("已删除", "success")
        except OSError as exc:
            self.shell.toast(f"删除失败：{exc}", "error")
        self.refresh()

    def _open_game_folder(self) -> None:
        version = self.shell.minecraft.find(self.selected_id)
        if version:
            open_path(self.shell.minecraft.game_dir(version))

    def _open_path(self, key: str) -> None:
        version = self.shell.minecraft.find(self.selected_id)
        if not version:
            return
        path = self.shell.minecraft.saves_dir(version, create=True) if key == "saves" else version.path
        open_path(path)

    def _export_pack(self) -> None:
        version = self.shell.minecraft.find(self.selected_id)
        if not version:
            self.shell.toast("请先选择一个版本", "warning")
            return
        dialog = ExportPackDialog(self.theme, version, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        data = dialog.result_data
        if not data.get("output"):
            self.shell.toast("请选择保存位置", "warning")
            return
        output = Path(data["output"])
        if output.suffix.lower() not in {".mrpack", ".zip"}:
            output = output.with_suffix(".mrpack")

        def job(progress, cancel):
            state = {"percent": 0}
            def report(pct, message):
                state["percent"] = pct
                progress((pct, message))
            def log(message):
                progress((state["percent"], message))
            return self.shell.modpack.export_mrpack(
                version, output, name=data["name"], pack_version=data["pack_version"],
                summary=data["summary"], include_config=data["include_config"],
                progress=report, log=log, cancel=cancel,
            )

        progress_dialog = ProgressJobDialog(
            self.theme, "导出整合包", "正在把模组与配置装入 .mrpack",
            job, success_text="整合包已经装订完毕。", width=650, height=490, parent=self)
        if progress_dialog.run() == QDialog.DialogCode.Accepted:
            self.shell.toast(f"已导出：{output.name}", "success")
            try:
                open_path(output.parent)
            except OSError:
                pass

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        version = self.shell.minecraft.find(self.selected_id)
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            suffix = path.suffix.lower()
            if suffix == ".mrpack":
                self.shell.import_local_modpack(path)
                break
            if suffix == ".jar":
                if not version:
                    self.shell.toast("请先选择要放入的版本", "warning")
                    continue
                try:
                    self.shell.mod_manager.import_file(path, version)
                    self.shell.toast(f"已导入 {path.name}", "success")
                except (LauncherError, OSError) as exc:
                    self.shell.toast(str(exc), "error")
                self.refresh()
                break
            if suffix == ".zip" and self.active_tab in {"resourcepacks", "shaderpacks"}:
                if not version:
                    self.shell.toast("请先选择要放入的版本", "warning")
                    continue
                folder = (self.shell.minecraft.resourcepacks_dir(version, create=True)
                          if self.active_tab == "resourcepacks"
                          else self.shell.minecraft.shaderpacks_dir(version, create=True))
                try:
                    shutil.copy2(path, folder / path.name)
                    self.shell.toast(f"已导入 {path.name}", "success")
                    self.refresh()
                except OSError as exc:
                    self.shell.toast(str(exc), "error")
                break
        event.acceptProposedAction()

    def focus_tab(self, key: str) -> None:
        if key in {"mods", "resourcepacks", "shaderpacks", "saves"}:
            self._switch_tab(key, force=True)

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        for pill in self.count_pills.values():
            pill.set_theme(theme)
        if self.isVisible():
            self.refresh()
