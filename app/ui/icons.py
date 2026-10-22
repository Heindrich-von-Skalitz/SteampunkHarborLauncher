from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from ..paths import generated_dir, resource_dir

_SVG_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
    'width="24" height="24" fill="none" stroke="{color}" stroke-width="{width}" '
    'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
)

_ICONS: dict[str, str] = {
    "wheel": """
      <circle cx="12" cy="12" r="8.3"/><circle cx="12" cy="12" r="2.3"/>
      <path d="M12 3.7v4.1M12 16.2v4.1M3.7 12h4.1M16.2 12h4.1M6.1 6.1l2.9 2.9M15 15l2.9 2.9M17.9 6.1 15 9M9 15l-2.9 2.9"/>
    """,
    "compass": """
      <circle cx="12" cy="12" r="8.5"/><path d="m15.3 8.7-1.9 4.7-4.7 1.9 1.9-4.7 4.7-1.9Z"/>
    """,
    "download": """
      <path d="M12 3.5v11.2M7.3 10.3 12 15l4.7-4.7M4.5 16.8v2.3c0 .8.6 1.4 1.4 1.4h12.2c.8 0 1.4-.6 1.4-1.4v-2.3"/>
    """,
    "upload": """
      <path d="M12 20.5V9.3M7.3 13.7 12 9l4.7 4.7M4.5 7.2V4.9c0-.8.6-1.4 1.4-1.4h12.2c.8 0 1.4.6 1.4 1.4v2.3"/>
    """,
    "layers": """
      <path d="m12 3.2 8.5 4.4-8.5 4.5-8.5-4.5L12 3.2Z"/><path d="m3.5 12 8.5 4.5 8.5-4.5M3.5 16.4l8.5 4.4 8.5-4.4"/>
    """,
    "package": """
      <path d="m12 3 8 4.2v9.6L12 21l-8-4.2V7.2L12 3Z"/><path d="M4.3 7.4 12 11.5l7.7-4.1M12 11.5V21M8 5l8 4.3"/>
    """,
    "box": """
      <path d="m12 3 8 4.3v9.4L12 21l-8-4.3V7.3L12 3Z"/><path d="M4.4 7.5 12 11.7l7.6-4.2M12 11.7V21"/>
    """,
    "sliders": """
      <path d="M4 6h8M16 6h4M4 12h3M11 12h9M4 18h9M17 18h3"/>
      <circle cx="14" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="15" cy="18" r="2"/>
    """,
    "search": """
      <circle cx="10.7" cy="10.7" r="6.4"/><path d="m15.4 15.4 5 5"/>
    """,
    "refresh": """
      <path d="M20 7v4.5h-4.5M4 17v-4.5h4.5"/><path d="M18.1 15.7A7.6 7.6 0 0 1 5.4 17M5.9 8.3A7.6 7.6 0 0 1 18.6 7"/>
    """,
    "folder": """
      <path d="M3.4 6.8c0-.9.7-1.6 1.6-1.6h4l2 2.2h8c.9 0 1.6.7 1.6 1.6v8.4c0 .9-.7 1.6-1.6 1.6H5c-.9 0-1.6-.7-1.6-1.6V6.8Z"/>
    """,
    "folder_open": """
      <path d="M3.4 7.2c0-.9.7-1.6 1.6-1.6h4l2 2.2h6.6c.9 0 1.6.7 1.6 1.6v1.2"/><path d="M4.5 19.5h13.3c.6 0 1.2-.4 1.4-1l2-6.1c.2-.7-.3-1.4-1.1-1.4H7.3c-.6 0-1.1.4-1.3.9L3.6 18c-.2.7.2 1.5.9 1.5Z"/>
    """,
    "play": """
      <path d="M8 5.3 19 12 8 18.7V5.3Z" fill="{color}"/>
    """,
    "stop": """
      <rect x="6" y="6" width="12" height="12" rx="2" fill="{color}"/>
    """,
    "trash": """
      <path d="M4.5 7h15M9 7V4.6c0-.6.5-1.1 1.1-1.1h3.8c.6 0 1.1.5 1.1 1.1V7M6.5 7l.8 12.1c.1.8.7 1.4 1.5 1.4h6.4c.8 0 1.4-.6 1.5-1.4L17.5 7M10 11v6M14 11v6"/>
    """,
    "plus": """<path d="M12 5v14M5 12h14"/>""",
    "minus": """<path d="M5 12h14"/>""",
    "close": """<path d="m6 6 12 12M18 6 6 18"/>""",
    "minimize": """<path d="M5 12h14"/>""",
    "maximize": """<rect x="5" y="5" width="14" height="14" rx="1.5"/>""",
    "restore": """<path d="M8 8V5h11v11h-3M5 8h11v11H5V8Z"/>""",
    "chevron_down": """<path d="m6 9 6 6 6-6"/>""",
    "chevron_up": """<path d="m6 15 6-6 6 6"/>""",
    "chevron_right": """<path d="m9 6 6 6-6 6"/>""",
    "chevron_left": """<path d="m15 6-6 6 6 6"/>""",
    "arrow_right": """<path d="M4 12h16M14 6l6 6-6 6"/>""",
    "arrow_left": """<path d="M20 12H4M10 6l-6 6 6 6"/>""",
    "user": """
      <circle cx="12" cy="8" r="3.7"/><path d="M4.8 20.2c.5-3.6 3.2-6.2 7.2-6.2s6.7 2.6 7.2 6.2"/>
    """,
    "users": """
      <circle cx="9" cy="8" r="3.2"/><path d="M3 19.5c.4-3.2 2.6-5.4 6-5.4s5.6 2.2 6 5.4M16 5.2a3 3 0 0 1 0 5.8M18 14.3c2 .5 3.2 2.3 3.4 5.2"/>
    """,
    "shield": """
      <path d="M12 3.2 20 6v5.6c0 4.7-3.3 7.6-8 9.2-4.7-1.6-8-4.5-8-9.2V6l8-2.8Z"/><path d="m8.8 11.8 2.2 2.2 4.3-4.4"/>
    """,
    "key": """
      <circle cx="8.4" cy="15.6" r="3.4"/><path d="m10.9 13.1 8-8M16 6l2 2M13.5 8.5l2 2"/>
    """,
    "globe": """
      <circle cx="12" cy="12" r="8.5"/><path d="M3.8 12h16.4M12 3.5c2.2 2.5 3.2 5.4 3.2 8.5S14.2 18 12 20.5C9.8 18 8.8 15.1 8.8 12S9.8 6 12 3.5Z"/>
    """,
    "link": """
      <path d="M10.2 13.8a4 4 0 0 0 5.7 0l2.6-2.6a4 4 0 0 0-5.7-5.7l-1.4 1.4"/><path d="M13.8 10.2a4 4 0 0 0-5.7 0l-2.6 2.6a4 4 0 0 0 5.7 5.7l1.4-1.4"/>
    """,
    "external": """
      <path d="M14 4h6v6M20 4l-8.5 8.5M18 14v4.5c0 .8-.7 1.5-1.5 1.5h-11C4.7 20 4 19.3 4 18.5v-11C4 6.7 4.7 6 5.5 6H10"/>
    """,
    "copy": """
      <rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5.5C16 4.7 15.3 4 14.5 4h-9C4.7 4 4 4.7 4 5.5v9c0 .8.7 1.5 1.5 1.5H8"/>
    """,
    "check": """<path d="m5 12.5 4.5 4.5L19 6.5"/>""",
    "check_circle": """
      <circle cx="12" cy="12" r="8.5"/><path d="m8 12.2 2.6 2.6 5.5-5.6"/>
    """,
    "x_circle": """
      <circle cx="12" cy="12" r="8.5"/><path d="m9 9 6 6M15 9l-6 6"/>
    """,
    "alert": """
      <path d="M12 3.8 21 20H3L12 3.8Z"/><path d="M12 9v4.5M12 17h.01"/>
    """,
    "info": """
      <circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5M12 7.6h.01"/>
    """,
    "cpu": """
      <rect x="7" y="7" width="10" height="10" rx="1.6"/><rect x="10" y="10" width="4" height="4" rx=".7"/><path d="M9.5 3v4M14.5 3v4M9.5 17v4M14.5 17v4M3 9.5h4M3 14.5h4M17 9.5h4M17 14.5h4"/>
    """,
    "memory": """
      <rect x="3.5" y="7" width="17" height="10" rx="1.8"/><path d="M6.5 10.5h2M10.3 10.5h2M14.1 10.5h2M17.9 10.5h.1M6.5 13.5h.1M9.7 13.5h4.6M17.9 13.5h.1M7 17v3M12 17v3M17 17v3"/>
    """,
    "java": """
      <path d="M5 13h11v4.4c0 .9-.7 1.6-1.6 1.6H6.6c-.9 0-1.6-.7-1.6-1.6V13ZM4 13h13M8 10.8c0-1.7 1.7-2.1 1.7-3.6 0-1.1-.8-1.7-.8-3.2"/><path d="M7.7 20.5h9.1M10 22h6"/>
    """,
    "clock": """
      <circle cx="12" cy="12" r="8.5"/><path d="M12 7v5.4l3.6 2.2"/>
    """,
    "monitor": """
      <rect x="3.5" y="4.5" width="17" height="12" rx="1.7"/><path d="M9 20h6M12 16.5V20"/>
    """,
    "terminal": """
      <rect x="3" y="4.5" width="18" height="15" rx="2"/><path d="m7 9 3 3-3 3M12.5 15H17"/>
    """,
    "archive": """
      <rect x="3.5" y="5" width="17" height="4" rx="1.2"/><path d="M5 9v10h14V9M10 12.5h4M9 5V3h6v2"/>
    """,
    "image": """
      <rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="8.5" cy="9.5" r="1.5"/><path d="m4.5 17 5-5 3.5 3.4 2.5-2.4 4 4"/>
    """,
    "book": """
      <path d="M4 5.5c0-.8.7-1.5 1.5-1.5H11v16H5.5C4.7 20 4 19.3 4 18.5v-13ZM20 5.5c0-.8-.7-1.5-1.5-1.5H13v16h5.5c.8 0 1.5-.7 1.5-1.5v-13Z"/>
    """,
    "save": """
      <path d="M4.5 4h12l3 3v13h-15V4Z"/><path d="M8 4v5h7V4M8 20v-6h8v6"/>
    """,
    "wrench": """
      <path d="M14.8 6.6a4.1 4.1 0 0 0-5.4 5.4L3.8 17.6a2 2 0 0 0 2.8 2.8l5.6-5.6a4.1 4.1 0 0 0 5.4-5.4l-2.6 2.6-2.8-.7-.7-2.8 3.3-3Z"/>
    """,
    "gear": """
      <circle cx="12" cy="12" r="3.2"/><path d="M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M18.5 5.5l-1.7 1.7M7.2 16.8l-1.7 1.7"/><circle cx="12" cy="12" r="8.3"/>
    """,
    "anchor": """
      <circle cx="12" cy="4.6" r="2"/><path d="M12 6.6V21M8 10h8M4 14c.7 3.4 3.7 6 8 6s7.3-2.6 8-6M4 14H7M20 14h-3"/>
    """,
    "sparkle": """
      <path d="m12 3 1.9 5.4L19 10.5l-5.1 2.1L12 18l-1.9-5.4L5 10.5l5.1-2.1L12 3Z"/><path d="m19 16 .8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8L19 16Z"/>
    """,
    "dots": """
      <circle cx="5.5" cy="12" r="1.2" fill="{color}"/><circle cx="12" cy="12" r="1.2" fill="{color}"/><circle cx="18.5" cy="12" r="1.2" fill="{color}"/>
    """,
    "grid": """
      <rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/>
    """,
    "list": """<path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01"/>""",
    "filter": """<path d="M3.5 5.5h17l-6.5 7.4v6.3l-4-2v-4.3L3.5 5.5Z"/>""",
    "sort": """<path d="M8 4v16M4 16l4 4 4-4M16 20V4M12 8l4-4 4 4"/>""",
    "edit": """<path d="m4.5 19.5 4.4-1 10-10a2 2 0 0 0 0-2.8l-.6-.6a2 2 0 0 0-2.8 0l-10 10-1 4.4Z"/><path d="m14.2 6.6 3.2 3.2"/>""",
    "server": """
      <rect x="3.5" y="4" width="17" height="6" rx="1.5"/><rect x="3.5" y="14" width="17" height="6" rx="1.5"/><path d="M7 7h.01M7 17h.01M11 7h5M11 17h5"/>
    """,
    "tag": """
      <path d="M3.7 11.2V4.5c0-.5.4-.9.9-.9h6.7c.3 0 .5.1.7.3l7.8 7.8c.4.4.4 1 0 1.4l-6.8 6.8c-.4.4-1 .4-1.4 0l-7.8-7.8a1 1 0 0 1-.1-.9Z"/><circle cx="8" cy="8" r="1.2"/>
    """,
    "log": """
      <path d="M6 3.5h9l4 4v13H6v-17Z"/><path d="M14.5 3.5V8H19M9 12h6M9 15.5h6"/>
    """,
    "sun": """
      <circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M18.7 5.3l-1.4 1.4M6.7 17.3l-1.4 1.4"/>
    """,
    "moon": """<path d="M20.2 15.1A8.5 8.5 0 0 1 8.9 3.8 8.5 8.5 0 1 0 20.2 15.1Z"/>""",
    "home": """
      <path d="m3.5 11 8.5-7 8.5 7"/><path d="M5.8 9.5v10h12.4v-10M9.8 19.5v-5.3h4.4v5.3"/>
    """,
    "pin": """<path d="m9 3 6 6-1.4 1.4 4 4L15 17l-4-4-1.4 1.4-6-6L6 5l3-2Z"/><path d="m8.5 15.5-5 5"/>""",
    "map": """
      <path d="m3.5 5.5 5.5-2 6 2 5.5-2v15l-5.5 2-6-2-5.5 2v-15Z"/><path d="M9 3.5v15M15 5.5v15"/>
    """,
    "loader": """
      <path d="m4 15.5 2-8 6-3 6 3 2 8-5 4.5h-6L4 15.5Z"/><path d="M8 9.5 12 12l4-2.5M12 12v7"/>
    """,
    "download_cloud": """
      <path d="M7 17.5H5.5a3.5 3.5 0 0 1-.4-7 5.5 5.5 0 0 1 10.8-1.2A4.1 4.1 0 0 1 19 17.5"/><path d="M12 10v9M8.5 15.4 12 19l3.5-3.6"/>
    """,
    "gamepad": """
      <path d="M7.5 7h9c2.8 0 4.5 2.4 4.5 5.2 0 3.1-1.2 5.3-3 5.3-1.2 0-1.9-.9-2.9-2H8.9c-1 1.1-1.7 2-2.9 2-1.8 0-3-2.2-3-5.3C3 9.4 4.7 7 7.5 7Z"/><path d="M8 10v4M6 12h4M15.5 11h.01M18 13h.01"/>
    """,
    "edit_pen": """<path d="m4 20 1-4.5L16.7 3.8a2.2 2.2 0 0 1 3.1 0l.4.4a2.2 2.2 0 0 1 0 3.1L8.5 19 4 20Z"/><path d="m14.6 6.9 2.5 2.5"/>""",
    "restart": """
      <path d="M20 7v4.5h-4.5M4 17v-4.5h4.5"/><path d="M18.1 15.7A7.6 7.6 0 0 1 5.4 17M5.9 8.3A7.6 7.6 0 0 1 18.6 7"/>
    """,
    "flag": """<path d="M5 21V4M5 4h12l-2.2 4L17 12H5"/>""",
    "heart": """<path d="M12 20.2S3.5 15.5 3.5 9.6A4.4 4.4 0 0 1 12 7a4.4 4.4 0 0 1 8.5 2.6c0 5.9-8.5 10.6-8.5 10.6Z"/>""",
}


