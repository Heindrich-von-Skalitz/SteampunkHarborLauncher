from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Callable, Iterable

from PyQt6.QtCore import (
    QByteArray, QEasingCurve, QEvent, QObject, QPoint, QPointF, QPropertyAnimation,
    QRect, QRectF, QSize, Qt, QTimer, QUrl, pyqtProperty, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QGuiApplication, QIcon, QImage, QLinearGradient,
    QPainter, QPainterPath, QPen, QPixmap, QRegion, QTextCursor,
)
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import (
    QAbstractButton, QButtonGroup, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QScrollArea, QSizePolicy, QSpacerItem, QVBoxLayout, QWidget,
)

from ..core import skins as skin_model
from ..paths import cache_dir, resource_dir
from . import icons
from .skin_render import head_pixmap, load_skin_pixmap
from .theme import THEMES, Theme, ThemeManager


# --------------------------------------------------------------------------- icon helpers
def set_icon(widget: QWidget, name: str, role: str = "muted", size: int = 16,
             width: float = 1.7) -> None:
    widget.setProperty("_shl_icon_name", name)
    widget.setProperty("_shl_icon_role", role)
    widget.setProperty("_shl_icon_size", size)
    widget.setProperty("_shl_icon_width", width)
    if hasattr(widget, "setIconSize"):
        widget.setIconSize(QSize(size, size))
    if hasattr(widget, "setIcon"):
        widget.setIcon(icons.icon(name, "#5A675C", size, width))


def themed_color(theme: Theme, role: str) -> str:
    if role == "hero":
        return "#F4E7D3"
    if role == "hero_strong":
        return "#1B1308"
    return theme.color(role)


def refresh_widget_icons(root: QWidget, theme: Theme) -> None:
    for widget in root.findChildren(QWidget):
        name = widget.property("_shl_icon_name")
        if not name:
            continue
        role = widget.property("_shl_icon_role") or "muted"
        size = int(widget.property("_shl_icon_size") or 16)
        width = float(widget.property("_shl_icon_width") or 1.7)
        if hasattr(widget, "setIcon"):
            widget.setIcon(icons.icon(str(name), themed_color(theme, str(role)), size, width))


def refresh_theme_widgets(root: QWidget, theme: Theme) -> None:
    """Let custom-painted children pick up a new palette."""
    for widget in root.findChildren(QWidget):
        setter = getattr(widget, "set_theme", None)
        if callable(setter):
            try:
                setter(theme)
            except (TypeError, RuntimeError):
                pass
    root.update()


def make_button(text: str, role: str = "ghost", icon_name: str | None = None,
                icon_role: str = "muted", icon_size: int = 16, parent: QWidget | None = None,
                tooltip: str = "") -> QPushButton:
    button = QPushButton(text, parent)
    button.setProperty("role", role)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if icon_name:
        set_icon(button, icon_name, icon_role, icon_size)
    if tooltip:
        button.setToolTip(tooltip)
    return button


def icon_button(name: str, tooltip: str = "", role: str = "icon",
                icon_role: str = "muted", size: int = 17) -> QPushButton:
    button = QPushButton()
    button.setProperty("role", role)
    button.setFixedSize(32, 32)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setToolTip(tooltip)
    set_icon(button, name, icon_role, size)
    return button


def clear_layout(layout) -> None:
    if layout is None:
        return
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        child = item.layout()
        if widget:
            widget.setParent(None)
            widget.deleteLater()
        elif child:
            clear_layout(child)


def hline() -> QFrame:
    frame = QFrame()
    frame.setObjectName("Divider")
    frame.setFixedHeight(1)
    return frame


def vline() -> QFrame:
    frame = QFrame()
    frame.setObjectName("VerticalDivider")
    frame.setFixedWidth(1)
    return frame


def label(text: str = "", role: str = "", wrap: bool = False) -> QLabel:
    item = QLabel(text)
    if role:
        item.setProperty("role", role)
    item.setWordWrap(wrap)
    return item


def badge(text: str, tone: str = "accent") -> "Pill":
    return Pill(text, tone)


def repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


