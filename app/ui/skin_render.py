from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QPixmap


def load_skin_pixmap(path: str | Path | None) -> QPixmap:
    if not path:
        return QPixmap()
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        return QPixmap()
    if pixmap.width() != 64 or pixmap.height() not in (32, 64):
        return QPixmap()
    return pixmap


def head_pixmap(skin: QPixmap, size: int) -> QPixmap:
    """Render a 2D front head from a Java skin texture (with hat overlay)."""
    if skin.isNull() or skin.width() < 64 or skin.height() < 32:
        return QPixmap()
    face = skin.copy(8, 8, 8, 8)
    overlay = skin.copy(40, 8, 8, 8)
    combined = QPixmap(8, 8)
    combined.fill(Qt.GlobalColor.transparent)
    painter = QPainter(combined)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.drawPixmap(0, 0, face)
    painter.drawPixmap(0, 0, overlay)
    painter.end()
    return combined.scaled(max(1, size), max(1, size),
                           Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.FastTransformation)


__all__ = ["load_skin_pixmap", "head_pixmap"]
