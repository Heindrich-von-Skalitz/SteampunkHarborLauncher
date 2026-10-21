from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtCore import QUrl

from ...core.mods import ModManager
from ...core.modpack import record_source
from ...services.curseforge import CurseForgeService
from ...services.modrinth import ModrinthService
from ...utils import format_number, format_size, parse_iso, relative_time
from .. import icons
from ..dialogs import BaseDialog, ConfirmDialog, ProgressJobDialog
from ..tasks import run_task
from ..theme import Theme
from ..widgets import (
    Card, EmptyState, GearSpinner, InfoBanner, PageHeader, Pill, ProjectIcon,
    ScrollPage, SearchBox, SegmentedControl, ToggleSwitch, icon_button, label,
    make_button, repolish,
)


class VersionRow(QFrame):
    install_requested = pyqtSignal(str)

    def __init__(self, entry: dict[str, Any], theme: Theme, installed: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.entry = entry
        self.theme = theme
        self.setObjectName("VersionCard")
        self.setFixedHeight(68)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 8, 13, 8)
        layout.setSpacing(12)
        icon_frame = QFrame()
        icon_frame.setFixedSize(38, 38)
        icon_frame.setStyleSheet(
            f"background: {theme.surface_alt}; border: 1px solid {theme.border_soft}; border-radius: 9px;")
        il = QHBoxLayout(icon_frame)
        il.setContentsMargins(0, 0, 0, 0)
        icon_label = QLabel()
        icon_label.setPixmap(icons.pixmap("layers", theme.text_muted, 18))
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        il.addWidget(icon_label)
        layout.addWidget(icon_frame)
        body = QVBoxLayout()
        body.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(7)
        version_id = str(entry.get("id") or "")
        title = QLabel(version_id)
        title.setStyleSheet(f"color: {theme.text}; font-size: 13px; font-weight: 600;")
        title_row.addWidget(title)
        type_key = str(entry.get("type") or "release")
        type_map = {"release": ("正式版", "success"), "snapshot": ("快照", "accent"),
                    "old_beta": ("旧测试版", "muted"), "old_alpha": ("旧远古版", "muted")}
        type_text, tone = type_map.get(type_key, (type_key, "muted"))
        pill = Pill(type_text, tone)
        pill.set_theme(theme)
        title_row.addWidget(pill)
        if installed:
            installed_pill = Pill("已安装", "secondary")
            installed_pill.set_theme(theme)
            title_row.addWidget(installed_pill)
        title_row.addStretch()
        body.addLayout(title_row)
        release = entry.get("releaseTime") or entry.get("time") or ""
        meta = QLabel(f"发布于 {_date_label(release)}")
        meta.setStyleSheet(f"color: {theme.text_faint}; font-size: 10px;")
        body.addWidget(meta)
        layout.addLayout(body, 1)
        self.install = make_button("安装" if not installed else "重装", "install")
        self.install.clicked.connect(lambda: self.install_requested.emit(version_id))
        layout.addWidget(self.install)
        detail = icon_button("chevron_right", "查看详情", icon_role="faint", size=14)
        detail.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl(f"https://minecraft.wiki/w/Java_Edition_{version_id}")))
        layout.addWidget(detail)


def _date_label(value: str) -> str:
    dt = parse_iso(value)
    if not dt:
        return "未知日期"
    return dt.astimezone().strftime("%Y-%m-%d")


_SNAPSHOT_RE = re.compile(r"^(\d{2})w(\d{2})([a-z])$", re.I)
_PRERELEASE_RE = re.compile(
    r"^(\d+(?:\.\d+)*)[-_.]?(pre|rc|snapshot|weekly|alpha|beta)[-_.]?(\d+)?$", re.I)


def _mc_version_key(value: str) -> tuple:
    """Sort Minecraft versions high-to-low without network data.

    Releases compare numerically; release candidates sort above pre-releases
    of the same base version. Weekly snapshots cannot be placed relative to
    releases without their publication date, so the date-less fallback keeps
    them below releases. ``_order_game_versions`` uses real dates whenever the
    version manifest or the project files provide them.
    """

    text = str(value or "").strip()
    snapshot = _SNAPSHOT_RE.match(text)
    if snapshot:
        year, week, letter = snapshot.groups()
        return (0, 2000 + int(year), int(week), ord(letter.lower()) - 96,
                0, 0, 0, text.casefold())
    prerelease = _PRERELEASE_RE.match(text)
    if prerelease:
        base, stage, number = prerelease.groups()
        numbers = tuple(int(part) for part in base.split("."))
        padded = numbers + (0,) * (4 - len(numbers))
        stage_rank = 2 if stage.lower() == "rc" else 1
        return (1,) + padded + (stage_rank, int(number or 0), text.casefold())
    numbers = tuple(int(part) for part in re.findall(r"\d+", text)[:4])
    padded = numbers + (0,) * (4 - len(numbers))
    return (1,) + padded + (3, 0, text.casefold())


def _sorted_game_versions(values: Any) -> list[str]:
    unique = {str(item) for item in (values or []) if item}
    return sorted(unique, key=_mc_version_key, reverse=True)