# --------------------------------------------------------------------------- surface
class Card(QFrame):
    def __init__(self, parent: QWidget | None = None, *, alt: bool = False,
                 padding: int = 18, spacing: int = 12) -> None:
        super().__init__(parent)
        self.setObjectName("CardAlt" if alt else "Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(padding, padding, padding, padding)
        self.body.setSpacing(spacing)


class ClickableFrame(QFrame):
    clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("clickable", True)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class Avatar(QWidget):
    """Player head rendered from the active Java skin.

    Microsoft players use the skin URL returned by the profile API; offline
    players use their chosen skin or the original Steve/Alex-style default
    assigned deterministically from their player UUID.
    """

    _default_heads: dict[tuple[str, int], QPixmap] = {}

    def __init__(self, name: str = "Player", size: int = 42, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name = name
        self._size = size
        self._model = skin_model.model_for_seed(name)
        self._pixmap: QPixmap | None = None
        self._skin_url = ""
        self.setFixedSize(size, size)

    def set_name(self, name: str) -> None:
        self._name = name
        self._model = skin_model.model_for_seed(name)
        self._pixmap = None
        self._skin_url = ""
        self.update()

    def set_pixmap(self, pixmap: QPixmap) -> None:
        if not pixmap.isNull():
            self._pixmap = pixmap
            self.update()

    def set_account(self, account: dict[str, Any] | None) -> None:
        self._skin_url = ""
        if not account:
            self.set_name("Player")
            return
        self._name = str(account.get("name") or "Player")
        self._model = skin_model.effective_model(account)
        url = str(account.get("skin_url") or "")
        if account.get("type") == "microsoft" and url:
            self.set_skin_url(url, self._model)
            return
        path = skin_model.effective_skin_path(account)
        self.set_skin_file(path, self._model)

    def set_skin_url(self, url: str, model: str | None = None) -> None:
        if not url:
            self.set_default_skin(model or self._model)
            return
        self._skin_url = url
        self._model = skin_model.normalize_model(model) or self._model

        def apply(skin: QPixmap) -> None:
            if self._skin_url != url:
                return
            rendered = head_pixmap(skin, self._size)
            if not rendered.isNull():
                self._pixmap = rendered
            else:
                self._pixmap = self._default_head(self._model)
            self.update()

        cached = image_loader().request(url, apply)
        if cached:
            apply(cached)
        else:
            # Keep a deterministic default visible while the network loads.
            self._pixmap = self._default_head(self._model)
            self.update()

    def set_skin_file(self, path: Path | str | None, model: str | None = None) -> None:
        self._skin_url = ""
        self._model = skin_model.normalize_model(model) or self._model
        skin = load_skin_pixmap(path)
        rendered = head_pixmap(skin, self._size) if not skin.isNull() else QPixmap()
        self._pixmap = rendered if not rendered.isNull() else self._default_head(self._model)
        self.update()

    def set_default_skin(self, model: str | None = None) -> None:
        self._skin_url = ""
        self._model = skin_model.normalize_model(model) or self._model
        self._pixmap = self._default_head(self._model)
        self.update()

    def _default_head(self, model: str) -> QPixmap:
        chosen = skin_model.normalize_model(model) or skin_model.MODEL_CLASSIC
        key = (chosen, self._size)
        cached = self._default_heads.get(key)
        if cached is not None:
            return cached
        skin = load_skin_pixmap(skin_model.default_skin_path(chosen))
        rendered = head_pixmap(skin, self._size) if not skin.isNull() else QPixmap()
        self._default_heads[key] = rendered
        return rendered

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = self.rect()
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5),
                            self._size * 0.16, self._size * 0.16)
        painter.setClipPath(path)
        pixmap = self._pixmap
        if pixmap is None or pixmap.isNull():
            pixmap = self._default_head(self._model)
        if not pixmap.isNull():
            scaled = pixmap.scaled(self.size(), Qt.AspectRatioMode.IgnoreAspectRatio,
                                   Qt.TransformationMode.FastTransformation)
            painter.drawPixmap(rect, scaled)
        else:
            painter.fillRect(rect, QColor("#4AAE55"))
        painter.end()


