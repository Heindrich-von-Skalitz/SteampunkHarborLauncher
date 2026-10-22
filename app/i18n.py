from __future__ import annotations

import re
from typing import Any

from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtWidgets import QComboBox, QLabel, QLineEdit, QPushButton, QWidget

from .i18n_strings import EN as _EN_FULL, FT as _FT_FULL

LANGUAGES = {"zh_JT": "简体中文", "zh_FT": "繁體中文", "en": "English"}

_EN = {
    "机械冒险港启动器": "Steampunk Harbor Launcher",
    "机械冒险港工坊": "Steampunk Harbor Works",
    "购买 Minecraft 正版": "Buy Minecraft",
    "打开微软商店购买页面": "Open the Microsoft Store purchase page",
    "启动": "Play", "下载": "Download", "版本": "Versions", "内容": "Content",
    "设置": "Settings", "任务": "Tasks", "游戏版本": "Game versions", "模组": "Mods",
    "资源包": "Resource packs", "光影": "Shaders", "整合包": "Modpacks",
    "安装": "Install", "选择版本": "Choose version", "搜索": "Search",
    "取消": "Cancel", "关闭": "Close", "保存": "Save", "确定": "OK",
    "通用": "General", "外观": "Appearance", "玩家": "Players", "关于": "About",
    "主题": "Themes", "下载源": "Download source", "下载与网络": "Downloads & network",
    "下载行为": "Download behavior", "同时下载数": "Concurrent downloads",
    "默认下载源": "Default source", "仅官方源": "Official only",
    "仅 BMCLAPI 镜像": "BMCLAPI only", "优先官方源": "Prefer official",
    "优先 BMCLAPI": "Prefer BMCLAPI", "自动选择（推荐）": "Automatic (recommended)",
    "材质包可勾选": "Resource packs can be toggled",
    "苦力怕绿": "Creeper green", "海洋蓝": "Ocean blue", "红石暖": "Redstone warm",
    "紫水晶": "Amethyst", "石墨灰": "Graphite", "象牙白": "Ivory",
    "下界合金": "Netherite", "樱花粉": "Cherry blossom", "海晶蓝": "Prismarine",
    "繁茂绿": "Lush green", "末地紫": "End purple",
    "当前版本": "Current version", "加载器": "Loader", "排序": "Sort",
    "相关度": "Relevance", "下载量": "Downloads", "最近更新": "Recently updated",
    "全部版本": "All versions", "目标版本": "Target version",
    "自动": "Auto", "不限": "Any", "原版": "Vanilla",
    "官方": "Official", "镜像": "Mirror",
    "搜索模组": "Search mods", "搜索资源包": "Search resource packs",
    "搜索光影": "Search shaders", "搜索整合包": "Search modpacks",
    "语言": "Language", "重启生效": "Restart required",
}

_TW_CHARS = str.maketrans(
    "设置下载版本内容玩家通用启动外观关于保存取消安装选择搜索主题材质包"
    "资源包光影整合包官方镜像加载器排序相关度最近更新全部目标自动原版"
    "苦力怕绿海洋蓝红石暖紫水晶石墨灰象牙白下界合金樱花粉海晶蓝繁茂绿末地紫",
    "設定下載版本內容玩家通用啟動外觀關於儲存取消安裝選擇搜尋主題材質包"
    "資源包光影整合包官方鏡像加載器排序相關度最近更新全部目標自動原版"
    "苦力怕綠海洋藍紅石暖紫水晶石墨灰象牙白下界合金櫻花粉海晶藍繁茂綠末地紫",
)
_TW_PHRASES = {
    "机械冒险港启动器": "Steampunk Harbor Launcher",
    "机械冒险港工坊": "Steampunk Harbor Works",
    "购买 Minecraft 正版": "購買 Minecraft",
    "打开微软商店购买页面": "開啟 Microsoft Store 購買頁面",
    "设置": "設定", "下载": "下載", "版本": "版本", "內容": "內容",
    "任务": "任務",
    "外观": "外觀", "关于": "關於", "启动": "啟動", "保存": "儲存",
    "取消": "取消", "安装": "安裝", "选择版本": "選擇版本", "搜索": "搜尋",
    "主题": "主題", "材质包": "材質包", "资源包": "資源包", "光影": "光影",
    "整合包": "整合包", "官方": "官方", "镜像": "鏡像", "加载器": "載入器",
    "苦力怕绿": "苦力怕綠", "海洋蓝": "海洋藍", "红石暖": "紅石暖",
    "紫水晶": "紫水晶", "石墨灰": "石墨灰", "象牙白": "象牙白",
    "下界合金": "下界合金", "樱花粉": "櫻花粉", "海晶蓝": "海晶藍",
    "繁茂绿": "繁茂綠", "末地紫": "末地紫",
}


