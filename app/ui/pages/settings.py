from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
    QStackedWidget, QVBoxLayout, QWidget,
)

from ...constants import APP_AUTHOR, APP_NAME, APP_NAME_EN, APP_VERSION
from ...i18n import LANGUAGES
from ...core.java import JavaInfo
from ...paths import ensure_launcher_metadata, icons_dir
from ...utils import LauncherError, format_size, open_path, sha256_file
from .. import icons
from ..dialogs import AccountDialog, ConfirmDialog, ProgressJobDialog
from ..theme import THEMES, Theme
from ..widgets import (
    Avatar, BrandMark, Card, InfoBanner, PageHeader, Pill, SectionCard, SettingRow, ThemePreview,
    ToggleSwitch, badge, icon_button, label, make_button, repolish,
)


class NavItem(QPushButton):
    def __init__(self, key: str, text: str, icon_name: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.key = key
        self.setProperty("role", "nav")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(True)
        self.setMinimumHeight(39)
        self.setIconSize(QSize(17, 17))
        from ..widgets import set_icon
        set_icon(self, icon_name, "muted", 17)
        self.setStyleSheet("text-align: left; padding-left: 12px;")


class CreditRow(QWidget):
    def __init__(self, letter: str, title: str, detail: str, link_text: str = "",
                 link_url: str = "", badge_text: str = "",
                 image_path: Path | str | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 9, 0, 9)
        layout.setSpacing(11)
        avatar = QLabel(letter)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setFixedSize(38, 38)
        avatar.setStyleSheet(
            "background: #EEF6EF; color: #2F7D3A; border: 1px solid #DCE9DE;"
            " border-radius: 7px; font-family: 'Microsoft YaHei UI', 'Segoe UI';"
            " font-size: 15px; font-weight: 700;")
        if image_path:
            pixmap = QPixmap(str(image_path))
            if not pixmap.isNull():
                avatar.setPixmap(pixmap.scaled(
                    38, 38, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
                avatar.setStyleSheet(
                    "background: transparent; border: 1px solid #DCE9DE;"
                    " border-radius: 7px;")
        layout.addWidget(avatar)
        col = QVBoxLayout()
        col.setSpacing(1)
        title_row = QHBoxLayout()
        title_row.setSpacing(7)
        title_row.addWidget(label(title, "cardTitle"))
        if badge_text:
            title_row.addWidget(badge(badge_text, "muted"))
        title_row.addStretch()
        col.addLayout(title_row)
        desc = label(detail, "faint", wrap=True)
        col.addWidget(desc)
        layout.addLayout(col, 1)
        if link_text and link_url:
            button = make_button(link_text, "ghost", "external", "accent", 13)
            button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(link_url)))
            layout.addWidget(button)


class SettingsPage(QWidget):
    def __init__(self, shell: Any, theme: Theme) -> None:
        super().__init__()
        self.shell = shell
        self.settings = shell.settings
        self.theme = theme
        self.nav_buttons: dict[str, NavItem] = {}
        self.pages: dict[str, QWidget] = {}
        self.theme_tiles: dict[str, QFrame] = {}
        self.source_warnings: list[InfoBanner] = []
        self._build()
        self.refresh_java()
        self.refresh_accounts()
        self.refresh_theme_gallery()
        self.refresh_paths()

    # ------------------------------------------------------------------ shell
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 18)
        outer.setSpacing(14)
        self.header = PageHeader(
            "", "设置",
            "管理启动器选项；更改会立即保存。")
        outer.addWidget(self.header)
        body = QHBoxLayout()
        body.setSpacing(18)
        side = QVBoxLayout()
        side.setSpacing(4)
        side.setContentsMargins(0, 0, 0, 0)
        for key, text, icon_name in (
            ("general", "通用", "home"), ("launch", "启动", "play"),
            ("download", "下载", "download"), ("java", "Java", "java"),
            ("appearance", "外观", "image"), ("accounts", "玩家", "users"),
            ("about", "关于", "info"),
        ):
            button = NavItem(key, text, icon_name)
            button.clicked.connect(lambda _checked=False, k=key: self.show_tab(k))
            side.addWidget(button)
            self.nav_buttons[key] = button
        side.addStretch()
        side_frame = QFrame()
        side_frame.setObjectName("SectionBox")
        side_frame.setFixedWidth(178)
        side_layout = QVBoxLayout(side_frame)
        side_layout.setContentsMargins(10, 12, 10, 12)
        side_layout.setSpacing(4)
        side_layout.addLayout(side)
        body.addWidget(side_frame)
        self.stack = QStackedWidget()
        self._add_page("general", self._build_general())
        self._add_page("launch", self._build_launch())
        self._add_page("download", self._build_download())
        self._add_page("java", self._build_java())
        self._add_page("appearance", self._build_appearance())
        self._add_page("accounts", self._build_accounts())
        self._add_page("about", self._build_about())
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)
        self.show_tab("general")

    def _add_page(self, key: str, page: QWidget) -> None:
        self.pages[key] = page
        self.stack.addWidget(page)

    def _new_scroll_page(self) -> tuple[QScrollArea, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 6, 8)
        layout.setSpacing(12)
        scroll.setWidget(host)
        return scroll, layout

    def show_tab(self, key: str) -> None:
        if key not in self.pages:
            return
        self.stack.setCurrentWidget(self.pages[key])
        self._update_source_warnings()
        for name, button in self.nav_buttons.items():
            button.setChecked(name == key)
            button.setProperty("active", "true" if name == key else "false")
            repolish(button)

    def _mirror_warning(self) -> InfoBanner:
        banner = InfoBanner(
            "当前使用第三方 BMCLAPI 镜像源，元数据未经 Mojang 官方签名验证，"
            "请自行承担风险。", "warning", "shield")
        banner.setVisible(str(self.settings.get("download_source") or "auto") in {"mirror_priority", "mirror_only"})
        return banner

    def _update_source_warnings(self) -> None:
        visible = str(self.settings.get("download_source") or "auto") in {"mirror_priority", "mirror_only"}
        for banner in self.source_warnings:
            banner.setVisible(visible)

    # ------------------------------------------------------------------ helpers
    def _combo(self, items: list[tuple[str, str]], current: str = "") -> QComboBox:
        combo = QComboBox()
        for data, text in items:
            combo.addItem(text, data)
        index = combo.findData(current)
        if index >= 0:
            combo.setCurrentIndex(index)
        return combo

    def _save(self, key: str, value: Any, message: str = "已保存") -> None:
        self.settings.set(key, value)
        if message:
            self.shell.toast(message, "success", 1800)

    def _setting_row(self, title: str, desc: str, control: QWidget, icon_name: str = "") -> SettingRow:
        return SettingRow(title, desc, control, icon_name or None)

    def _executable_hash(self) -> str:
        cached = getattr(self, "_exe_hash_cache", "")
        if cached:
            return cached
        argv0 = Path(sys.argv[0])
        packaged = (getattr(sys, "frozen", False)
                    or "__compiled__" in globals()
                    or argv0.suffix.lower() == ".exe")
        if not packaged:
            value = "开发运行（未打包 exe）"
        else:
            executable = None
            for candidate in (argv0, Path(sys.executable)):
                try:
                    resolved = candidate.resolve()
                except OSError:
                    continue
                if (resolved.is_file() and resolved.suffix.lower() == ".exe"
                        and not resolved.name.lower().startswith("python")):
                    executable = resolved
                    break
            if executable is None:
                value = "无法定位打包后的 exe"
            else:
                try:
                    value = sha256_file(executable)
                except OSError as exc:
                    value = f"读取失败：{exc}"
        self._exe_hash_cache = value
        return value

    def _open(self, path: Path | str) -> None:
        try:
            open_path(path)
        except OSError as exc:
            self.shell.toast(f"无法打开：{exc}", "error")

    # ------------------------------------------------------------------ general
    def _build_general(self) -> QWidget:
        scroll, layout = self._new_scroll_page()
        game = SectionCard("游戏目录", "游戏可以保存在启动器当前目录的 .minecraft、官方启动器目录或任意自定义目录。", "folder")
        mode = str(self.settings.get("minecraft_dir_mode") or "official")
        self.game_mode_combo = self._combo([
            ("current", "当前目录的 .minecraft（便携）"),
            ("official", "官方启动器目录"),
            ("custom", "自定义目录"),
        ], mode)
        self.game_mode_combo.currentIndexChanged.connect(self._game_mode_changed)
        game.add_row(self._setting_row(
            "保存位置", "非官方目录都会在其中创建 .minecraft 子文件夹，versions、libraries、assets、存档等都在该文件夹内。",
            self.game_mode_combo, "folder"))
        path_row = QWidget()
        path_layout = QHBoxLayout(path_row)
        path_layout.setContentsMargins(0, 7, 0, 7)
        path_layout.setSpacing(7)
        self.game_dir_edit = QLineEdit(str(self.settings.minecraft_dir))
        self.game_dir_edit.setReadOnly(True)
        path_layout.addWidget(self.game_dir_edit, 1)
        browse = make_button("选择", "secondary", "folder", "muted", 14)
        browse.clicked.connect(self._browse_game_dir)
        path_layout.addWidget(browse)
        open_btn = make_button("打开", "secondary", "folder_open", "muted", 14)
        open_btn.clicked.connect(lambda: self._open(self.settings.minecraft_dir))
        path_layout.addWidget(open_btn)
        game.add_widget(path_row)
        data_button = make_button("打开目录", "ghost", "folder", "accent", 13)
        data_button.clicked.connect(lambda: self._open(self.shell.data_dir))
        game.add_row(self._setting_row(
            "启动器数据目录", "配置、日志与下载缓存会保存在这里。",
            data_button, "save"))
        logs_button = make_button("打开日志目录", "ghost", "log", "accent", 13)
        logs_button.clicked.connect(lambda: self._open(self.shell.logs_dir()))
        game.add_row(self._setting_row(
            "启动器日志目录", "launcher.log 与游戏日志保存在这里，排错时可直接打开。",
            logs_button, "log"))
        layout.addWidget(game)

        language_combo = self._combo(
            [(key, name) for key, name in LANGUAGES.items()],
            str(self.settings.get("language") or "zh_JT"))
        language_combo.currentIndexChanged.connect(
            lambda: (self._save("language", language_combo.currentData(), "语言已保存"),
                     self.shell.toast("语言将在重启启动器后生效。", "info", 5000)))
        game.add_row(self._setting_row(
            "语言 / Language", "简体中文、繁體中文、English；重启后生效。",
            language_combo, "globe"))
        game_language = ToggleSwitch(
            bool(self.settings.get("apply_language_to_new_versions", True)))
        game_language.toggled.connect(
            lambda checked: self._save(
                "apply_language_to_new_versions", bool(checked),
                "新版本语言设置已保存"))
        game.add_row(self._setting_row(
            "新版本跟随启动器语言",
            "下载完成后写入该版本 options.txt 的 lang；繁體中文使用香港繁中。",
            game_language, "globe"))

        downloads = SectionCard("下载与网络", "下载源会影响版本清单与依赖库的获取速度。", "download_cloud")
        source_combo = self._combo([
            ("auto", "自动选择（推荐）"),
            ("official_priority", "优先官方源"),
            ("mirror_priority", "优先 BMCLAPI"),
            ("official", "仅官方源"),
            ("mirror_only", "仅 BMCLAPI 镜像"),
        ], str(self.settings.get("download_source") or "auto"))
        source_combo.currentIndexChanged.connect(
            lambda: (self._save("download_source", source_combo.currentData(), "下载源已更新"),
                     self._update_source_warnings()))
        downloads.add_row(self._setting_row(
            "下载源", "自动优先官方；优先模式可回退；仅模式不回退。",
            source_combo, "globe"))
        self.source_warnings.append(self._mirror_warning())
        downloads.add_widget(self.source_warnings[-1])
        self.parallel_spin = QSpinBox()
        self.parallel_spin.setRange(2, 16)
        self.parallel_spin.setValue(int(self.settings.get("parallel_downloads") or 8))
        self.parallel_spin.valueChanged.connect(
            lambda value: self._save("parallel_downloads", value, "并发数已更新"))
        downloads.add_row(self._setting_row(
            "同时下载数", "资源文件补全时的并发连接数；网络不稳定时建议降低。",
            self.parallel_spin, "cpu"))
        self.curseforge_key_edit = QLineEdit(str(self.settings.get("curseforge_api_key") or ""))
        self.curseforge_key_edit.setPlaceholderText("CurseForge API Key（用于搜索/下载 CurseForge 模组）")
        self.curseforge_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.curseforge_key_edit.editingFinished.connect(
            lambda: self._save("curseforge_api_key",
                               self.curseforge_key_edit.text().strip(),
                               "CurseForge API Key 已保存"))
        downloads.add_row(self._setting_row(
            "CurseForge API Key", "CurseForge 官方要求 API Key；留空时只搜索 Modrinth。",
            self.curseforge_key_edit, "key"))
        layout.addWidget(downloads)

        behavior = SectionCard("界面行为", "让启动器更贴合你的使用习惯。", "sliders")
        visibility = self._combo([
            ("keep", "启动游戏后保持窗口"), ("minimize", "启动后最小化"),
        ], str(self.settings.get("launcher_visibility") or "keep"))
        visibility.currentIndexChanged.connect(
            lambda: self._save("launcher_visibility", visibility.currentData()))
        behavior.add_row(self._setting_row(
            "启动后", "决定 Minecraft 进程出现后启动器窗口的状态。", visibility, "monitor"))
        console = self._combo([
            ("always", "总是打开"), ("on_error", "仅启动失败时打开"), ("never", "不自动打开"),
        ], str(self.settings.get("open_console") or "on_error"))
        console.currentIndexChanged.connect(
            lambda: self._save("open_console", console.currentData()))
        behavior.add_row(self._setting_row(
            "游戏日志窗口", "日志窗口会显示 Minecraft 的实时输出，并保存到 logs 目录。",
            console, "terminal"))
        layout.addWidget(behavior)
        layout.addStretch()
        return scroll

    def _game_mode_changed(self, _index: int) -> None:
        mode = str(self.game_mode_combo.currentData() or "custom")
        if mode == "custom":
            if not self._browse_game_dir():
                # Revert to the actual saved mode when the picker is cancelled.
                actual = str(self.settings.get("minecraft_dir_mode") or "custom")
                blocker = self.game_mode_combo.blockSignals(True)
                pos = self.game_mode_combo.findData(actual)
                if pos >= 0:
                    self.game_mode_combo.setCurrentIndex(pos)
                self.game_mode_combo.blockSignals(blocker)
                return
            return
        path = self.settings.set_game_dir_mode(mode)
        self.settings.ensure_minecraft_dirs()
        ensure_launcher_metadata(path, mode)
        self.shell.refresh_all_versions()
        self.refresh_paths()
        self.shell.toast(f"游戏目录已切换到：{path}", "success")

    def _browse_game_dir(self) -> bool:
        path = QFileDialog.getExistingDirectory(self, "选择 .minecraft 所在目录", str(self.settings.minecraft_dir))
        if not path:
            return False
        candidate = Path(path)
        if candidate.name.casefold() != ".minecraft":
            candidate = candidate / ".minecraft"
        self.settings.set("minecraft_dir_mode", "custom")
        self.settings.minecraft_dir = candidate
        self.settings.ensure_minecraft_dirs()
        ensure_launcher_metadata(candidate, "custom")
        self.shell.refresh_all_versions()
        self.refresh_paths()
        self.shell.toast(f"游戏目录已更新：{candidate}", "success")
        return True

    # ------------------------------------------------------------------ launch
    def _build_launch(self) -> QWidget:
        scroll, layout = self._new_scroll_page()
        memory = SectionCard("内存分配", "为 Minecraft 的 Java 虚拟机预留的堆内存。", "memory")
        mode_row = QWidget()
        mode_layout = QHBoxLayout(mode_row)
        mode_layout.setContentsMargins(0, 8, 0, 0)
        mode_layout.setSpacing(12)
        self.memory_slider = QSlider(Qt.Orientation.Horizontal)
        self.memory_slider.setRange(1024, max(2048, int(self.settings.total_ram_mb * 0.8)))
        self.memory_slider.setSingleStep(512)
        self.memory_slider.setPageStep(1024)
        self.memory_slider.setValue(int(self.settings.get("memory_mb") or 4096))
        self.memory_slider.valueChanged.connect(self._memory_changed)
        mode_layout.addWidget(self.memory_slider, 1)
        self.memory_value = label("", "accent")
        self.memory_value.setMinimumWidth(80)
        mode_layout.addWidget(self.memory_value)
        self.memory_auto = ToggleSwitch(str(self.settings.get("memory_mode") or "auto") == "auto")
        self.memory_auto.toggled.connect(self._memory_auto_toggled)
        auto_row = QHBoxLayout()
        auto_row.addWidget(self.memory_auto)
        auto_row.addWidget(label("自动推荐（根据物理内存）", "faint"))
        auto_row.addStretch()
        mode_layout.addLayout(auto_row)
        memory.add_widget(mode_row)
        self.memory_hint = label("", "faint", wrap=True)
        memory.add_widget(self.memory_hint)
        self._memory_changed()
        layout.addWidget(memory)

        preset = SectionCard("性能预设", "默认参数已针对现代 Minecraft 调整。", "cpu")
        gc_combo = self._combo([
            ("auto", "自动（G1GC，推荐）"), ("g1", "强制 G1GC"),
            ("zgc", "ZGC（Java 15+）"), ("none", "不指定 GC，交给自定义参数"),
        ], str(self.settings.get("gc_mode") or "auto"))
        gc_combo.currentIndexChanged.connect(lambda: self._save("gc_mode", gc_combo.currentData()))
        preset.add_row(self._setting_row(
            "垃圾回收器", "G1GC 对大多数整合包更稳妥；ZGC 仅在较新的 Java 上可用。",
            gc_combo, "cpu"))
        visible = self._combo([
            ("keep", "启动后保持窗口"), ("minimize", "启动后最小化"),
        ], str(self.settings.get("launcher_visibility") or "keep"))
        visible.currentIndexChanged.connect(lambda: self._save("launcher_visibility", visible.currentData()))
        preset.add_row(self._setting_row("启动器可见性", "与通用设置中的启动后行为一致。", visible, "monitor"))
        window_combo = self._combo([
            ("default", "默认窗口（854×480）"), ("maximized", "窗口（最大化，有边框）"),
            ("fullscreen", "全屏"), ("custom", "自定义分辨率"),
        ], str(self.settings.get("window_mode") or "default"))
        window_combo.currentIndexChanged.connect(
            lambda: (self._save("window_mode", window_combo.currentData()),
                     self._window_mode_changed(window_combo.currentData())))
        preset.add_row(self._setting_row(
            "游戏窗口", "最大化会保留窗口边框并在游戏窗口出现后最大化；全屏为无边框全屏。",
            window_combo, "monitor"))
        size_row = QWidget()
        size_layout = QHBoxLayout(size_row)
        size_layout.setContentsMargins(0, 8, 0, 8)
        size_layout.setSpacing(7)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(640, 7680)
        self.width_spin.setValue(int(self.settings.get("window_width") or 1280))
        self.width_spin.valueChanged.connect(lambda value: self._save("window_width", value, ""))
        self.height_spin = QSpinBox()
        self.height_spin.setRange(480, 4320)
        self.height_spin.setValue(int(self.settings.get("window_height") or 720))
        self.height_spin.valueChanged.connect(lambda value: self._save("window_height", value, ""))
        size_layout.addWidget(label("宽", "faint"))
        size_layout.addWidget(self.width_spin)
        size_layout.addWidget(label("高", "faint"))
        size_layout.addWidget(self.height_spin)
        size_layout.addStretch()
        preset.add_widget(size_row)
        self._window_mode_changed(window_combo.currentData())
        layout.addWidget(preset)

        args = SectionCard("高级参数", "不确定时请保持留空。", "terminal")
        self.jvm_args_edit = QLineEdit(str(self.settings.get("jvm_args") or ""))
        self.jvm_args_edit.setPlaceholderText("例如 -XX:+UseG1GC -Dfile.encoding=UTF-8")
        self.jvm_args_edit.editingFinished.connect(
            lambda: self._save("jvm_args", self.jvm_args_edit.text().strip(), "JVM 参数已保存"))
        args.add_row(self._setting_row("全局 JVM 参数", "会追加到所有版本的 Java 启动参数。",
                                       self.jvm_args_edit, "cpu"))
        self.game_args_edit = QLineEdit(str(self.settings.get("game_args") or ""))
        self.game_args_edit.setPlaceholderText("例如 --fullscreen")
        self.game_args_edit.editingFinished.connect(
            lambda: self._save("game_args", self.game_args_edit.text().strip(), "游戏参数已保存"))
        args.add_row(self._setting_row("全局游戏参数", "会追加到所有版本的 Minecraft 参数。",
                                       self.game_args_edit, "gamepad"))
        self.server_edit = QLineEdit(str(self.settings.get("server_quick_join") or ""))
        self.server_edit.setPlaceholderText("留空则不自动加入服务器")
        self.server_edit.editingFinished.connect(
            lambda: self._save("server_quick_join", self.server_edit.text().strip(), "服务器地址已保存"))
        args.add_row(self._setting_row("快速加入服务器", "启动后自动连接，例如 play.example.com:25565。",
                                       self.server_edit, "server"))
        layout.addWidget(args)

        isolation = SectionCard("版本隔离策略", "隔离的版本会使用 versions/<版本名> 作为游戏目录。", "layers")
        iso_combo = self._combo([
            ("modded", "仅隔离可安装 Mod 的版本（推荐）"), ("all", "隔离所有版本"),
            ("non_release", "仅隔离非正式版"), ("off", "从不自动隔离"),
        ], str(self.settings.get("isolation_mode") or "modded"))
        iso_combo.currentIndexChanged.connect(
            lambda: self._save("isolation_mode", iso_combo.currentData(), "隔离策略已保存"))
        isolation.add_row(self._setting_row(
            "默认策略", "你随时可以在单个版本设置中覆盖这个选择。", iso_combo, "shield"))
        layout.addWidget(isolation)
        layout.addStretch()
        return scroll

    def _memory_auto_toggled(self, enabled: bool) -> None:
        self.memory_slider.setEnabled(not enabled)
        self.settings.set("memory_mode", "auto" if enabled else "custom")
        self._memory_changed()
        self.shell.refresh_home_stats()

    def _memory_changed(self) -> None:
        value = self.memory_slider.value()
        self.memory_value.setText(f"{value} MB")
        auto = self.memory_auto.isChecked()
        if auto:
            value = self.shell.automatic_memory_mb()
            self.memory_value.setText(f"推荐 {value} MB")
            self.settings.set("memory_mb", value, save=False)
        else:
            self.settings.set("memory_mb", value, save=False)
        self.settings.save()
        total = self.settings.total_ram_mb
        if value > total * 0.8:
            self.memory_hint.setText("提示：分配超过物理内存的 80% 可能导致系统卡顿。")
        else:
            self.memory_hint.setText(f"物理内存 {total / 1024:.1f} GB · 当前分配 {value / 1024:.1f} GB")

    def _window_mode_changed(self, mode: str) -> None:
        custom = mode == "custom"
        self.width_spin.setEnabled(custom)
        self.height_spin.setEnabled(custom)

    # ------------------------------------------------------------------ download
    def _build_download(self) -> QWidget:
        scroll, layout = self._new_scroll_page()
        card = SectionCard("下载行为", "控制版本安装与 Modrinth 下载的默认行为。", "download_cloud")
        source_combo = self._combo([
            ("auto", "自动选择（推荐）"),
            ("official_priority", "优先官方源"),
            ("mirror_priority", "优先 BMCLAPI"),
            ("official", "仅官方源"),
            ("mirror_only", "仅 BMCLAPI 镜像"),
        ], str(self.settings.get("download_source") or "auto"))
        source_combo.currentIndexChanged.connect(
            lambda: (self._save("download_source", source_combo.currentData(), "下载源已更新"),
                     self._update_source_warnings()))
        card.add_row(self._setting_row("默认下载源", "影响 Mojang 版本清单、支持库与资源文件的获取。",
                                       source_combo, "globe"))
        self.source_warnings.append(self._mirror_warning())
        card.add_widget(self.source_warnings[-1])
        parallel_spin = QSpinBox()
        parallel_spin.setRange(2, 16)
        parallel_spin.setValue(int(self.settings.get("parallel_downloads") or 8))
        parallel_spin.valueChanged.connect(
            lambda value: self._save("parallel_downloads", value, "并发数已更新"))
        card.add_row(self._setting_row("并行下载数", "较高的值可以加快资源补全，但会占用更多带宽。",
                                       parallel_spin, "cpu"))
        layout.addWidget(card)
        info = SectionCard("下载源", "所有主题与功能都不需要赞助；这些服务由社区公开提供。", "server")
        sources = [
            ("Mojang Piston Meta", "官方 Minecraft 元数据与资源", "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"),
            ("Modrinth", "开源模组、资源包、光影与整合包索引", "https://modrinth.com"),
            ("BMCLAPI", "国内镜像，用于自动回退下载源", "https://bmclapi2.bangbang93.com"),
            ("Fabric Meta / Quilt Meta", "加载器版本与安装配置", "https://meta.fabricmc.net"),
        ]
        for name, desc, url in sources:
            button = make_button("访问", "ghost", "external", "accent", 13)
            button.clicked.connect(lambda _checked=False, u=url: QDesktopServices.openUrl(QUrl(u)))
            info.add_row(SettingRow(name, desc, button, "globe"))
        layout.addWidget(info)
        layout.addStretch()
        return scroll

    # ------------------------------------------------------------------ java
    def _build_java(self) -> QWidget:
        scroll, layout = self._new_scroll_page()
        self.java_card = SectionCard(
            "Java 运行时", "启动器会自动扫描系统、官方启动器运行时与常见 JDK 目录。", "java")
        self.java_list_box = QVBoxLayout()
        self.java_list_box.setSpacing(5)
        self.java_card.add_widget(self._java_list_widget())
        actions = QHBoxLayout()
        actions.setSpacing(8)
        scan = make_button("重新检测", "secondary", "refresh", "muted", 14)
        scan.clicked.connect(self.shell.rescan_java)
        actions.addWidget(scan)
        add = make_button("手动添加", "secondary", "plus", "muted", 14)
        add.clicked.connect(self._add_java)
        actions.addWidget(add)
        self.java_download_combo = QComboBox()
        self.java_download_combo.addItem("Java 21 LTS", 21)
        self.java_download_combo.addItem("Java 25 LTS", 25)
        self.java_download_combo.setMinimumWidth(120)
        latest = getattr(self.shell, "latest_release", "")
        if latest and latest[0].isdigit() and int(latest.split(".")[0]) >= 26:
            self.java_download_combo.setCurrentIndex(1)
        actions.addWidget(self.java_download_combo)
        download = make_button("下载 Temurin", "primary", "download", "on_accent", 14)
        download.clicked.connect(self._download_java)
        actions.addWidget(download)
        actions.addStretch()
        self.java_card.add_widget(self._actions_widget(actions))
        layout.addWidget(self.java_card)
        note = InfoCard(
            "Java 说明",
            "Minecraft 1.20.5+ 通常需要 Java 21，最新的 26.x 版本需要 Java 25；"
            "1.18+ 建议 Java 17，旧版本通常使用 Java 8。启动器会选择满足版本要求的最低可用版本；"
            "你也可以在单个版本设置中指定 Java。")
        layout.addWidget(note)
        layout.addStretch()
        return scroll

    def _java_list_widget(self) -> QWidget:
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self.java_rows_layout = layout
        self.java_auto_row = self._java_choice_row(
            "自动选择", "根据每个版本的要求挑选合适的 Java", "", True)
        layout.addWidget(self.java_auto_row)
        self.java_rows_host = layout
        return host

    def _java_choice_row(self, title: str, subtitle: str, path: str, active: bool) -> QFrame:
        row = QFrame()
        row.setObjectName("SubCard")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(12, 8, 12, 8)
        row_layout.setSpacing(10)
        icon_label = QLabel()
        icon_label.setPixmap(icons.pixmap(
            "java", self.theme.accent if active else self.theme.text_faint, 18))
        icon_label.setFixedSize(20, 20)
        row_layout.addWidget(icon_label)
        col = QVBoxLayout()
        col.setSpacing(1)
        row_layout.addLayout(col, 1)
        return row

    def refresh_java(self) -> None:
        if not hasattr(self, "java_rows_layout"):
            return
        while self.java_rows_layout.count():
            item = self.java_rows_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        configured = str(self.settings.get("java_path") or "")
        auto_row = self._make_java_row("自动选择", "按版本要求选择合适 Java", "", configured == "")
        self.java_rows_layout.addWidget(auto_row)
        infos = list(getattr(self.shell, "java_list", []))
        for info in infos:
            active = configured == str(info.path)
            row = self._make_java_row(
                f"Java {info.version} · {info.vendor}",
                f"{info.arch} · {info.source} · {info.path}", str(info.path), active)
            self.java_rows_layout.addWidget(row)
        if not infos:
            empty = QLabel("还没有检测到 Java。可在下方选择 Java 21 / 25 并下载 Temurin 便携运行时。")
            empty.setProperty("role", "muted")
            empty.setWordWrap(True)
            empty.setContentsMargins(4, 12, 4, 8)
            self.java_rows_layout.addWidget(empty)

    def _make_java_row(self, title: str, subtitle: str, path: str, active: bool) -> QFrame:
        row = QFrame()
        row.setObjectName("SubCard")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)
        icon_label = QLabel()
        icon_label.setPixmap(icons.pixmap("java", self.theme.accent if active else self.theme.text_faint, 18))
        icon_label.setFixedSize(20, 20)
        layout.addWidget(icon_label)
        col = QVBoxLayout()
        col.setSpacing(1)
        title_label = label(title, "cardTitle")
        col.addWidget(title_label)
        sub = label(subtitle, "faint", wrap=True)
        sub.setToolTip(subtitle)
        sub.setMaximumWidth(680)
        col.addWidget(sub)
        layout.addLayout(col, 1)
        if active:
            pill = Pill("当前使用", "success")
            pill.set_theme(self.theme)
            layout.addWidget(pill)
        else:
            use = make_button("使用", "ghost", "check", "accent", 13)
            use.clicked.connect(lambda _checked=False, p=path: self._choose_java(p))
            layout.addWidget(use)
        return row

    def _choose_java(self, path: str) -> None:
        self.settings.set("java_path", path)
        self.shell.refresh_java(force=False)
        self.refresh_java()
        self.shell.toast("默认 Java 已更新", "success")

    def _add_java(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 java.exe", "", "Java (java.exe);;所有文件 (*)")
        if not path:
            return
        try:
            info = self.shell.java_manager.add_manual(path)
        except LauncherError as exc:
            self.shell.toast(str(exc), "error")
            return
        self.shell.refresh_java(force=False)
        self._choose_java(str(info.path))

    def _download_java(self) -> None:
        major = int(self.java_download_combo.currentData() or 21)
        def job(progress, cancel):
            return self.shell.java_manager.download_temurin(
                major_version=major,
                progress=lambda percent, message: progress((percent, message)), cancel=cancel)
        dialog = ProgressJobDialog(
            self.theme, f"下载 Java {major}", "Eclipse Temurin JRE · 安装到启动器数据目录",
            job, success_text=f"Java {major} 已就绪，可在上方选择它。",
            width=630, height=480, parent=self)
        if dialog.run() == QDialog.DialogCode.Accepted:
            self.shell.refresh_java(force=True)
            self.refresh_java()
            self.shell.toast(f"Java {major} 已安装", "success")

    # ------------------------------------------------------------------ appearance
    def _build_appearance(self) -> QWidget:
        scroll, layout = self._new_scroll_page()
        theme_card = SectionCard(
            "主题", "", "image")
        note = InfoBanner("选择一套配色，立即应用到整个启动器。", "success", "sparkle")
        theme_card.add_widget(note)
        grid = QGridLayout()
        grid.setSpacing(10)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        self.theme_grid = grid
        for index, theme_id in enumerate(THEMES):
            theme = THEMES[theme_id]
            tile = QFrame()
            tile.setObjectName("ThemeTile")
            tile.setCursor(Qt.CursorShape.PointingHandCursor)
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(9, 9, 9, 9)
            tile_layout.setSpacing(5)
            preview = ThemePreview(theme)
            preview.setFixedHeight(90)
            preview.clicked.connect(lambda _checked=False, tid=theme_id: self._choose_theme(tid))
            tile_layout.addWidget(preview)
            name_row = QHBoxLayout()
            name_row.setSpacing(4)
            theme_icon = BrandMark(theme_id, 18, follow_theme=False)
            name_row.addWidget(theme_icon)
            name = label(theme.name, "cardTitle")
            name_row.addWidget(name)
            name_row.addStretch()
            check = QLabel()
            check.setFixedSize(14, 14)
            name_row.addWidget(check)
            tile_layout.addLayout(name_row)
            desc = label(theme.subtitle, "faint")
            tile_layout.addWidget(desc)
            tile.setMinimumHeight(154)
            for child in tile.findChildren(QLabel):
                child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            tile.mouseReleaseEvent = lambda event, tid=theme_id: self._choose_theme(tid)
            grid.addWidget(tile, index // 3, index % 3)
            self.theme_tiles[theme_id] = tile
            tile._preview = preview  # type: ignore[attr-defined]
            tile._check = check  # type: ignore[attr-defined]
        theme_card.add_widget(self._grid_widget(grid))
        layout.addWidget(theme_card)
        layout.addStretch()
        return scroll

    def _grid_widget(self, grid: QGridLayout) -> QWidget:
        host = QWidget()
        host.setLayout(grid)
        return host

    def _actions_widget(self, layout: QHBoxLayout) -> QWidget:
        host = QWidget()
        host.setLayout(layout)
        return host

    def _button_row(self, *buttons: QPushButton) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(8)
        for button in buttons:
            layout.addWidget(button)
        layout.addStretch()
        return layout

    def _choose_theme(self, theme_id: str) -> None:
        self.shell.apply_theme(theme_id)
        self.refresh_theme_gallery()

    def refresh_theme_gallery(self) -> None:
        current = self.shell.theme_id
        for theme_id, tile in self.theme_tiles.items():
            selected = theme_id == current
            tile.setProperty("selected", "true" if selected else "false")
            repolish(tile)
            preview = getattr(tile, "_preview", None)
            if preview:
                preview.set_active(selected)
            check = getattr(tile, "_check", None)
            if check:
                check.setPixmap(icons.pixmap("check_circle", self.theme.success, 13) if selected else QPixmap())

    # ------------------------------------------------------------------ accounts
    def _build_accounts(self) -> QWidget:
        scroll, layout = self._new_scroll_page()
        card = SectionCard("玩家", "启动器仅在本机保存玩家信息。", "users")
        self.accounts_box = QVBoxLayout()
        self.accounts_box.setSpacing(6)
        card.add_widget(self._accounts_widget())
        actions = QHBoxLayout()
        open_accounts = make_button("打开玩家管理", "primary", "users", "on_accent", 14)
        open_accounts.clicked.connect(self.shell.open_accounts)
        actions.addWidget(open_accounts)
        refresh = make_button("刷新微软登录", "secondary", "refresh", "muted", 14)
        refresh.clicked.connect(self.shell.refresh_active_account)
        actions.addWidget(refresh)
        actions.addStretch()
        card.add_widget(self._actions_widget(actions))
        layout.addWidget(card)
        layout.addStretch()
        return scroll

    def _accounts_widget(self) -> QWidget:
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.accounts_box = layout
        return host

    def refresh_accounts(self) -> None:
        if not hasattr(self, "accounts_box"):
            return
        while self.accounts_box.count():
            item = self.accounts_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        active = self.settings.active_account() or {}
        row = QFrame()
        row.setObjectName("SubCard")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(13, 10, 13, 10)
        layout.setSpacing(10)
        avatar = Avatar(str(active.get("name") or "?"), 38)
        avatar.set_account(active)
        layout.addWidget(avatar)
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(label(str(active.get("name") or "未登录"), "cardTitle"))
        kind = "微软正版" if active.get("type") == "microsoft" else "离线玩家"
        uuid_text = str(active.get("uuid") or "")
        col.addWidget(label(f"{kind} · {uuid_text[:8]}…" if uuid_text else kind, "faint"))
        layout.addLayout(col, 1)
        pill = Pill("当前", "success")
        pill.set_theme(self.theme)
        layout.addWidget(pill)
        self.accounts_box.addWidget(row)

    # ------------------------------------------------------------------ about
    def _build_about(self) -> QWidget:
        scroll, layout = self._new_scroll_page()
        materials = SectionCard(
            "素材与第三方库",
            "图标与 Minecraft 素材来源，以及启动器运行时使用的库。",
            "info")
        materials.add_widget(CreditRow(
            "M", "Minecraft 素材 · Mojang Studios / Microsoft",
            "应用图标、主题图标、草方块图标与内置 Steve/Alex 皮肤是项目所有者提供的 Minecraft "
            "素材；版权归 Mojang Studios / Microsoft，不适用 GPL。",
            "", "", "Mojang", image_path=icons_dir() / "grass_block.png"))
        materials.add_widget(CreditRow(
            "Q", "PyQt6 / Qt",
            "界面运行库，GPL-3.0，许可证文本见 GNU 官方页面。",
            "", "", "GPL-3.0"))
        materials.add_widget(CreditRow(
            "L", "minecraft-launcher-lib",
            "仅用于 Forge 官方安装处理器，BSD-2-Clause。",
            "", "", "BSD-2-Clause"))
        layout.addWidget(materials)

        project = SectionCard(
            "项目主页", "源码仓库与发布页。", "link")
        project.add_widget(CreditRow(
            "GH", "GitHub",
            "https://github.com/Heindrich-von-Skalitz/SteampunkHarborLauncher",
            "打开", "https://github.com/Heindrich-von-Skalitz/SteampunkHarborLauncher",
            "GitHub"))
        project.add_widget(CreditRow(
            "GT", "Gitee",
            "https://gitee.com/Matinov/SteampunkHarborLauncher",
            "打开", "https://gitee.com/Matinov/SteampunkHarborLauncher",
            "Gitee"))
        layout.addWidget(project)

        hero = Card(padding=20, spacing=12)
        top = QHBoxLayout()
        top.setSpacing(14)
        logo = QLabel()
        logo.setPixmap(icons.logo(56))
        logo.setFixedSize(56, 56)
        top.addWidget(logo)
        text_col = QVBoxLayout()
        text_col.setSpacing(3)
        title = label(APP_NAME, "section")
        text_col.addWidget(title)
        text_col.addWidget(label(f"版本 {APP_VERSION}", "muted"))
        text_col.addWidget(label(
            "Minecraft 版本、玩家、Java 与内容管理。",
            "muted", wrap=True))
        top.addLayout(text_col, 1)
        hero.body.addLayout(top)
        layout.addWidget(hero)

        legal = SectionCard("许可证与数据", f"{APP_NAME} 的原创代码以 GPL-3.0 公开，第三方组件遵循其各自许可证。", "shield")
        legal.add_widget(CreditRow(
            "©", "启动器原创代码", f"{APP_AUTHOR} · GNU GPL v3。源码随软件一同提供，可查看、修改与再分发。",
            "GNU GPL", "https://www.gnu.org/licenses/gpl-3.0.html", "GPL-3.0"))
        legal.add_widget(CreditRow(
            "SHA", "可执行文件 SHA-256", self._executable_hash(),
            "查询发布页", "https://github.com/Heindrich-von-Skalitz/SteampunkHarborLauncher/releases/",
            "SHA-256"))
        open_data = make_button("打开", "ghost", "folder", "accent", 13)
        open_data.clicked.connect(lambda: self._open(self.shell.data_dir))
        legal.add_row(SettingRow("启动器数据目录", str(self.shell.data_dir), open_data, "save"))
        open_logs = make_button("打开", "ghost", "folder", "accent", 13)
        open_logs.clicked.connect(lambda: self._open(self.shell.logs_dir()))
        legal.add_row(SettingRow("日志目录", str(self.shell.logs_dir()), open_logs, "log"))
        copy_row = QWidget()
        copy_layout = QHBoxLayout(copy_row)
        copy_layout.setContentsMargins(0, 8, 0, 8)
        copy_btn = make_button("复制版本信息", "secondary", "copy", "muted", 13)
        copy_btn.clicked.connect(self._copy_version_info)
        copy_layout.addWidget(copy_btn)
        copy_layout.addStretch()
        legal.add_widget(copy_row)
        layout.addWidget(legal)
        layout.addStretch()
        return scroll

    def _copy_version_info(self) -> None:
        text = f"{APP_NAME} {APP_VERSION}\nPython launcher data: {self.shell.data_dir}\n"
        from PyQt6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(text)
        self.shell.toast("版本信息已复制", "success")

    # ------------------------------------------------------------------ refresh
    def refresh_paths(self) -> None:
        if hasattr(self, "game_dir_edit"):
            self.game_dir_edit.setText(str(self.settings.minecraft_dir))
        if hasattr(self, "game_mode_combo"):
            mode = str(self.settings.get("minecraft_dir_mode") or "official")
            blocker = self.game_mode_combo.blockSignals(True)
            pos = self.game_mode_combo.findData(mode)
            if pos >= 0:
                self.game_mode_combo.setCurrentIndex(pos)
            self.game_mode_combo.blockSignals(blocker)

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        for banner in self.source_warnings:
            banner.set_theme(theme)
        self.refresh_java()
        self.refresh_accounts()
        self.refresh_theme_gallery()


class InfoCard(Card):
    def __init__(self, title: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent, padding=18, spacing=6)
        self.body.addWidget(label(title, "section"))
        self.body.addWidget(label(text, "muted", wrap=True))