class BrandMark(QWidget):
    """Theme mark: static for most themes; command block/sea lantern animate."""

    def __init__(self, theme_id: str = "creeper", size: int = 30,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._size = size
        self._theme_id = str(getattr(theme_id, "id", theme_id))
        self._pixmap = QPixmap()
        self._frame = 0
        self._frame_count = 1
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._next_frame)
        self._load_theme(self._theme_id)

    def set_theme(self, theme_id) -> None:
        theme_id = str(getattr(theme_id, "id", theme_id))
        if theme_id == self._theme_id:
            return
        self._theme_id = theme_id
        self._load_theme(theme_id)

    def _load_theme(self, theme_id: str) -> None:
        self._timer.stop()
        self._frame = 0
        self._pixmap = QPixmap(str(icons.theme_icon_path(theme_id)))
        frames = {"ivory": 4, "prismarine": 5}.get(theme_id, 1)
        self._frame_count = frames if self._pixmap.height() >= frames * 16 else 1
        if self._frame_count > 1:
            self._timer.setInterval(160 if theme_id == "prismarine" else 500)
            self._timer.start()
        self.update()

    def _next_frame(self) -> None:
        self._frame = (self._frame + 1) % self._frame_count
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        if self._pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        if self._frame_count > 1:
            source = QRectF(0, self._frame * 16, 16, 16)
        else:
            source = QRectF(self._pixmap.rect())
        painter.drawPixmap(QRectF(self.rect()), self._pixmap, source)
        painter.end()


class Pill(QLabel):
    TONES = {
        "accent": ("accent", 0.14, "accent"),
        "secondary": ("secondary", 0.14, "secondary_hi"),
        "muted": ("border", 0.2, "muted"),
        "success": ("success", 0.14, "success"),
        "danger": ("danger", 0.14, "danger"),
        "warning": ("warning", 0.14, "warning"),
        "hero": ("hero", 0.0, "hero"),
    }

    def __init__(self, text: str = "", tone: str = "accent", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.tone = tone
        self._theme = THEMES["creeper"]
        self.setContentsMargins(8, 3, 8, 3)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = self.font()
        font.setPointSizeF(8.4)
        font.setWeight(QFont.Weight.DemiBold)
        self.setFont(font)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def set_tone(self, tone: str) -> None:
        self.tone = tone
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        base, opacity, text_color = self.TONES.get(self.tone, self.TONES["accent"])
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self._theme.color(base))
        fill = QColor(color)
        fill.setAlphaF(max(0.10, opacity))
        painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 85), 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1),
                                self.height() * 0.35, self.height() * 0.35)
        painter.setPen(QColor(self._theme.color(text_color)))
        painter.setFont(self.font())
        painter.drawText(self.rect(), int(self.alignment()), self.text())
        painter.end()