_EN_WORDS = (
    ("机械冒险港启动器", "Steampunk Harbor Launcher"),
    ("机械冒险港工坊", "Steampunk Harbor Works"),
    ("的原创代码以 GPL-3.0 公开，第三方组件遵循其各自许可证。",
     " original code is released under GPL-3.0; third-party components follow their own licenses."),
    ("GNU GPL v3。源码随软件一同提供，可查看、修改与再分发。",
     "GNU GPL v3. Source is provided with the software and may be viewed, modified, and redistributed."),
    ("点击「安装」可同时安装加载器", "Click Install to also add a loader"),
    ("物理内存", "Physical memory"),
    ("当前分配", "Allocated"),
    ("推荐", "Recommended"),
    ("已就绪", "Ready"),
    ("原版", "Vanilla"),
    ("正式版", "Release"),
    ("主题免费", "Themes free"),
    ("内存", "Memory"),
    ("个版本", "versions"),
    ("版本", "Version"),
    ("需要 Java", "Requires Java"),
    ("个 Java", "Java runtimes"),
    ("已安装", "Installed"),
    ("加载器", "Loader"),
    ("安装", "Install"),
    ("下载", "Download"),
)

_DYNAMIC_EN = (
    (re.compile(r"^共 ([\d,]+) 个版本 · 来源 (.+)$"), r"\1 versions · Source \2"),
    (re.compile(r"^共 ([\d,]+) 个版本 · 选择后安装$"), r"\1 versions · Choose to install"),
    (re.compile(r"^共 ([\d,]+) 个版本 · 选择后下载$"), r"\1 versions · Choose to download"),
    (re.compile(r"^共 ([\d,]+) 个版本 · 选择后(安装|下载)$"), r"\1 versions · Choose to \2"),
    (re.compile(r"^共 ([\d,]+) 个结果 · 已加载 ([\d,]+) 个$"), r"\1 results · \2 loaded"),
    (re.compile(r"^([\d,]+) 个版本 · 点击卡片查看详情$"), r"\1 versions · Click a card for details"),
    (re.compile(r"^([\d,]+) / ([\d,]+) 个版本$"), r"\1 / \2 versions"),
    (re.compile(r"^([\d,]+) 个版本$"), r"\1 versions"),
    (re.compile(r"^([\d,]+) 个项目$"), r"\1 projects"),
    (re.compile(r"^模组 ([\d,]+)$"), r"Mods \1"),
    (re.compile(r"^资源包 ([\d,]+)$"), r"Resource packs \1"),
    (re.compile(r"^光影 ([\d,]+)$"), r"Shaders \1"),
    (re.compile(r"^存档 ([\d,]+)$"), r"Saves \1"),
)


def _dynamic_english(text: str) -> str | None:
    for pattern, replacement in _DYNAMIC_EN:
        if pattern.match(text):
            return pattern.sub(replacement, text)
    return None


def translate(text: str, language: str) -> str:
    if not text or language == "zh_JT":
        return text
    if language == "en":
        exact = _EN.get(text) or _EN_FULL.get(text) or _dynamic_english(text)
        if exact:
            return exact
        for chinese, english in _EN_WORDS:
            text = text.replace(chinese, english)
        return text
    if language == "zh_FT":
        return _TW_PHRASES.get(text) or _FT_FULL.get(text) or text.translate(_TW_CHARS)
    return text


class _LanguageFilter(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.language = "zh_JT"

    def eventFilter(self, obj: Any, event: Any) -> bool:
        if event.type() in {QEvent.Type.Polish, QEvent.Type.Show}:
            translate_widget(obj, self.language)
        return False


_FILTER: _LanguageFilter | None = None


def _translate_combo(combo: QComboBox, language: str) -> None:
    original = getattr(combo, "_shl_i18n_items", None)
    if original is None or len(original) != combo.count():
        original = [combo.itemText(i) for i in range(combo.count())]
        combo._shl_i18n_items = original
    for index, text in enumerate(original):
        if index < combo.count():
            combo.setItemText(index, translate(text, language))


def translate_widget(widget: Any, language: str) -> None:
    if isinstance(widget, QComboBox):
        _translate_combo(widget, language)
        return
    if isinstance(widget, (QLabel, QPushButton)):
        current = widget.text()
        original = getattr(widget, "_shl_i18n_text", None)
        if original is None:
            original = current
        elif current != original and current != translate(original, language):
            original = current
        widget._shl_i18n_text = original
        if current == original or current == translate(original, language):
            widget.setText(translate(original, language))
    elif isinstance(widget, QLineEdit):
        current = widget.placeholderText()
        original = getattr(widget, "_shl_i18n_placeholder", None)
        if original is None:
            original = current
        elif current != original and current != translate(original, language):
            original = current
        widget._shl_i18n_placeholder = original
        if current == original or current == translate(original, language):
            widget.setPlaceholderText(translate(original, language))


def apply_language(root: QWidget, language: str) -> None:
    global _FILTER
    language = language if language in LANGUAGES else "zh_JT"
    if _FILTER is None:
        _FILTER = _LanguageFilter()
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(_FILTER)
    _FILTER.language = language
    translate_widget(root, language)
    for widget in root.findChildren(QWidget):
        translate_widget(widget, language)