@lru_cache(maxsize=512)
def icon(name: str, color: str = "#F2E7D2", size: int = 18, width: float = 1.7) -> QIcon:
    body = _ICONS.get(name)
    if not body:
        body = _ICONS["dots"]
    body = body.replace("{color}", color)
    svg = _SVG_TEMPLATE.format(color=color, width=width, body=body)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    scale = 2
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    ic = QIcon(pixmap)
    return ic


@lru_cache(maxsize=32)
def pixmap(name: str, color: str = "#F2E7D2", size: int = 18, width: float = 1.7) -> QPixmap:
    return icon(name, color, size, width).pixmap(size, size)


THEME_ICON_FILES = {
    "creeper": "creeper_head.png",
    "ocean": "heart_of_the_sea.png",
    "redstone": "redstone.png",
    "amethyst": "amethyst_shard.png",
    "graphite": "wither_skeleton.png",
    "ivory": "command_block.png",
    "alloy": "netherite_ingot.png",
    "cherry": "cherry_sapling.png",
    "prismarine": "sea_lantern.png",
    "lush": "glow_berries.png",
    "end": "chorus_fruit.png",
}


def theme_icon_path(theme_id: str):
    from ..paths import icons_dir
    return icons_dir() / THEME_ICON_FILES.get(str(theme_id), "creeper_head.png")


def loader_icon_name(loader: str) -> str:
    """Original vector icons; no Minecraft texture files are shipped."""
    return {
        "vanilla": "box",
        "fabric": "layers",
        "quilt": "layers",
        "forge": "wrench",
        "neoforge": "wrench",
        "optifine": "sun",
        "liteloader": "loader",
    }.get(str(loader or ""), "box")



def logo(size: int = 42) -> QPixmap:
    """Creeper-head brand mark. Keep the 16px pixel art crisp."""
    for path in (resource_dir() / "creeper_head.png",
                 generated_dir() / "app_icon.png"):
        pixmap = QPixmap(str(path))
        if not pixmap.isNull():
            return pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.FastTransformation)
    return QPixmap()