class SegmentedControl(QFrame):
    changed = pyqtSignal(str)

    def __init__(self, items: Iterable[tuple[str, str] | tuple[str, str, str]],
                 current: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Segmented")
        self._theme = THEMES["creeper"]
        self.buttons: dict[str, QPushButton] = {}
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        for item in items:
            key, text = item[0], item[1]
            icon_name = item[2] if len(item) > 2 else None
            button = QPushButton(text)
            button.setProperty("role", "segment")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            if icon_name:
                set_icon(button, icon_name, "muted", 15)
            button.clicked.connect(lambda checked=False, k=key: self.set_current(k, emit=True))
            self.group.addButton(button)
            self.buttons[key] = button
            layout.addWidget(button)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        if current:
            self.set_current(current)
        elif self.buttons:
            self.set_current(next(iter(self.buttons)))

    def set_current(self, key: str, emit: bool = False) -> None:
        if key not in self.buttons:
            return
        for name, button in self.buttons.items():
            button.setChecked(name == key)
            button.setProperty("active", "true" if name == key else "false")
            repolish(button)
        if emit:
            self.changed.emit(key)

    def current(self) -> str:
        for key, button in self.buttons.items():
            if button.isChecked():
                return key
        return ""

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme


class SearchBox(QLineEdit):
    def __init__(self, placeholder: str = "搜索…", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "search")
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self.setTextMargins(27, 0, 2, 0)
        self.setFixedHeight(36)
        self._icon = icons.pixmap("search", "#8A9B94", 15)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.drawPixmap(11, (self.height() - 15) // 2, self._icon)
        painter.end()


class EmptyState(QWidget):
    def __init__(self, title: str, description: str, icon_name: str = "info",
                 action_text: str = "", action_icon: str = "plus",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = THEMES["creeper"]
        self._icon_name = icon_name
        self.setMinimumHeight(240)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 28, 20, 28)
        outer.setSpacing(9)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ring = QFrame()
        self._ring.setFixedSize(58, 58)
        ring_layout = QHBoxLayout(self._ring)
        ring_layout.setContentsMargins(0, 0, 0, 0)
        self._mark = QLabel()
        self._mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ring_layout.addWidget(self._mark)
        outer.addWidget(self._ring, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.set_theme(self._theme)
        title_label = label(title, "section")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc = label(description, "muted", wrap=True)
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setMaximumWidth(380)
        outer.addWidget(title_label)
        outer.addWidget(desc, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.action_button: QPushButton | None = None
        if action_text:
            self.action_button = make_button(action_text, "secondary", action_icon, "accent")
            outer.addWidget(self.action_button, alignment=Qt.AlignmentFlag.AlignHCenter)

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._ring.setStyleSheet(
            f"QFrame {{ border: 1px solid {theme.border}; border-radius: 29px;"
            f" background: {theme.surface_alt}; }}")
        self._mark.setPixmap(icons.pixmap(self._icon_name, theme.accent, 24))


class ToggleSwitch(QAbstractButton):
    def __init__(self, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(42, 23)
        self._offset = 1.0 if checked else 0.0
        self._animation = QPropertyAnimation(self, b"offset", self)
        self._animation.setDuration(160)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)
        self._theme = THEMES["creeper"]

    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, value: float) -> None:
        self._offset = value
        self.update()

    offset = pyqtProperty(float, _get_offset, _set_offset)

    def _animate(self, checked: bool) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._offset)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(self._theme.accent) if self.isChecked() else QColor(self._theme.border)
        if not self.isEnabled():
            bg.setAlpha(100)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 11.5, 11.5)
        knob_color = QColor(self._theme.card) if self.isChecked() else QColor(self._theme.text_muted)
        painter.setBrush(knob_color)
        diameter = self.height() - 6
        x = 3 + (self.width() - 6 - diameter) * self._offset
        painter.drawEllipse(QRectF(x, 3, diameter, diameter))
        if self.isChecked():
            glow = QColor(self._theme.accent_hi)
            glow.setAlpha(70)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(glow, 1))
            painter.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 11, 11)
        painter.end()


class SettingRow(QWidget):
    def __init__(self, title: str, description: str = "", control: QWidget | None = None,
                 icon_name: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 9, 0, 9)
        layout.setSpacing(12)
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        if icon_name:
            icon_label = QLabel()
            icon_label.setPixmap(icons.pixmap(icon_name, "#AAB9B2", 15))
            icon_label.setFixedSize(17, 18)
            title_row.addWidget(icon_label)
        title_label = label(title, "cardTitle")
        title_row.addWidget(title_label)
        title_row.addStretch()
        text_col.addLayout(title_row)
        if description:
            desc = label(description, "faint", wrap=True)
            desc.setMaximumWidth(620)
            text_col.addWidget(desc)
        layout.addLayout(text_col, 1)
        if control is not None:
            layout.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)


class SectionCard(Card):
    def __init__(self, title: str, subtitle: str = "", icon_name: str | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent, padding=18, spacing=8)
        self.body.setContentsMargins(20, 16, 20, 13)
        self.header = QHBoxLayout()
        self.header.setSpacing(10)
        self._icon_name = icon_name
        if icon_name:
            self.icon_label = QLabel()
            self.icon_label.setPixmap(icons.pixmap(icon_name, "#5A675C", 18))
            self.icon_label.setFixedSize(22, 22)
            self.header.addWidget(self.icon_label)
        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        self.title = label(title, "section")
        title_col.addWidget(self.title)
        if subtitle:
            self.subtitle = label(subtitle, "faint", wrap=True)
            title_col.addWidget(self.subtitle)
        self.header.addLayout(title_col, 1)
        self.body.addLayout(self.header)
        self.body.addWidget(hline())
        self.rows = QVBoxLayout()
        self.rows.setSpacing(0)
        self.body.addLayout(self.rows)

    def set_theme(self, theme: Theme) -> None:
        if self._icon_name:
            self.icon_label.setPixmap(icons.pixmap(self._icon_name, theme.accent, 18))

    def add_row(self, row: QWidget) -> None:
        self.rows.addWidget(row)
        divider = hline()
        self.rows.addWidget(divider)

    def add_widget(self, widget: QWidget) -> None:
        self.rows.addWidget(widget)

    def finish(self) -> None:
        if self.rows.count() and isinstance(self.rows.itemAt(self.rows.count() - 1).widget(), QFrame):
            last = self.rows.itemAt(self.rows.count() - 1).widget()
            if last.objectName() == "Divider":
                last.deleteLater()