def _order_game_versions(groups: dict[str, list[dict[str, Any]]], target_version: str,
                         release_times: dict[str, str] | None = None) -> list[str]:
    """Order Minecraft version groups newest-first, pinning the target group.

    Manifest release times are authoritative because they also place snapshots
    correctly among releases. Project file dates are the fallback, and the
    structural version key is the last resort.
    """

    release_times = release_times or {}

    def stamp(value: str) -> float:
        parsed = parse_iso(value)
        return parsed.timestamp() if parsed else 0.0

    def order_key(version: str) -> tuple:
        manifest_stamp = stamp(str(release_times.get(version) or ""))
        if manifest_stamp:
            return (2, manifest_stamp, _mc_version_key(version))
        file_stamps = [stamp(str(row.get("date") or "")) for row in groups.get(version, [])]
        newest_file = max(file_stamps) if file_stamps else 0.0
        if newest_file:
            return (1, newest_file, _mc_version_key(version))
        return (0, 0.0, _mc_version_key(version))

    ordered = sorted(groups, key=order_key, reverse=True)
    if target_version in groups:
        ordered.remove(target_version)
        ordered.insert(0, target_version)
    return ordered


class LoaderChoiceDialog(BaseDialog):
    def __init__(self, theme: Theme, shell: Any, base_version: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(theme, f"安装 Minecraft {base_version}",
                         "选择原版或加载器，然后由下载页自动补全文件",
                         width=660, height=470, icon_name="loader", parent=parent)
        self.shell = shell
        self.base_version = base_version
        self.selected_loader = "vanilla"
        body = self.body_layout
        info = Card(padding=16, spacing=9)
        title_row = QHBoxLayout()
        title_row.setSpacing(9)
        self.loader_block_label = QLabel()
        self.loader_block_label.setPixmap(icons.pixmap("box", theme.accent, 22))
        self.loader_block_label.setFixedSize(25, 25)
        self.loader_block_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_row.addWidget(self.loader_block_label)
        title = label(base_version, "section")
        title_row.addWidget(title)
        title_row.addWidget(Pill("官方版本", "muted"))
        title_row.addStretch()
        info.body.addLayout(title_row)
        release_time = ""
        for entry in getattr(shell, "manifest_versions", []):
            if entry.get("id") == base_version:
                release_time = entry.get("releaseTime") or ""
                break
        info.body.addWidget(label(
            f"安装将补全客户端、支持库、资源文件与 Natives。"
            + (f" 发布于 {_date_label(release_time)}。" if release_time else ""),
            "muted", wrap=True))
        body.addWidget(info)
        body.addWidget(label("选择安装方式", "cardTitle"))
        self.tabs = SegmentedControl([
            ("vanilla", "原版"), ("fabric", "Fabric"), ("forge", "Forge"),
            ("neoforge", "NeoForge"), ("quilt", "Quilt"),
        ], "vanilla")
        self.tabs.changed.connect(self._loader_changed)
        body.addWidget(self.tabs)
        self.loader_note = InfoBanner(
            "将安装纯净的官方 Minecraft，不附加任何加载器。", "info", "info")
        body.addWidget(self.loader_note)
        self.loader_version = QComboBox()
        self.loader_version.setMinimumHeight(34)
        self.loader_version.setMaxVisibleItems(18)
        self.loader_version.addItem("自动选择最新稳定版", "")
        self.loader_version.hide()
        body.addWidget(self.loader_version)
        body.addWidget(label("版本显示名称（可选，支持中文）", "faint"))
        self.version_name_edit = QLineEdit()
        self.version_name_edit.setPlaceholderText("例如：机械冒险港·主世界")
        body.addWidget(self.version_name_edit)
        self._loader_request = 0
        self.java_note = InfoBanner("", "warning", "java")
        self.java_note.hide()
        body.addWidget(self.java_note)
        body.addStretch()
        self.add_footer_button("取消", "secondary", callback=self.reject)
        self.install_button = self.add_footer_button("开始安装", "primary", "download", self._accept)
        self._loader_changed("vanilla")

    def _loader_changed(self, loader: str) -> None:
        self.selected_loader = loader
        self._loader_request += 1
        request = self._loader_request
        self.loader_block_label.setPixmap(
            icons.pixmap(icons.loader_icon_name(loader), self.theme.accent, 22))
        names = {"vanilla": "原版", "fabric": "Fabric", "forge": "Forge",
                 "neoforge": "NeoForge", "quilt": "Quilt"}
        if loader == "vanilla":
            self.loader_version.hide()
            self.java_note.hide()
            self.install_button.setEnabled(True)
            self.loader_note.set_text("将安装纯净的官方 Minecraft，不附加任何加载器。")
            return
        self.loader_version.show()
        self.loader_version.clear()
        self.loader_version.addItem("加载器版本列表中…", "")
        self.loader_version.setEnabled(False)
        if loader in {"forge", "neoforge"}:
            java = self.shell.java_for_install(self.base_version)
            if java:
                self.java_note.show()
                self.java_note.set_text(
                    f"官方安装处理器将使用 Java {java.major}（{java.vendor}）。安装时间可能较长。")
                self.install_button.setEnabled(True)
            else:
                required = self.shell.required_java_for(self.base_version)
                detected = self.shell.detected_java_majors()
                detected_text = "、".join(f"Java {major}" for major in detected) or "没有检测到 Java"
                self.java_note.show()
                self.java_note.set_text(
                    f"该版本需要 Java {required}+；当前检测到 {detected_text}。"
                    f"点击“开始安装”会自动提示下载 Temurin {required}。")
                self.install_button.setEnabled(True)
            if loader == "neoforge":
                self.loader_note.set_text(
                    "NeoForge 会按所选 Minecraft 的精确补丁匹配版本；该补丁没有稳定版时自动使用官方测试版。")
            else:
                self.loader_note.set_text(
                    f"{names[loader]} 使用官方安装处理器安装；首次安装需要下载较多组件。")
        else:
            self.java_note.hide()
            self.install_button.setEnabled(True)
            self.loader_note.set_text(
                f"{names[loader]} 将自动下载加载器配置与依赖库，安装完成后即可加入模组。")

        def job(progress, cancel):
            return self.shell.loaders.loader_versions(loader, self.base_version)

        def done(result: object) -> None:
            if request != self._loader_request:
                return
            self.loader_version.clear()
            self.loader_version.addItem("自动选择最新稳定版", "")
            rows = result if isinstance(result, list) else []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                version = str(row.get("version") or "")
                if not version:
                    continue
                stable = bool(row.get("stable", False))
                self.loader_version.addItem(version + (" · 稳定" if stable else " · 测试"), version)
            self.loader_version.setEnabled(True)
            if len(self.loader_version) <= 1:
                self.loader_version.setItemText(0, "没有可选版本，自动选择")
                self.loader_note.set_text(
                    f"没有获取到 {names[loader]} 的可用版本；仍可尝试自动安装。")

        def failed(message: str) -> None:
            if request != self._loader_request:
                return
            self.loader_version.clear()
            self.loader_version.addItem("版本列表获取失败，自动选择", "")
            self.loader_version.setEnabled(True)
            self.loader_note.set_text(
                f"{names[loader]} 版本列表获取失败：{message[:160]}；仍可尝试自动安装。")

        run_task(self, job, on_done=done, on_error=failed, name="shl-loader-versions")

    def _accept(self) -> None:
        base_version = self.base_version
        loader = self.selected_loader
        loader_version = str(self.loader_version.currentData() or "")
        display_name = self.version_name_edit.text().strip()
        self.accept()
        QTimer.singleShot(0, lambda: self.shell.start_install(
            base_version, loader=loader, loader_version=loader_version,
            display_name=display_name))


class GameVersionsTab(QWidget):
    def __init__(self, page: "DownloadPage") -> None:
        super().__init__()
        self.page = page
        self.shell = page.shell
        self.theme = page.theme
        self.versions: list[dict[str, Any]] = []
        self.rows: list[VersionRow] = []
        self.filter_key = "release"
        self.search_text = ""
        self.loading = False
        self._build()

    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        main = QVBoxLayout()
        main.setSpacing(10)
        filter_row = QHBoxLayout()
        filter_row.setSpacing(9)
        self.filters = SegmentedControl([("release", "正式版"), ("snapshot", "快照"),
                                         ("all", "全部")], "release")
        self.filters.changed.connect(self._set_filter)
        filter_row.addWidget(self.filters)
        self.search = SearchBox("搜索 Minecraft 版本…")
        self.search.setFixedWidth(235)
        self.search.textChanged.connect(self._search)
        filter_row.addWidget(self.search)
        filter_row.addStretch()
        self.only_installed = ToggleSwitch(False)
        self.only_installed.toggled.connect(lambda _checked: self._render())
        filter_row.addWidget(label("仅看已安装", "faint"))
        filter_row.addWidget(self.only_installed)
        refresh = icon_button("refresh", "刷新版本清单", icon_role="muted", size=16)
        refresh.clicked.connect(self.load)
        filter_row.addWidget(refresh)
        main.addLayout(filter_row)
        self.info_banner = InfoBanner("正在获取版本列表…", "info", "download_cloud")
        main.addWidget(self.info_banner)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 4, 0)
        self.list_layout.setSpacing(7)
        self.list_layout.addStretch()
        self.scroll.setWidget(self.list_host)
        main.addWidget(self.scroll, 1)
        self.footer = QHBoxLayout()
        self.footer_label = label("0 个版本", "faint")
        self.footer.addWidget(self.footer_label)
        self.footer.addStretch()
        self.footer.addWidget(label("来源：Mojang / BMCLAPI 缓存", "faint"))
        main.addLayout(self.footer)
        layout.addLayout(main, 1)

        # Side rail
        rail = QVBoxLayout()
        rail.setSpacing(12)
        rail.setContentsMargins(0, 0, 0, 0)
        latest = Card(padding=16, spacing=9)
        latest.body.addWidget(label("最新正式版", "eyebrow"))
        self.latest_id = label("读取中…", "section")
        latest.body.addWidget(self.latest_id)
        self.latest_meta = label("正在获取版本清单", "faint", wrap=True)
        latest.body.addWidget(self.latest_meta)
        self.latest_button = make_button("安装最新版", "primary", "download", "on_accent", 15)
        self.latest_button.setEnabled(False)
        self.latest_button.clicked.connect(self._install_latest)
        latest.body.addWidget(self.latest_button)
        rail.addWidget(latest)
        rail.addStretch()
        rail_widget = QWidget()
        rail_widget.setFixedWidth(230)
        rail_widget.setLayout(rail)
        layout.addWidget(rail_widget)

    def load(self) -> None:
        if self.loading:
            return
        self.loading = True
        self.info_banner.set_text("正在获取版本列表…")
        self.latest_button.setEnabled(False)

        def job(progress, cancel):
            progress((10, "获取版本清单…"))
            manifest = self.shell.mojang.get_manifest(force=True)
            progress((60, "整理正式版与快照…"))
            versions = [v for v in manifest.get("versions", []) if isinstance(v, dict)]
            latest = manifest.get("latest") or {}
            return versions, latest, manifest.get("_source") or "Mojang"

        run_task(self, job, on_done=self._loaded, on_error=self._failed,
                 on_progress=lambda val: self._progress(val), name="manifest")

    def _progress(self, value: object) -> None:
        if isinstance(value, tuple) and len(value) > 1:
            self.info_banner.set_text(str(value[1]))

    def _loaded(self, result: object) -> None:
        self.loading = False
        if not isinstance(result, tuple) or len(result) < 3:
            self._failed("版本清单格式异常。")
            return
        versions, latest, source = result
        self.shell.manifest_versions = versions
        self.shell.latest_release = str(latest.get("release") or "")
        self.versions = versions
        self.latest_id.setText(self.shell.latest_release or "未知版本")
        latest_time = next((v.get("releaseTime") for v in versions
                            if v.get("id") == self.shell.latest_release), "")
        self.latest_meta.setText(
            f"发布于 {_date_label(latest_time)} · 通过 {source} 获取" if latest_time else f"来源：{source}")
        self.latest_button.setEnabled(bool(self.shell.latest_release))
        self.info_banner.set_tone("success", "check_circle")
        self.info_banner.set_text(f"共 {len(self.versions)} 个版本 · 来源 {source}")
        self._render()
        self.page.manifest_ready.emit()

    def _failed(self, message: str) -> None:
        self.loading = False
        self.latest_button.setEnabled(bool(self.shell.latest_release))
        self.info_banner.set_text(message)
        self.info_banner.set_tone("danger", "alert")
        self.footer_label.setText("无法载入版本清单")

    def _render(self) -> None:
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.rows.clear()
        installed_bases = {v.id for v in self.shell.versions} | {v.base_version for v in self.shell.versions}
        filtered = []
        for entry in self.versions:
            if self.filter_key == "release" and entry.get("type") != "release":
                continue
            if self.filter_key == "snapshot" and entry.get("type") != "snapshot":
                continue
            if self.only_installed.isChecked() and entry.get("id") not in installed_bases:
                continue
            if self.search_text and self.search_text.lower() not in str(entry.get("id") or "").lower():
                continue
            filtered.append(entry)
        for entry in filtered:
            installed = str(entry.get("id")) in installed_bases
            row = VersionRow(entry, self.theme, installed=installed)
            row.install_requested.connect(self._open_loader_dialog)
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)
            self.rows.append(row)
        if not filtered:
            empty = QLabel("没有符合筛选条件的版本。")
            empty.setStyleSheet(f"color: {self.theme.text_faint}; padding: 32px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.list_layout.insertWidget(0, empty)
        self.footer_label.setText(f"{len(filtered)} 个版本 · 点击「安装」可同时安装加载器")

    def _set_filter(self, key: str) -> None:
        self.filter_key = key
        self._render()

    def _search(self, text: str) -> None:
        self.search_text = text.strip()
        self._render()

    def _open_loader_dialog(self, version_id: str) -> None:
        LoaderChoiceDialog(self.theme, self.shell, version_id, self).exec()

    def _install_latest(self) -> None:
        if self.shell.latest_release:
            self._open_loader_dialog(self.shell.latest_release)


def _source_label(value: str) -> str:
    return {"auto": "自动选择", "official": "官方源优先", "mirror": "BMCLAPI 镜像优先"}.get(value, value)


class ProjectVersionsDialog(BaseDialog):
    """All files of one project, grouped by Minecraft version (except packs)."""

    def __init__(self, theme: Theme, shell: Any, hit: dict[str, Any],
                 project_type: str, target_version: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(theme, str(hit.get("title") or "项目版本"),
                         "选择任意历史版本安装；非整合包按 Minecraft 版本分组",
                         width=820, height=680, icon_name="layers", parent=parent)
        self.shell = shell
        self.hit = hit
        self.project_type = project_type
        self.target_version = target_version
        self.rows: list[dict[str, Any]] = []
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.host = QWidget()
        self.list_layout = QVBoxLayout(self.host)
        self.list_layout.setContentsMargins(0, 0, 8, 0)
        self.list_layout.setSpacing(7)
        self.scroll.setWidget(self.host)
        self.body_layout.addWidget(self.scroll, 1)
        self.status = label("正在读取全部版本…", "faint")
        self.body_layout.addWidget(self.status)
        self.add_footer_button("关闭", "secondary", callback=self.reject)
        QTimer.singleShot(0, self._load)

    def _load(self) -> None:
        source = str(self.hit.get("source") or "modrinth")
        project_id = str(self.hit.get("project_id") or self.hit.get("slug") or "")
        project_type = self.project_type

        def job(progress, cancel):
            if source == "curseforge":
                files = self.shell.curseforge.files(project_id)
                return [{
                    "mc": (item.get("gameVersions") or [""])[0],
                    "loader": "",
                    "title": str(item.get("fileName") or item.get("displayName") or ""),
                    "date": str(item.get("fileDate") or ""),
                    "raw": item, "source": "curseforge",
                } for item in files]
            versions = self.shell.modrinth.versions(project_id)
            return [{
                "mc": (item.get("game_versions") or [""])[0],
                "loader": ", ".join(str(x) for x in (item.get("loaders") or [])[:2]),
                "title": str(item.get("name") or item.get("version_number") or "未命名版本"),
                "date": str(item.get("date_published") or ""),
                "raw": item, "source": "modrinth",
            } for item in versions]

        run_task(self, job, on_done=self._render, on_error=self._failed,
                 name="project-versions")

    def _failed(self, message: str) -> None:
        self.status.setText(f"读取版本失败：{message}")

    def _render(self, data: object) -> None:
        rows = [item for item in (data if isinstance(data, list) else [])
                if isinstance(item, dict)]
        self.rows = rows
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not rows:
            self.status.setText("这个项目没有可下载版本。")
            self.list_layout.addWidget(label("没有可下载版本", "faint"))
            return
        self.status.setText(f"共 {len(rows)} 个版本 · 点击安装")
        if self.project_type == "modpack":
            for row in sorted(rows, key=lambda x: str(x.get("date") or ""), reverse=True):
                self.list_layout.addWidget(self._row_widget(row))
        else:
            groups: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                groups.setdefault(str(row.get("mc") or "未知版本"), []).append(row)
            release_times = {
                str(entry.get("id") or ""): str(entry.get("releaseTime") or "")
                for entry in (getattr(self.shell, "manifest_versions", None) or [])
                if isinstance(entry, dict)
            }
            ordered = _order_game_versions(groups, self.target_version, release_times)
            for version in ordered:
                header = QHBoxLayout()
                title = label(f"Minecraft {version}", "cardTitle")
                header.addWidget(title)
                if version == self.target_version:
                    badge = Pill("当前版本", "accent")
                    badge.set_theme(self.theme)
                    header.addWidget(badge)
                header.addStretch()
                wrapper = QWidget()
                wrapper.setLayout(header)
                self.list_layout.addWidget(wrapper)
                for row in sorted(groups[version],
                                  key=lambda x: str(x.get("date") or ""), reverse=True):
                    self.list_layout.addWidget(self._row_widget(row))
        self.list_layout.addStretch()

    def _row_widget(self, row: dict[str, Any]) -> QWidget:
        frame = QFrame()
        frame.setObjectName("SubCard")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label(str(row.get("title") or "未命名"), "muted"))
        meta = " · ".join(part for part in (
            f"Minecraft {row.get('mc')}" if row.get("mc") else "",
            str(row.get("loader") or ""),
            _date_label(str(row.get("date") or "")),
        ) if part)
        col.addWidget(label(meta, "faint"))
        layout.addLayout(col, 1)
        install = make_button("安装", "secondary", "download", "muted", 13)
        install.clicked.connect(lambda: self._install(row))
        layout.addWidget(install)
        return frame

    def _install(self, row: dict[str, Any]) -> None:
        if str(row.get("source")) == "curseforge":
            self.shell.install_curseforge_project(
                self.hit, self.project_type, selected_file=row.get("raw"))
        else:
            self.shell.install_modrinth_project(
                self.hit, self.project_type, selected_version=row.get("raw"))
        self.status.setText("已开始下载，关闭此窗口不会中断安装。")


class ProjectCard(QFrame):
    install_requested = pyqtSignal(dict)
    open_requested = pyqtSignal(dict)
    detail_requested = pyqtSignal(dict)

    def __init__(self, hit: dict[str, Any], project_type: str, theme: Theme,
                 compatible_current: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.hit = hit
        self.project_type = project_type
        self.theme = theme
        self.setObjectName("ProjectCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(144)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 13, 14, 13)
        layout.setSpacing(12)
        icon = ProjectIcon(48)
        icon.set_fallback(str(hit.get("title") or "P"))
        icon.set_theme(theme)
        icon.set_icon_url(str(hit.get("icon_url") or ""))
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        body = QVBoxLayout()
        body.setSpacing(3)
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        title = QLabel(str(hit.get("title") or "未命名"))
        title.setStyleSheet(f"color: {theme.text}; font-size: 13px; font-weight: 600;")
        title_row.addWidget(title)
        if compatible_current:
            badge = Pill("当前版本", "accent")
            badge.set_theme(theme)
            title_row.addWidget(badge)
        title_row.addStretch()
        open_button = icon_button("external", "在内容源中查看", icon_role="faint", size=13)
        open_button.clicked.connect(lambda: self.open_requested.emit(self.hit))
        title_row.addWidget(open_button)
        body.addLayout(title_row)
        subtitle = f"by {hit.get('author') or '未知作者'}"
        author = QLabel(subtitle)
        author.setStyleSheet(f"color: {theme.text_faint}; font-size: 10px;")
        body.addWidget(author)
        description = QLabel(str(hit.get("description") or ""))
        description.setStyleSheet(f"color: {theme.text_muted}; font-size: 11px;")
        description.setWordWrap(True)
        description.setMaximumHeight(34)
        body.addWidget(description)
        meta = QHBoxLayout()
        meta.setSpacing(10)
        downloads = QLabel(f"↓ {format_number(hit.get('downloads'))}")
        downloads.setStyleSheet(f"color: {theme.text_faint}; font-size: 10px;")
        meta.addWidget(downloads)
        follows = QLabel(f"♡ {format_number(hit.get('follows'))}")
        follows.setStyleSheet(f"color: {theme.text_faint}; font-size: 10px;")
        meta.addWidget(follows)
        categories = [c for c in (hit.get("categories") or []) if c in {"fabric", "forge", "neoforge", "quilt"}]
        if categories:
            cat = QLabel(" · ".join(c.title() for c in categories))
            cat.setStyleSheet(f"color: {theme.accent}; font-size: 10px;")
            meta.addWidget(cat)
        meta.addStretch()
        body.addLayout(meta)
        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        versions = _sorted_game_versions(hit.get("versions") or [])
        version_text = "支持 " + ", ".join(versions[:3]) + ("…" if len(versions) > 3 else "") if versions else "支持版本未知"
        version_label = QLabel(version_text)
        version_label.setStyleSheet(f"color: {theme.text_faint}; font-size: 9px;")
        bottom.addWidget(version_label, 1)
        self.install_button = make_button("安装", "install", "download", "accent", 13)
        self.install_button.clicked.connect(lambda: self.install_requested.emit(self.hit))
        bottom.addWidget(self.install_button)
        body.addLayout(bottom)
        layout.addLayout(body, 1)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.detail_requested.emit(self.hit)
        super().mouseReleaseEvent(event)

    def set_installing(self, busy: bool, text: str = "下载中…") -> None:
        self.install_button.setEnabled(not busy)
        self.install_button.setText(text if busy else "安装")


class ContentTab(QWidget):
    def __init__(self, page: "DownloadPage", project_type: str) -> None:
        super().__init__()
        self.page = page
        self.shell = page.shell
        self.theme = page.theme
        self.project_type = project_type
        self.results: list[dict[str, Any]] = []
        self.offset = 0
        self.page_index = 0
        self.limit = 20
        self.total = 0
        self.request_generation = 0
        self.loading = False
        self.loaded_once = False
        self.cards: list[ProjectCard] = []
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(500)
        self._search_timer.timeout.connect(lambda: self.search(reset=True))
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        filter_frame = QFrame()
        filter_frame.setObjectName("SectionBox")
        filter_layout = QVBoxLayout(filter_frame)
        filter_layout.setContentsMargins(13, 10, 13, 10)
        filter_layout.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.search_box = SearchBox(f"在 Modrinth / CurseForge 中搜索{ModrinthService.project_type_label(self.project_type)}…")
        self.search_box.textChanged.connect(lambda _text: self._search_timer.start())
        self.search_box.returnPressed.connect(lambda: self.search(reset=True))
        top.addWidget(self.search_box, 1)
        top.addWidget(label("目标版本", "faint"))
        self.version_combo = QComboBox()
        self.version_combo.setMinimumWidth(170)
        self.version_combo.currentIndexChanged.connect(self._version_changed)
        top.addWidget(self.version_combo)
        top.addWidget(label("加载器", "faint"))
        self.loader_combo = QComboBox()
        self.loader_combo.setMinimumWidth(115)
        for key, text in (("auto", "自动"), ("fabric", "Fabric"), ("forge", "Forge"),
                          ("neoforge", "NeoForge"), ("quilt", "Quilt"), ("", "不限")):
            self.loader_combo.addItem(text, key)
        self.loader_combo.currentIndexChanged.connect(lambda _idx: self.search(reset=True))
        top.addWidget(self.loader_combo)
        top.addWidget(label("排序", "faint"))
        self.sort_combo = QComboBox()
        for key, text in (("relevance", "相关度"), ("downloads", "下载量"), ("newest", "最近更新")):
            self.sort_combo.addItem(text, key)
        self.sort_combo.currentIndexChanged.connect(lambda _idx: self.search(reset=True))
        top.addWidget(self.sort_combo)
        self.search_button = make_button("搜索", "primary", "search", "on_accent", 15)
        self.search_button.clicked.connect(lambda: self.search(reset=True))
        top.addWidget(self.search_button)
        filter_layout.addLayout(top)
        note_row = QHBoxLayout()
        self.result_count = label("输入关键词，或者直接搜索热门项目", "faint")
        note_row.addWidget(self.result_count, 1)
        source = Pill("Modrinth + CurseForge", "secondary")
        source.set_theme(self.theme)
        note_row.addWidget(source)
        filter_layout.addLayout(note_row)
        layout.addWidget(filter_frame)
        self.banner = InfoBanner("", "info", "shield")
        self.banner.hide()
        layout.addWidget(self.banner)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 4, 0)
        self.grid.setSpacing(9)
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 1)
        self.scroll.setWidget(self.grid_host)
        layout.addWidget(self.scroll, 1)
        action_row = QHBoxLayout()
        action_row.addStretch()
        self.more_button = make_button("加载更多", "secondary", "plus", "muted", 14)
        self.more_button.clicked.connect(lambda: self.search(reset=False))
        self.more_button.hide()
        action_row.addWidget(self.more_button)
        self.loading_widget = QWidget()
        loading_layout = QHBoxLayout(self.loading_widget)
        loading_layout.setContentsMargins(0, 0, 0, 0)
        loading_layout.setSpacing(7)
        spinner = GearSpinner(15)
        loading_layout.addWidget(spinner)
        loading_layout.addWidget(label("正在联络 Modrinth / CurseForge…", "faint"))
        self.loading_widget.hide()
        action_row.addWidget(self.loading_widget)
        action_row.addStretch()
        layout.addLayout(action_row)
        self.refresh_filters()

    def refresh_filters(self) -> None:
        version = self.shell.current_version()
        self.version_combo.blockSignals(True)
        self.loader_combo.blockSignals(True)
        try:
            self.version_combo.clear()
            self.version_combo.addItem("全部版本", "")
            for v in self.shell.versions:
                self.version_combo.addItem(f"{v.id} · {v.loader_label}", v.id)
            if version:
                index = self.version_combo.findData(version.id)
                if index >= 0:
                    self.version_combo.setCurrentIndex(index)
            else:
                self.version_combo.setCurrentIndex(0)
            loader = version.primary_loader if version and version.loaders else ""
            index = self.loader_combo.findData(loader or "auto")
            if index >= 0:
                self.loader_combo.setCurrentIndex(index)
        finally:
            self.version_combo.blockSignals(False)
            self.loader_combo.blockSignals(False)

    def _version_changed(self, index: int) -> None:
        version_id = self.version_combo.itemData(index)
        if version_id:
            self.shell.set_current_version(str(version_id), notify=False)
            self.refresh_filters()
        self.search(reset=True)

    def _current_instance(self):
        version_id = self.version_combo.currentData() if hasattr(self, "version_combo") else None
        if not version_id:
            return None
        return self.shell.minecraft.find(str(version_id)) or self.shell.current_version()

    def _target_game_version(self) -> str:
        version_id = self.version_combo.currentData() if hasattr(self, "version_combo") else None
        if not version_id:
            # "尚未安装版本" must not silently filter by the newest Minecraft
            # release; that hides Create and most other mods.
            return ""
        version = self._current_instance()
        return version.base_version if version else ""

    def _loader_facet(self) -> str:
        value = self.loader_combo.currentData()
        if value == "auto":
            version = self._current_instance()
            return version.primary_loader if version and version.loaders else ""
        return str(value or "")

    def search(self, *, reset: bool = True) -> None:
        if reset:
            self.offset = 0
            self.page_index = 0
            self.results = []
            self._clear_grid()
        else:
            self.page_index += 1
        self.request_generation += 1
        generation = self.request_generation
        query = self.search_box.text().strip()
        game_version = self._target_game_version()
        loader = self._loader_facet()
        sort_index = str(self.sort_combo.currentData() or "relevance")
        offset = self.page_index * self.limit
        limit = self.limit
        project_type = self.project_type
        curseforge = self.shell.curseforge
        self.loading = True
        self.loading_widget.show()
        self.more_button.setEnabled(False)
        self.banner.hide()
        self.result_count.setText("正在搜索 Modrinth / CurseForge…")

        def job(progress, cancel):
            errors: list[str] = []
            hits: list[dict[str, Any]] = []
            total = 0
            try:
                data = self.shell.modrinth.search(
                    query, project_type=project_type, game_version=game_version,
                    loader=loader, offset=offset, limit=limit, index=sort_index,
                )
                hits.extend(hit for hit in data.get("hits", []) if isinstance(hit, dict))
                total += int(data.get("total_hits") or 0)
            except Exception as exc:  # noqa: BLE001 - one source may be offline
                errors.append(f"Modrinth：{exc}")
            if curseforge.enabled:
                try:
                    data = curseforge.search(
                        query, project_type=project_type, game_version=game_version,
                        loader=loader, offset=offset, limit=limit, index=sort_index,
                    )
                    hits.extend(hit for hit in data.get("hits", []) if isinstance(hit, dict))
                    total += int(data.get("total_hits") or 0)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"CurseForge：{exc}")
            else:
                errors.append("CurseForge 未配置 API Key（设置 → 下载与网络）")
            if not hits and errors:
                raise RuntimeError("；".join(errors))
            unique: list[dict[str, Any]] = []
            seen: set[tuple[str, str]] = set()
            for hit in hits:
                key = (str(hit.get("source") or "modrinth"),
                       str(hit.get("project_id") or hit.get("slug") or ""))
                if key not in seen:
                    seen.add(key)
                    unique.append(hit)
            return {"hits": unique, "total_hits": total, "errors": errors}

        run_task(self, job,
                 on_done=lambda data, gen=generation: self._search_done(gen, data),
                 on_error=lambda message, gen=generation: self._search_error(gen, message),
                 name="content-search")

    def _search_done(self, generation: int, data: object) -> None:
        if generation != self.request_generation:
            return
        self.loading = False
        self.loading_widget.hide()
        self.more_button.setEnabled(True)
        if not isinstance(data, dict):
            self._search_error(generation, "内容源返回了无法识别的数据。")
            return
        hits = [hit for hit in data.get("hits", []) if isinstance(hit, dict)]
        current = self._target_game_version()
        if current:
            hits.sort(key=lambda hit: 0 if current in _sorted_game_versions(
                hit.get("versions") or []) else 1)
        self.results.extend(hits)
        self.offset = len(self.results)
        self.total = int(data.get("total_hits") or 0)
        self._render_results()
        self.result_count.setText(f"共 {format_number(self.total)} 个结果 · 已加载 {len(self.results)} 个")
        self.more_button.setVisible(self.offset < min(self.total, 500))
        errors = [str(item) for item in (data.get("errors") or []) if item]
        if errors:
            self.banner.show()
            self.banner.tone = "warning"
            self.banner.set_text("部分来源不可用：" + "；".join(errors)[:320])
            self.banner.update()
        if not hits and not self.results:
            self._show_empty()
        if not self.loaded_once:
            self.loaded_once = True

    def _search_error(self, generation: int, message: str) -> None:
        if generation != self.request_generation:
            return
        self.loading = False
        self.loading_widget.hide()
        self.more_button.setEnabled(True)
        self.result_count.setText("内容搜索暂时不可用")
        self.banner.show()
        self.banner.tone = "danger"
        self.banner.set_text(f"{message} 请检查网络，或稍后重试。")
        self.banner.update()
        if not self.results:
            self._show_empty(error=True)

    def _clear_grid(self) -> None:
        self.cards = []
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _render_results(self) -> None:
        start_index = len(self.cards)
        current = self._target_game_version()
        for index, hit in enumerate(self.results[start_index:], start_index):
            versions = _sorted_game_versions(hit.get("versions") or [])
            compatible = bool(current and current in versions)
            card = ProjectCard(hit, self.project_type, self.theme,
                               compatible_current=compatible)
            card.install_requested.connect(self._install)
            card.open_requested.connect(self._open_page)
            card.detail_requested.connect(self._open_versions)
            self.grid.addWidget(card, index // 2, index % 2)
            self.cards.append(card)
        self.more_button.setVisible(self.offset < min(self.total, 500))
        if self.results:
            self._ensure_stretch()

    def _open_versions(self, hit: dict[str, Any]) -> None:
        dialog = ProjectVersionsDialog(
            self.theme, self.shell, hit, self.project_type,
            target_version=self._target_game_version(), parent=self)
        dialog.exec()

    def _ensure_stretch(self) -> None:
        # QGridLayout will naturally stretch; add a spacer row for breathing room.
        if self.grid.rowCount() > 0:
            self.grid.setRowStretch(self.grid.rowCount(), 1)

    def _show_empty(self, error: bool = False) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        text = ("内容源暂时无法连接，请稍后再试。" if error else
                "没有找到匹配的项目。换一个关键词，或调整目标版本与加载器。")
        empty = EmptyState(
            "没有找到内容" if not error else "无法连接内容源", text, "package")
        empty.set_theme(self.theme)
        self.grid.addWidget(empty, 0, 0, 1, 2)

    def _install(self, hit: dict[str, Any]) -> None:
        card = next((c for c in self.cards if c.hit is hit), None)
        if card:
            card.set_installing(True, "准备中…")
        if str(hit.get("source") or "") == "curseforge":
            self.shell.install_curseforge_project(hit, self.project_type,
                                                  card=card, page=self)
        else:
            self.shell.install_modrinth_project(hit, self.project_type,
                                                card=card, page=self)

    def _open_page(self, hit: dict[str, Any]) -> None:
        url = str(hit.get("website_url") or "")
        if not url:
            slug = hit.get("slug") or hit.get("project_id")
            if str(hit.get("source") or "") == "curseforge":
                url = CurseForgeService.project_url(hit)
            elif slug:
                url = ModrinthService.project_url(str(slug))
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def on_shown(self) -> None:
        self.refresh_filters()
        if not self.loaded_once:
            self.search(reset=True)

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.banner.set_theme(theme)
        if not self.isVisible():
            self._clear_grid()
            self.results = []
            self.loaded_once = False
            return
        self._clear_grid()
        self.cards = []
        self._render_results()
        self.loaded_once = bool(self.results)


class DownloadPage(QWidget):
    manifest_ready = pyqtSignal()

    def __init__(self, shell: Any, theme: Theme) -> None:
        super().__init__()
        self.shell = shell
        self.theme = theme
        self.tabs: dict[str, ContentTab] = {}
        self._build()
        self.refresh_dynamic()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 18)
        outer.setSpacing(12)
        self.header = PageHeader(
            "", "下载",
            "安装 Minecraft 版本、加载器与社区内容。")
        self.source_pill = Pill("", "accent")
        self.source_pill.hide()
        self.header.add_badge(self.source_pill)
        self.switcher = SegmentedControl([
            ("game", "游戏版本"), ("mod", "模组"), ("resourcepack", "资源包"),
            ("shader", "光影"), ("modpack", "整合包"),
        ], "game")
        self.switcher.changed.connect(self._switch_tab)
        self.header.add_action(self.switcher)
        outer.addWidget(self.header)
        self.stack = QStackedWidget()
        self.game_tab = GameVersionsTab(self)
        self.stack.addWidget(self.game_tab)
        outer.addWidget(self.stack, 1)

    def _switch_tab(self, key: str) -> None:
        if key == "game":
            self.stack.setCurrentWidget(self.game_tab)
            return
        if key not in self.tabs:
            tab = ContentTab(self, key)
            self.tabs[key] = tab
            self.stack.addWidget(tab)
        tab = self.tabs[key]
        self.stack.setCurrentWidget(tab)
        tab.on_shown()

    def refresh_dynamic(self) -> None:
        for tab in self.tabs.values():
            tab.refresh_filters()
        self.game_tab._render()

    def on_shown(self) -> None:
        if self.stack.currentWidget() is self.game_tab:
            if not self.game_tab.versions:
                self.game_tab.load()
        else:
            current = self.stack.currentWidget()
            if isinstance(current, ContentTab):
                current.on_shown()

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.source_pill.set_theme(theme)
        self.game_tab.theme = theme
        self.game_tab.info_banner.set_theme(theme)
        if self.game_tab.versions:
            self.game_tab._render()
        for tab in self.tabs.values():
            tab.set_theme(theme)
