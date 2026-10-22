from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtGui import QColor, QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication

from ..constants import THEME_NAMES
from ..paths import icons_dir


@dataclass(frozen=True)
class Theme:
    id: str
    name: str
    subtitle: str
    cover: str
    dark: bool
    bg: str
    bg_deep: str
    surface: str
    surface_alt: str
    card: str
    card_alt: str
    card_hover: str
    border: str
    border_soft: str
    text: str
    text_muted: str
    text_faint: str
    accent: str
    accent_hi: str
    accent_lo: str
    on_accent: str
    secondary: str
    secondary_hi: str
    danger: str
    success: str
    warning: str
    shadow: str
    input: str
    selection: str
    sidebar: str

    def color(self, role: str) -> str:
        mapping = {
            "text": self.text, "muted": self.text_muted, "faint": self.text_faint,
            "accent": self.accent, "accent_hi": self.accent_hi, "accent_lo": self.accent_lo,
            "on_accent": self.on_accent, "secondary": self.secondary,
            "secondary_hi": self.secondary_hi, "danger": self.danger,
            "success": self.success, "warning": self.warning,
            "border": self.border, "bg": self.bg,
        }
        return mapping.get(role, self.text)


THEMES: dict[str, Theme] = {
    "creeper": Theme(
        id="creeper", name="苦力怕绿", subtitle="清爽的苦力怕绿与白色界面", cover="",
        dark=False,
        bg="#F4F7F4", bg_deep="#EAF0EA", surface="#FFFFFF", surface_alt="#F7FAF7",
        card="#FFFFFF", card_alt="#F7FAF7", card_hover="#EFF7EF",
        border="#D6E2D7", border_soft="#E6EEE7",
        text="#1F2A21", text_muted="#5A675C", text_faint="#8B978D",
        accent="#3FA34D", accent_hi="#4DB85D", accent_lo="#2F7D3A", on_accent="#FFFFFF",
        secondary="#527D5D", secondary_hi="#6B9875",
        danger="#D9534F", success="#2E9E4F", warning="#C38A1B",
        shadow="#00000018", input="#FFFFFF", selection="#DDF1E0", sidebar="#FFFFFF",
    ),
    "ocean": Theme(
        id="ocean", name="海洋蓝", subtitle="干净的蓝色与白色", cover="",
        dark=False,
        bg="#F2F7FB", bg_deep="#E7F0F7", surface="#FFFFFF", surface_alt="#F7FAFC",
        card="#FFFFFF", card_alt="#F6FAFD", card_hover="#EEF6FC",
        border="#D3E2EE", border_soft="#E4EEF6",
        text="#1D2B35", text_muted="#5A6B78", text_faint="#8A9AA6",
        accent="#2478B8", accent_hi="#328BCB", accent_lo="#195F93", on_accent="#FFFFFF",
        secondary="#4F7F9B", secondary_hi="#6A9BB7",
        danger="#D9534F", success="#2E9E6A", warning="#C38A1B",
        shadow="#00000018", input="#FFFFFF", selection="#DCEEF9", sidebar="#FFFFFF",
    ),
    "redstone": Theme(
        id="redstone", name="红石暖", subtitle="温暖的红石色与米白", cover="",
        dark=False,
        bg="#FAF5F2", bg_deep="#F2E9E4", surface="#FFFFFF", surface_alt="#FCF8F5",
        card="#FFFFFF", card_alt="#FCF8F5", card_hover="#FBF0EB",
        border="#E9D7CE", border_soft="#F1E3DC",
        text="#33241F", text_muted="#745B50", text_faint="#A38B7F",
        accent="#C74B3A", accent_hi="#DA5A47", accent_lo="#9C3528", on_accent="#FFFFFF",
        secondary="#8A6A55", secondary_hi="#A88770",
        danger="#C0392B", success="#2E9E4F", warning="#C38A1B",
        shadow="#00000018", input="#FFFFFF", selection="#F6DDD5", sidebar="#FFFFFF",
    ),
    "amethyst": Theme(
        id="amethyst", name="紫水晶", subtitle="柔和的紫色与白色", cover="",
        dark=False,
        bg="#F7F4FB", bg_deep="#EFEAF7", surface="#FFFFFF", surface_alt="#FBF9FD",
        card="#FFFFFF", card_alt="#FBF9FD", card_hover="#F5EFFB",
        border="#DFD4EE", border_soft="#ECE4F5",
        text="#291F37", text_muted="#665777", text_faint="#9386A2",
        accent="#7452B8", accent_hi="#8965CB", accent_lo="#583D91", on_accent="#FFFFFF",
        secondary="#6B5C8A", secondary_hi="#8878A8",
        danger="#D9534F", success="#2E9E4F", warning="#C38A1B",
        shadow="#00000018", input="#FFFFFF", selection="#E9DFF8", sidebar="#FFFFFF",
    ),
    "graphite": Theme(
        id="graphite", name="石墨灰", subtitle="低调的深色主题", cover="",
        dark=True,
        bg="#1B1F22", bg_deep="#14181A", surface="#252B2F", surface_alt="#2A3135",
        card="#262D31", card_alt="#2C3438", card_hover="#333C41",
        border="#3C464C", border_soft="#30383D",
        text="#EEF2F3", text_muted="#AFB9BE", text_faint="#7E8A90",
        accent="#7DC98B", accent_hi="#99DDA6", accent_lo="#4F9B5E", on_accent="#0B1A0F",
        secondary="#7FA6B5", secondary_hi="#9CC3D2",
        danger="#E07070", success="#7DC98B", warning="#D8AE62",
        shadow="#00000055", input="#202629", selection="#39494E", sidebar="#202528",
    ),
    "ivory": Theme(
        id="ivory", name="象牙白", subtitle="温和的米白与深棕", cover="",
        dark=False,
        bg="#F7F4ED", bg_deep="#EFEADF", surface="#FFFFFF", surface_alt="#FCFAF5",
        card="#FFFFFF", card_alt="#FBF8F1", card_hover="#F7F1E5",
        border="#E2D8C6", border_soft="#EDE5D6",
        text="#2C271F", text_muted="#6E6558", text_faint="#9B9183",
        accent="#8A6A34", accent_hi="#A17F43", accent_lo="#684F25", on_accent="#FFFFFF",
        secondary="#3D7268", secondary_hi="#568F82",
        danger="#B55445", success="#4E7F55", warning="#9A6B24",
        shadow="#00000018", input="#FFFFFF", selection="#E8DFC9", sidebar="#FFFFFF",
    ),
    "alloy": Theme(
        id="alloy", name="下界合金", subtitle="下界合金与暗红砖色", cover="",
        dark=True,
        bg="#1C1D1F", bg_deep="#131416", surface="#25272A", surface_alt="#2B2E31",
        card="#242629", card_alt="#2C2F32", card_hover="#35383C",
        border="#41454A", border_soft="#33373B",
        text="#ECE9E4", text_muted="#B8B2A8", text_faint="#7E7A73",
        accent="#B5453F", accent_hi="#D05A52", accent_lo="#7E2A26", on_accent="#FFFFFF",
        secondary="#8A8178", secondary_hi="#A99E92",
        danger="#E06B63", success="#7FBF8A", warning="#D4A34F",
        shadow="#00000066", input="#1D1F21", selection="#4A2B2A", sidebar="#202224",
    ),
    "cherry": Theme(
        id="cherry", name="樱花粉", subtitle="樱花粉与浅木色", cover="",
        dark=False,
        bg="#FBF3F5", bg_deep="#F4E6EA", surface="#FFFFFF", surface_alt="#FDF7F8",
        card="#FFFFFF", card_alt="#FBF1F4", card_hover="#F8E7EC",
        border="#EAD4DB", border_soft="#F2E1E6",
        text="#3A2830", text_muted="#7A5D68", text_faint="#A98C97",
        accent="#D96C8A", accent_hi="#E9849F", accent_lo="#B54C69", on_accent="#FFFFFF",
        secondary="#8A6B78", secondary_hi="#A2808D",
        danger="#C34D62", success="#5E9B6C", warning="#B2813D",
        shadow="#00000018", input="#FFFFFF", selection="#F7DCE3", sidebar="#FFFFFF",
    ),
    "prismarine": Theme(
        id="prismarine", name="海晶蓝", subtitle="深海海晶石与青绿色", cover="",
        dark=True,
        bg="#102426", bg_deep="#0A1A1C", surface="#173235", surface_alt="#1B3A3D",
        card="#173235", card_alt="#1B3A3D", card_hover="#21474A",
        border="#2C5A5D", border_soft="#234649",
        text="#DFF2ED", text_muted="#9FC4BC", text_faint="#6E9992",
        accent="#3FAF9A", accent_hi="#5BCBB5", accent_lo="#2B7F70", on_accent="#071A17",
        secondary="#5C8F86", secondary_hi="#78ADA2",
        danger="#E0796E", success="#6FC49B", warning="#D6A85A",
        shadow="#00000055", input="#12292B", selection="#285A55", sidebar="#0D2022",
    ),
    "lush": Theme(
        id="lush", name="繁茂绿", subtitle="繁茂洞穴的绿意与暖橙", cover="",
        dark=False,
        bg="#F3F7EC", bg_deep="#E8F0DE", surface="#FFFFFF", surface_alt="#F7FAF1",
        card="#FFFFFF", card_alt="#F4F9ED", card_hover="#EDF6E4",
        border="#D5E3C7", border_soft="#E2EDD8",
        text="#24301F", text_muted="#5E7053", text_faint="#8C9C80",
        accent="#6E9E3F", accent_hi="#84B852", accent_lo="#4E7628", on_accent="#FFFFFF",
        secondary="#7A6A45", secondary_hi="#96825A",
        danger="#B55445", success="#4E9B5A", warning="#C77A2E",
        shadow="#00000018", input="#FFFFFF", selection="#E2F0D4", sidebar="#FFFFFF",
    ),
    "end": Theme(
        id="end", name="末地紫", subtitle="末地石米黄与紫颂紫", cover="",
        dark=True,
        bg="#17121F", bg_deep="#0F0B16", surface="#241C30", surface_alt="#2B2238",
        card="#241C30", card_alt="#2B2238", card_hover="#332743",
        border="#45345A", border_soft="#382B49",
        text="#F0E9D6", text_muted="#B9AAC9", text_faint="#85769A",
        accent="#9A6AD6", accent_hi="#B58BE8", accent_lo="#6E479F", on_accent="#14101C",
        secondary="#C9B36A", secondary_hi="#DDC97E",
        danger="#D47A8B", success="#79BE91", warning="#D8B85A",
        shadow="#00000066", input="#1B1524", selection="#3D2E52", sidebar="#120D19",
    ),
}