class InfoBanner(QFrame):
    TONES = {"info": "secondary", "warning": "warning", "danger": "danger", "success": "success"}

    def __init__(self, text: str, tone: str = "info", icon_name: str = "info",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tone = tone
        self.setObjectName("InfoBanner")
        self._theme = THEMES["creeper"]
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(8)
        self._icon_name = icon_name
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(18, 18)
        self.icon_label.setPixmap(icons.pixmap(icon_name, "#5A675C", 17))
        layout.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignTop)
        self.text = label(text, "muted", wrap=True)
        layout.addWidget(self.text, 1)

    def set_text(self, text: str) -> None:
        self.text.setText(text)

    def set_tone(self, tone: str, icon_name: str = "info") -> None:
        self.tone = tone
        self._icon_name = icon_name
        self.icon_label.setPixmap(icons.pixmap(
            icon_name, self._theme.color(self.TONES.get(tone, "secondary")), 17))
        self.update()

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.icon_label.setPixmap(icons.pixmap(self._icon_name, theme.color(
            self.TONES.get(self.tone, "secondary")), 17))
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self._theme.color(self.TONES.get(self.tone, "secondary")))
        bg = QColor(color)
        bg.setAlpha(20)
        painter.setBrush(bg)
        painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 55), 1))
        painter.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 9, 9)
        painter.end()


class GearSpinner(QWidget):
    # A class-level default keeps the timer safe even if an instance is
    # created without running __init__ (for example by a UI hot-reload).
    _angle = 0

    def __init__(self, size: int = 18, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._angle = 0
        self._size = size
        self._theme = THEMES["creeper"]
        self.setFixedSize(size, size)
        self._timer = QTimer(self)
        self._timer.setInterval(38)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self) -> None:
        self._angle = (int(getattr(self, "_angle", 0)) + 9) % 360
        self.update()

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.rotate(int(getattr(self, "_angle", 0)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._theme.accent))
        radius = self.width() * 0.40
        # A plain loading ring, not a steampunk gear.
        painter.drawArc(QRectF(-radius, -radius, radius * 2, radius * 2), 0, 285 * 16)
        painter.end()


class ScrollPage(QWidget):
    def __init__(self, parent: QWidget | None = None, *, margins: tuple[int, int, int, int] = (0, 0, 0, 0),
                 spacing: int = 18) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content = QWidget()
        self.content.setObjectName("PageContent")
        self.layout = QVBoxLayout(self.content)
        self.layout.setContentsMargins(*margins)
        self.layout.setSpacing(spacing)
        self.scroll.setWidget(self.content)
        outer.addWidget(self.scroll)

    def add(self, widget: QWidget, stretch: int = 0) -> None:
        self.layout.addWidget(widget, stretch)

    def add_layout(self, layout, stretch: int = 0) -> None:
        self.layout.addLayout(layout, stretch)

    def add_stretch(self, stretch: int = 1) -> None:
        self.layout.addStretch(stretch)


class PageHeader(QWidget):
    def __init__(self, eyebrow: str, title: str, subtitle: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # The parameter is retained for compatibility; the old English eyebrow
        # was decorative and is intentionally not rendered.
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        text_col = QVBoxLayout()
        text_col.setSpacing(3)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.title_label = label(title, "pageTitle")
        row.addWidget(self.title_label)
        self.badge_holder = QHBoxLayout()
        self.badge_holder.setSpacing(6)
        row.addLayout(self.badge_holder)
        row.addStretch()
        text_col.addLayout(row)
        if subtitle:
            text_col.addWidget(label(subtitle, "subtitle", wrap=True))
        layout.addLayout(text_col, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        layout.addLayout(self.actions)

    def add_action(self, widget: QWidget) -> None:
        self.actions.addWidget(widget)

    def add_badge(self, pill: QWidget) -> None:
        self.badge_holder.addWidget(pill)


# --------------------------------------------------------------------------- image loading
class RemoteImageLoader(QObject):
    loaded = pyqtSignal(str, QPixmap)

    def __init__(self) -> None:
        super().__init__()
        self.manager = QNetworkAccessManager(self)
        self.cache: dict[str, QPixmap] = {}
        self.replies: dict[str, QNetworkReply] = {}
        self.pending: dict[str, list[Callable[[QPixmap], None]]] = {}
        self._cache_dir = cache_dir() / "images"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def request(self, url: str, callback: Callable[[QPixmap], None] | None = None) -> QPixmap | None:
        if not url:
            return None
        if url in self.cache and not self.cache[url].isNull():
            pixmap = self.cache[url]
            if callback:
                callback(pixmap)
            return pixmap
        cache_path = self._cache_dir / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".img")
        if cache_path.exists():
            pixmap = QPixmap(str(cache_path))
            if not pixmap.isNull():
                self.cache[url] = pixmap
                if callback:
                    callback(pixmap)
                return pixmap
        if callback:
            self.pending.setdefault(url, []).append(callback)
        if url in self.replies:
            return None
        request = QNetworkRequest(QUrl(url))
        request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader,
                          "SteampunkHarborLauncher/1.0 (Modrinth image)")
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        reply = self.manager.get(request)
        self.replies[url] = reply
        reply.finished.connect(lambda u=url, r=reply: self._finished(u, r, cache_path))
        return None

    def _finished(self, url: str, reply: QNetworkReply, cache_path: Path) -> None:
        pixmap = QPixmap()
        if reply.error() == QNetworkReply.NetworkError.NoError:
            data = bytes(reply.readAll())
            if data:
                pixmap.loadFromData(data)
        self.replies.pop(url, None)
        callbacks = self.pending.pop(url, [])
        reply.deleteLater()
        if not pixmap.isNull():
            self.cache[url] = pixmap
            try:
                cache_path.write_bytes(data)
            except OSError:
                pass
            for callback in callbacks:
                try:
                    callback(pixmap)
                except RuntimeError:
                    pass
            self.loaded.emit(url, pixmap)


_IMAGE_LOADER: RemoteImageLoader | None = None


def image_loader() -> RemoteImageLoader:
    global _IMAGE_LOADER
    if _IMAGE_LOADER is None:
        _IMAGE_LOADER = RemoteImageLoader()
    return _IMAGE_LOADER


class ProjectIcon(QWidget):
    def __init__(self, size: int = 46, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._fallback = "M"
        self._theme = THEMES["creeper"]
        self._size = size
        self.setFixedSize(size, size)

    def _fit_pixmap(self, pixmap: QPixmap, smooth: bool = True) -> QPixmap:
        target = self._size * 2
        scaled = pixmap.scaled(target, target, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                               Qt.TransformationMode.SmoothTransformation if smooth
                               else Qt.TransformationMode.FastTransformation)
        x = max(0, (scaled.width() - target) // 2)
        y = max(0, (scaled.height() - target) // 2)
        return scaled.copy(x, y, target, target)

    def set_icon_url(self, url: str) -> None:
        def apply(pixmap: QPixmap) -> None:
            if not pixmap.isNull():
                self._pixmap = self._fit_pixmap(pixmap)
                self.update()
        cached = image_loader().request(url, apply)
        if cached:
            apply(cached)

    def set_fallback(self, letter: str) -> None:
        self._fallback = (letter or "M")[:1].upper()
        self.update()

    def set_local_icon(self, path: str) -> None:
        if not path:
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return
        self._pixmap = self._fit_pixmap(pixmap)
        self.update()

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 11, 11)
        painter.setClipPath(path)
        painter.fillPath(path, QColor(self._theme.surface_alt))
        if not self._pixmap.isNull():
            painter.drawPixmap(self.rect(), self._pixmap)
        else:
            grad = QLinearGradient(0, 0, self.width(), self.height())
            grad.setColorAt(0, QColor(self._theme.card_alt))
            grad.setColorAt(1, QColor(self._theme.border))
            painter.fillRect(self.rect(), grad)
            painter.setPen(QColor(self._theme.accent_hi))
            font = QFont("Segoe UI", 15)
            font.setWeight(QFont.Weight.Bold)
            painter.setFont(font)
            painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), self._fallback)
        painter.setClipping(False)
        painter.setPen(QPen(QColor(255, 255, 255, 17), 1))
        painter.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 11, 11)
        painter.end()