def choose_fonts() -> tuple[str, str, str, str]:
    families = set(QFontDatabase.families())
    ui = next((name for name in ("Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans SC",
                                 "Source Han Sans SC", "Segoe UI") if name in families), "Segoe UI")
    display = next((name for name in ("Bahnschrift SemiBold", "Bahnschrift", "Microsoft YaHei UI",
                                      "Segoe UI Variable Display") if name in families), ui)
    mono = next((name for name in ("Cascadia Mono", "JetBrains Mono", "Consolas",
                                   "Courier New") if name in families), "Consolas")
    serif = next((name for name in ("Georgia", "Cambria", "Noto Serif SC") if name in families), ui)
    return ui, display, mono, serif


class ThemeManager:
    def __init__(self, app: QApplication, theme_id: str = "creeper") -> None:
        self.app = app
        self.font_ui, self.font_display, self.font_mono, self.font_serif = choose_fonts()
        self.current = THEMES.get(theme_id) or THEMES["creeper"]
        self._apply_fonts()

    def _apply_fonts(self) -> None:
        font = QFont(self.font_ui, 10)
        font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
        self.app.setFont(font)

    def set_theme(self, theme_id: str) -> Theme:
        self.current = THEMES.get(theme_id) or THEMES["creeper"]
        self.app.setStyleSheet(self.stylesheet(self.current))
        return self.current

    @property
    def qss_asset_dir(self) -> str:
        return str(icons_dir()).replace("\\", "/")

    def stylesheet(self, t: Theme) -> str:
        arrow = f"{self.qss_asset_dir}/chevron_down.png"
        ui = self.font_ui
        display = self.font_display
        mono = self.font_mono
        return f"""
* {{
    font-family: "{ui}";
    outline: none;
    color: {t.text};
}}
QMainWindow, QWidget#WindowRoot {{
    background: transparent;
}}
QWidget {{
    selection-background-color: {t.selection};
    selection-color: {t.text};
}}
QToolTip {{
    background: {t.card_alt};
    color: {t.text};
    border: 1px solid {t.border};
    border-radius: 7px;
    padding: 6px 9px;
    font-size: 12px;
}}
QFrame#Card, QFrame[card="true"] {{
    background: {t.card};
    border: 1px solid {t.border_soft};
    border-radius: 16px;
}}
QFrame#CardAlt {{
    background: {t.card_alt};
    border: 1px solid {t.border_soft};
    border-radius: 14px;
}}
QFrame#SubCard {{
    background: {t.surface_alt};
    border: 1px solid {t.border_soft};
    border-radius: 12px;
}}
QFrame#Divider {{
    background: {t.border_soft};
    max-height: 1px;
    min-height: 1px;
    border: none;
}}
QFrame#VersionCard {{
    background: {t.card};
    border: 1px solid {t.border_soft};
    border-radius: 14px;
}}
QFrame#VersionCard:hover {{
    border-color: {t.border};
    background: {t.card_alt};
}}
QFrame#VersionCard[selected="true"] {{
    border: 1px solid {t.accent_lo};
    background: {t.card_alt};
}}
QFrame#ProjectCard {{
    background: {t.card};
    border: 1px solid {t.border_soft};
    border-radius: 13px;
}}
QFrame#ProjectCard:hover {{
    border-color: {t.border};
    background: {t.card_alt};
}}
QFrame#ThemeTile {{
    background: {t.card};
    border: 1px solid {t.border_soft};
    border-radius: 13px;
}}
QFrame#ThemeTile[selected="true"] {{
    border-color: {t.accent};
}}
QFrame#RowHover:hover {{
    background: {t.card_alt};
}}
QFrame#SectionBox {{
    background: {t.card};
    border: 1px solid {t.border_soft};
    border-radius: 15px;
}}
QFrame#VerticalDivider {{
    background: {t.border_soft};
    max-width: 1px;
    min-width: 1px;
    border: none;
}}
QLabel[role="eyebrow"] {{
    color: {t.accent};
    font-family: "{display}";
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 2px;
}}
QLabel[role="title"] {{
    color: {t.text};
    font-family: "{display}";
    font-size: 27px;
    font-weight: 600;
}}
QLabel[role="subtitle"] {{
    color: {t.text_muted};
    font-size: 12px;
}}
QLabel[role="section"] {{
    color: {t.text};
    font-size: 15px;
    font-weight: 600;
}}
QLabel[role="cardTitle"] {{
    color: {t.text};
    font-size: 14px;
    font-weight: 600;
}}
QLabel[role="muted"] {{
    color: {t.text_muted};
    font-size: 12px;
}}
QLabel[role="faint"] {{
    color: {t.text_faint};
    font-size: 11px;
}}
QLabel[role="mono"] {{
    font-family: "{mono}";
    color: {t.text_muted};
    font-size: 11px;
}}
QLabel[role="accent"] {{
    color: {t.accent};
    font-size: 12px;
}}
QLabel[role="danger"] {{
    color: {t.danger};
}}
QLabel[role="success"] {{
    color: {t.success};
}}
QLabel[role="bigNumber"] {{
    color: {t.text};
    font-family: "{display}";
    font-size: 24px;
    font-weight: 600;
}}
QLabel#HeroTitle {{
    color: #F6EBD8;
    font-family: "{display}";
    font-size: 43px;
    font-weight: 700;
}}
QLabel#HeroSubtitle {{
    color: rgba(236, 226, 210, 185);
    font-size: 13px;
}}
QLabel#HeroEyebrow {{
    color: #D8AF71;
    font-family: "{display}";
    font-size: 10px;
    letter-spacing: 3px;
    font-weight: 600;
}}
QLabel#HeroMeta {{
    color: rgba(240, 230, 213, 170);
    font-size: 11px;
}}
QLabel#HeroTitleSmall {{
    color: #F4E9D5;
    font-size: 22px;
    font-weight: 600;
}}
QPushButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 10px;
    color: {t.text_muted};
    padding: 8px 13px;
    font-size: 12px;
}}
QPushButton:hover {{
    color: {t.text};
    background: {t.card_alt};
    border-color: {t.border_soft};
}}
QPushButton:pressed {{
    background: {t.surface_alt};
}}
QPushButton:disabled {{
    color: {t.text_faint};
    background: transparent;
    border-color: transparent;
}}
QPushButton[role="primary"] {{
    background: {t.accent};
    color: {t.on_accent};
    border: 1px solid {t.accent_hi};
    font-weight: 700;
    padding: 10px 18px;
}}
QPushButton[role="primary"]:hover {{
    background: {t.accent_hi};
    border-color: {t.accent_hi};
}}
QPushButton[role="primary"]:pressed {{
    background: {t.accent_lo};
}}
QPushButton[role="secondary"] {{
    background: transparent;
    color: {t.text};
    border: 1px solid {t.border};
    padding: 9px 15px;
}}
QPushButton[role="secondary"]:hover {{
    background: {t.card_alt};
    border-color: {t.accent_lo};
}}
QPushButton[role="subtle"] {{
    background: {t.surface_alt};
    color: {t.text_muted};
    border: 1px solid {t.border_soft};
}}
QPushButton[role="subtle"]:hover {{
    color: {t.text};
    background: {t.card_alt};
    border-color: {t.border};
}}
QPushButton[role="danger"] {{
    color: {t.danger};
    border: 1px solid {t.danger}55;
    background: transparent;
}}
QPushButton[role="danger"]:hover {{
    background: {t.danger}18;
    border-color: {t.danger};
}}
QPushButton[role="ghost"] {{
    background: transparent;
    border: 1px solid transparent;
    color: {t.text_muted};
    padding: 6px 9px;
}}
QPushButton[role="ghost"]:hover {{
    color: {t.accent};
    background: {t.card_alt};
}}
QPushButton[role="nav"] {{
    background: transparent;
    border: none;
    border-radius: 10px;
    color: {t.text_muted};
    padding: 8px 14px;
    font-size: 12px;
}}
QPushButton[role="nav"]:hover {{
    color: {t.text};
    background: {t.card};
}}
QPushButton[role="nav"][active="true"] {{
    color: {t.accent_hi};
    background: {t.card_alt};
    border: 1px solid {t.border_soft};
}}
QPushButton[role="segment"] {{
    background: transparent;
    color: {t.text_muted};
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 7px 16px;
    font-size: 12px;
}}
QPushButton[role="segment"]:hover {{
    color: {t.text};
    background: {t.card_alt};
}}
QPushButton[role="segment"][active="true"] {{
    background: {t.accent};
    color: {t.on_accent};
    font-weight: 600;
}}
QPushButton[role="launch"] {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {t.accent_hi}, stop:1 {t.accent_lo});
    color: {t.on_accent};
    border: 1px solid {t.accent_hi};
    border-radius: 14px;
    font-size: 16px;
    font-weight: 800;
    padding: 12px 26px;
    letter-spacing: 2px;
}}
QPushButton[role="launch"]:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #F3D49C, stop:1 {t.accent});
}}
QPushButton[role="launch"]:pressed {{
    background: {t.accent_lo};
}}
QPushButton[role="launch"]:disabled {{
    background: {t.border_soft};
    border-color: {t.border_soft};
    color: {t.text_faint};
}}
QPushButton[role="window"] {{
    background: transparent;
    border: none;
    border-radius: 7px;
    padding: 0px;
}}
QPushButton[role="window"]:hover {{
    background: {t.card_alt};
}}
QPushButton[role="windowClose"] {{
    background: transparent;
    border: none;
    border-radius: 7px;
    padding: 0px;
}}
QPushButton[role="windowClose"]:hover {{
    background: #C75545;
}}
QPushButton[role="icon"] {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 6px;
}}
QPushButton[role="icon"]:hover {{
    background: {t.card_alt};
    border-color: {t.border_soft};
}}
QPushButton[role="chip"] {{
    background: {t.surface_alt};
    border: 1px solid {t.border_soft};
    border-radius: 9px;
    padding: 5px 9px;
    color: {t.text_muted};
    font-size: 11px;
}}
QPushButton[role="chip"]:hover {{
    color: {t.accent};
    border-color: {t.border};
}}
QPushButton[role="install"] {{
    background: transparent;
    color: {t.accent};
    border: 1px solid {t.border};
    border-radius: 9px;
    padding: 6px 13px;
    font-size: 11px;
}}
QPushButton[role="install"]:hover {{
    background: {t.accent}22;
    border-color: {t.accent};
}}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {t.input};
    color: {t.text};
    border: 1px solid {t.border_soft};
    border-radius: 9px;
    padding: 8px 11px;
    font-size: 12px;
    selection-background-color: {t.selection};
}}
QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover, QSpinBox:hover, QComboBox:hover {{
    border-color: {t.border};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border-color: {t.accent_lo};
}}
QLineEdit[role="search"] {{
    padding-left: 34px;
    background: {t.surface_alt};
}}
QComboBox {{
    padding-right: 27px;
    min-height: 18px;
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
    subcontrol-origin: padding;
    subcontrol-position: center right;
}}
QComboBox::down-arrow {{
    image: url("{arrow}");
    width: 10px;
    height: 7px;
}}
QComboBox QAbstractItemView {{
    background: {t.card_alt};
    color: {t.text};
    border: 1px solid {t.border};
    border-radius: 8px;
    padding: 5px;
    selection-background-color: {t.selection};
    selection-color: {t.text};
    outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button {{
    width: 18px;
    border: none;
    background: transparent;
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 9px;
    margin: 2px 1px;
}}
QScrollBar::handle:vertical {{
    background: {t.border};
    min-height: 34px;
    border-radius: 4px;
    margin: 2px;
}}
QScrollBar::handle:vertical:hover {{
    background: {t.accent_lo};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
    height: 0px;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 9px;
}}
QScrollBar::handle:horizontal {{
    background: {t.border};
    min-width: 34px;
    border-radius: 4px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: transparent;
    width: 0px;
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: {t.border_soft};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {t.accent};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {t.accent_hi};
    border: 2px solid {t.card};
    width: 15px;
    height: 15px;
    margin: -7px 0;
    border-radius: 8px;
}}
QSlider::handle:horizontal:hover {{
    background: {t.text};
}}
QProgressBar {{
    background: {t.surface_alt};
    border: 1px solid {t.border_soft};
    border-radius: 5px;
    height: 9px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t.accent_lo}, stop:1 {t.accent_hi});
    border-radius: 4px;
}}
QCheckBox {{
    color: {t.text_muted};
    spacing: 8px;
    font-size: 12px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 5px;
    border: 1px solid {t.border};
    background: {t.input};
}}
QCheckBox::indicator:checked {{
    background: {t.accent};
    border-color: {t.accent_hi};
}}
QRadioButton {{
    color: {t.text_muted};
    spacing: 7px;
    font-size: 12px;
}}
QDialog {{
    background: {t.bg};
}}
QMenu {{
    background: {t.card_alt};
    color: {t.text};
    border: 1px solid {t.border};
    border-radius: 9px;
    padding: 5px;
}}
QMenu::item {{
    padding: 7px 26px 7px 12px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background: {t.selection};
}}
QToolButton {{
    background: transparent;
    border: none;
    color: {t.text_muted};
    padding: 6px;
    border-radius: 8px;
}}
QToolButton:hover {{
    background: {t.card_alt};
    color: {t.text};
}}
QListWidget, QTableWidget, QTreeWidget {{
    background: transparent;
    border: none;
    color: {t.text};
}}
QListWidget::item, QTreeWidget::item {{
    padding: 7px;
    border-radius: 7px;
}}
QListWidget::item:selected, QTreeWidget::item:selected {{
    background: {t.selection};
}}
QHeaderView::section {{
    background: transparent;
    border: none;
    color: {t.text_faint};
    padding: 6px;
}}
QTextBrowser {{
    background: transparent;
    border: none;
}}
QSizeGrip {{
    background: transparent;
}}

/* ---- clean launcher overrides ------------------------------------------ */
QMainWindow, QWidget#WindowRoot, QStackedWidget#PageStack {{
    background: {t.bg};
}}
QFrame#TitleBar {{
    background: {t.accent};
    border: none;
}}
QLabel#BrandName {{
    color: {t.on_accent};
    font-size: 15px;
    font-weight: 700;
    letter-spacing: 0.5px;
}}
QFrame#AccountChip {{
    background: rgba(0, 0, 0, 0.12);
    border: none;
    border-radius: 7px;
}}
QFrame#AccountChip:hover {{
    background: rgba(0, 0, 0, 0.17);
}}
QLabel#TopAccountName {{
    color: {t.on_accent};
    font-size: 11px;
    font-weight: 600;
}}
QPushButton[role="topnav"] {{
    color: {t.on_accent};
    background: transparent;
    border: none;
    border-radius: 7px;
    padding: 7px 13px;
    font-size: 13px;
    font-weight: 500;
}}
QPushButton[role="topnav"]:hover {{
    color: {t.on_accent};
    background: rgba(0, 0, 0, 0.09);
}}
QPushButton[role="topnav"][active="true"] {{
    color: {t.accent_lo};
    background: {t.card};
    font-weight: 600;
}}
QPushButton[role="topnav"]:disabled {{
    color: {t.on_accent};
    background: transparent;
}}
QPushButton[role="nav"] {{
    color: {t.text_muted};
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 8px 12px;
    text-align: left;
}}
QPushButton[role="nav"]:hover {{
    color: {t.text};
    background: {t.surface_alt};
}}
QPushButton[role="nav"][active="true"] {{
    color: {t.accent_lo};
    background: {t.selection};
    font-weight: 600;
}}
QPushButton[role="topwindow"], QPushButton[role="topclose"] {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 0px;
}}
QPushButton[role="topwindow"]:hover {{
    background: rgba(0, 0, 0, 0.12);
}}
QPushButton[role="topclose"]:hover {{
    background: #D9534F;
}}
QFrame#Card, QFrame#SectionBox, QFrame#VersionCard, QFrame#ProjectCard, QFrame#ThemeTile {{
    background: {t.card};
    border: 1px solid {t.border_soft};
    border-radius: 8px;
}}
QFrame#VersionCard:hover, QFrame#ProjectCard:hover, QFrame#CardAlt:hover {{
    border-color: {t.border};
    background: {t.card_alt};
}}
QLabel[role="pageTitle"], QLabel[role="title"] {{
    color: {t.text};
    font-family: "{ui}";
    font-size: 24px;
    font-weight: 600;
    letter-spacing: 0px;
}}
QLabel[role="eyebrow"] {{
    color: {t.text_faint};
    font-family: "{ui}";
    font-size: 9px;
    font-weight: 500;
    letter-spacing: 1px;
}}
QLabel[role="subtitle"] {{
    color: {t.text_muted};
    font-size: 12px;
}}
QPushButton {{
    border-radius: 6px;
}}
QPushButton[role="primary"] {{
    background: {t.accent};
    color: {t.on_accent};
    border: 1px solid {t.accent};
    font-weight: 600;
    padding: 8px 14px;
}}
QPushButton[role="primary"]:hover {{
    background: {t.accent_hi};
    border-color: {t.accent_hi};
}}
QPushButton[role="secondary"] {{
    background: {t.card};
    color: {t.text};
    border: 1px solid {t.border};
    padding: 7px 12px;
}}
QPushButton[role="secondary"]:hover {{
    background: {t.card_alt};
    border-color: {t.accent_lo};
}}
QPushButton[role="subtle"] {{
    background: {t.surface_alt};
    color: {t.text_muted};
    border: 1px solid {t.border_soft};
}}
QPushButton[role="launch"] {{
    background: {t.accent};
    color: {t.on_accent};
    border: 1px solid {t.accent};
    border-radius: 8px;
    font-size: 14px;
    font-weight: 600;
    padding: 10px 22px;
    letter-spacing: 0px;
}}
QPushButton[role="launch"]:hover {{
    background: {t.accent_hi};
    border-color: {t.accent_hi};
}}
QPushButton[role="launch"]:pressed {{
    background: {t.accent_lo};
}}
QPushButton[role="launch"]:disabled {{
    background: {t.border_soft};
    border-color: {t.border_soft};
    color: {t.text_faint};
}}
QPushButton[role="segment"][active="true"] {{
    background: {t.accent};
    color: {t.on_accent};
    border-radius: 6px;
}}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {t.input};
    border: 1px solid {t.border};
    border-radius: 6px;
    padding: 7px 10px;
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border-color: {t.accent};
}}
QProgressBar {{
    border-radius: 4px;
}}
QProgressBar::chunk {{
    background: {t.accent};
    border-radius: 3px;
}}
QDialog {{
    background: {t.bg};
}}
QToolTip {{
    background: {t.card};
    color: {t.text};
    border: 1px solid {t.border};
    border-radius: 5px;
    padding: 5px 8px;
}}
"""