class DropZone(QFrame):
    dropped = pyqtSignal(str)

    def __init__(self, text: str = "拖入 .jar 或整合包文件", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMinimumHeight(120)
        self._theme = THEMES["creeper"]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 22, 20, 22)
        layout.setSpacing(6)
        self._drop_icon = QLabel()
        self._drop_icon.setPixmap(icons.pixmap("upload", "#5A675C", 24))
        self._drop_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._drop_icon)
        text_label = label(text, "muted")
        text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text_label.setWordWrap(True)
        layout.addWidget(text_label)
        hint = label("支持 Fabric / Forge / NeoForge 模组与整合包", "faint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)
        self._hover = False

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._drop_icon.setPixmap(icons.pixmap("upload", theme.accent, 24))
        self.update()

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            self._hover = True
            self.update()
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # type: ignore[override]
        self._hover = False
        self.update()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        self._hover = False
        self.update()
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.dropped.emit(url.toLocalFile())
                break
        event.acceptProposedAction()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self._theme.accent if self._hover else self._theme.border)
        color.setAlpha(210 if self._hover else 110)
        pen = QPen(color, 1.2, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(QColor(self._theme.card_alt) if self._hover else Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 8, 8)
        painter.end()


class ToastCard(QFrame):
    def __init__(self, text: str, kind: str = "info", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setObjectName("Toast")
        self._theme = THEMES["creeper"]
        layout = QHBoxLayout(self)
        layout.setContentsMargins(13, 10, 15, 10)
        layout.setSpacing(9)
        icon_name = {"info": "info", "success": "check_circle", "error": "alert",
                     "warning": "alert"}.get(kind, "info")
        role = {"info": "secondary", "success": "success", "error": "danger",
                "warning": "warning"}.get(kind, "secondary")
        self._icon = QLabel()
        self._icon.setPixmap(icons.pixmap(icon_name, "#D9B06B", 16))
        self._icon.setFixedSize(18, 18)
        layout.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        self.message = label(text, "muted", wrap=True)
        self.message.setMaximumWidth(460)
        self.message.setMinimumWidth(180)
        self.message.setSizePolicy(QSizePolicy.Policy.Preferred,
                                   QSizePolicy.Policy.Preferred)
        layout.addWidget(self.message, 1)
        self.setMinimumWidth(280)
        self.setMinimumHeight(44)
        self.setMaximumWidth(560)
        self.setMaximumHeight(180)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        role = {"info": "secondary", "success": "success", "error": "danger",
                "warning": "warning"}.get(self.kind, "secondary")
        icon_name = {"info": "info", "success": "check_circle", "error": "alert",
                     "warning": "alert"}.get(self.kind, "info")
        self._icon.setPixmap(icons.pixmap(icon_name, theme.color(role), 16))
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self._theme.color({
            "info": "secondary", "success": "success", "error": "danger",
            "warning": "warning"}.get(self.kind, "secondary")))
        bg = QColor(self._theme.card_alt)
        bg.setAlpha(248)
        painter.setBrush(bg)
        painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 95), 1))
        painter.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 10, 10)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(0, 13, 3, self.height() - 26), 1.5, 1.5)
        painter.end()


class ToastHost(QWidget):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(24, 16, 24, 24)
        self.layout.setSpacing(8)
        self.layout.addStretch()
        self.toasts: list[tuple[ToastCard, QGraphicsOpacityEffect, QTimer]] = []
        self._theme = THEMES["creeper"]

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        for toast, _, _ in self.toasts:
            toast.set_theme(theme)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        if self.parentWidget():
            self.setGeometry(self.parentWidget().rect())
        super().resizeEvent(event)

    def toast(self, text: str, kind: str = "info", duration: int = 3800) -> None:
        card = ToastCard(text, kind)
        card.set_theme(self._theme)
        effect = QGraphicsOpacityEffect(card)
        effect.setOpacity(0.0)
        card.setGraphicsEffect(effect)
        self.layout.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter)
        self.toasts.append((card, effect, QTimer(self)))
        animation = QPropertyAnimation(effect, b"opacity", card)
        animation.setDuration(180)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        timer = self.toasts[-1][2]
        timer.setSingleShot(True)
        timer.timeout.connect(lambda c=card: self._dismiss(c))
        timer.start(duration)
        self.raise_()

    def _dismiss(self, card: ToastCard) -> None:
        for entry in list(self.toasts):
            if entry[0] is card:
                effect = entry[1]
                animation = QPropertyAnimation(effect, b"opacity", card)
                animation.setDuration(180)
                animation.setStartValue(1.0)
                animation.setEndValue(0.0)
                animation.finished.connect(lambda c=card: self._remove(c))
                animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
                break

    def _remove(self, card: ToastCard) -> None:
        for index, (item, _, timer) in enumerate(self.toasts):
            if item is card:
                timer.stop()
                self.toasts.pop(index)
                self.layout.removeWidget(card)
                card.setParent(None)
                card.deleteLater()
                break


class ThemePreview(QAbstractButton):
    def __init__(self, theme: Theme, active: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme_data = theme
        self.active = active
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(172, 118)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(118)

    def set_active(self, active: bool) -> None:
        self.active = active
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self.theme_data
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        path = QPainterPath()
        path.addRoundedRect(rect, 13, 13)
        painter.setClipPath(path)
        grad = QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0, QColor(t.card))
        grad.setColorAt(1, QColor(t.card_alt))
        painter.fillRect(self.rect(), grad)
        # Mini application window.
        card = QRectF(12, 16, self.width() - 40, self.height() - 28)
        painter.setBrush(QColor(t.bg))
        painter.setPen(QPen(QColor(t.border), 1))
        painter.drawRoundedRect(card, 7, 7)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(t.accent))
        painter.drawRoundedRect(QRectF(card.left() + 7, card.top() + 8, 27, 5), 2.5, 2.5)
        painter.setBrush(QColor(t.card_alt))
        painter.drawRoundedRect(QRectF(card.left() + 7, card.top() + 20, card.width() - 14, 18), 4, 4)
        painter.setBrush(QColor(t.secondary))
        painter.drawRoundedRect(QRectF(card.left() + 7, card.top() + 45, 56, 24), 5, 5)
        painter.setBrush(QColor(t.surface_alt))
        painter.drawRoundedRect(QRectF(card.left() + 70, card.top() + 45, card.width() - 77, 24), 5, 5)
        painter.setBrush(QColor(t.card_alt))
        painter.drawRoundedRect(QRectF(card.left() + 7, card.top() + 76, card.width() - 14, 5), 2.5, 2.5)
        painter.drawRoundedRect(QRectF(card.left() + 7, card.top() + 85, card.width() - 44, 4), 2, 2)
        # Accent dots
        for i, color in enumerate((t.accent, t.secondary, t.text_muted)):
            painter.setBrush(QColor(color))
            painter.drawEllipse(QRectF(self.width() - 25, 18 + i * 11, 6, 6))
        painter.setClipping(False)
        if self.active:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(t.accent), 2))
            painter.drawRoundedRect(rect, 13, 13)
            painter.setBrush(QColor(t.accent))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(self.width() - 29, self.height() - 29, 17, 17))
            painter.setPen(QPen(QColor(t.on_accent), 1.8))
            painter.drawLine(QPointF(self.width() - 25, self.height() - 20.5),
                             QPointF(self.width() - 22, self.height() - 17.5))
            painter.drawLine(QPointF(self.width() - 22, self.height() - 17.5),
                             QPointF(self.width() - 16, self.height() - 24))
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(t.border_soft), 1))
            painter.drawRoundedRect(rect, 13, 13)
        painter.end()


class LoadingDots(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._step = 0
        self.setFixedSize(32, 16)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(180)
        self._theme = THEMES["creeper"]

    def _tick(self) -> None:
        self._step = (self._step + 1) % 4
        self.update()

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i in range(3):
            alpha = 240 if i == self._step % 3 else 70
            color = QColor(self._theme.accent)
            color.setAlpha(alpha)
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(i * 10, 6, 5, 5))
        painter.end()
