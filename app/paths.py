from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .constants import (
    APP_BUILD, APP_BUILD_NAME, APP_ID, APP_NAME, APP_VERSION, TEST_MODE,
)


def is_compiled() -> bool:
    """True for Nuitka and PyInstaller builds, false for a source checkout."""

    return bool(getattr(sys, "frozen", False) or "__compiled__" in globals())


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    # Nuitka standalone/onefile keeps the bundled data next to this module,
    # inside the extraction directory (sys._MEIPASS is PyInstaller-only).
    return Path(__file__).resolve().parents[1]


def executable_dir() -> Path:
    # Nuitka onefile extracts to %TEMP% but exposes the original exe folder.
    # Using it keeps portable.flag, .SHL and game data next to the executable.
    onefile_dir = os.environ.get("NUITKA_ONEFILE_DIRECTORY")
    if onefile_dir:
        return Path(onefile_dir).resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    if "__compiled__" in globals():
        candidate = Path(sys.argv[0]).resolve()
        if candidate.suffix.lower() == ".exe":
            return candidate.parent
        return Path(sys.executable).resolve().parent
    return project_root()


def icons_dir() -> Path:
    return project_root() / "icons"


def resource_dir() -> Path:
    # Kept as a compatibility alias: all launcher artwork now lives in icons/.
    return icons_dir()


def generated_dir() -> Path:
    return icons_dir()


def _shl_dir() -> Path:
    return executable_dir() / ".SHL"


def _directory_writable(path: Path) -> bool:
    """A directory is usable only if a file can actually be created in it.

    ``Path.mkdir(exist_ok=True)`` succeeds on an existing directory even when
    the current (Low Integrity) token cannot write there; without this probe
    Settings would silently use an unwritable path and drop data/logs elsewhere.
    """

    probe = path / f".shl-write-probe-{os.getpid()}"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def data_dir() -> Path:
    """Developer test mode, portable mode, or normal per-user data.

    TEST_MODE is a source constant in app/constants.py. There is deliberately
    no command-line flag or UI switch for it.
    """
    if TEST_MODE:
        # Literal project root: test settings, logs and caches land next to
        # the source tree rather than in AppData or a hidden subdirectory.
        path = project_root()
    else:
        override = os.environ.get("SHL_DATA_DIR")
        if override:
            path = Path(override).expanduser()
        elif (executable_dir() / "portable.flag").exists() or os.environ.get("SHL_PORTABLE") == "1":
            path = executable_dir() / "data"
        else:
            base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or str(Path.home())
            path = Path(base) / "SteampunkHarborLauncher"
    try:
        path.mkdir(parents=True, exist_ok=True)
        if _directory_writable(path):
            return path
        raise OSError(f"data directory is not writable: {path}")
    except OSError:
        # Low-integrity processes cannot write %APPDATA%. Keep every runtime
        # file inside the existing .SHL folder next to the exe instead of
        # scattering data/, SHL-logs/ or .minecraft next to the exe.
        fallback = _shl_dir() / "data"
        if fallback == path:
            raise
        try:
            fallback.mkdir(parents=True, exist_ok=True)
            if _directory_writable(fallback):
                return fallback
        except OSError:
            pass
        fallback = executable_dir() / "data"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def _runtime_subdir(name: str) -> Path:
    """Create a launcher subdirectory, falling back inside .SHL or temp.

    Import-time callers (JavaManager cache) must never crash because a security
    product locked the roaming profile.
    """

    try:
        path = data_dir() / name
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        for fallback in (_shl_dir() / name,
                         Path(tempfile.gettempdir()) / "SHL" / name):
            try:
                fallback.mkdir(parents=True, exist_ok=True)
                return fallback
            except OSError:
                continue
        return Path(tempfile.gettempdir()) / "SHL" / name


def logs_dir() -> Path:
    return _runtime_subdir("logs")


def cache_dir() -> Path:
    return _runtime_subdir("cache")


def runtimes_dir() -> Path:
    return _runtime_subdir("runtimes")


def settings_file() -> Path:
    return data_dir() / "settings.json"


def version_profiles_file() -> Path:
    return data_dir() / "version_profiles.json"


def current_game_dir() -> Path:
    """Portable game root: the folder containing the launcher."""
    return executable_dir()


def official_minecraft_dir() -> Path:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        return (Path(appdata) if appdata else Path.home() / "AppData" / "Roaming") / ".minecraft"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "minecraft"
    return Path.home() / ".minecraft"


def current_minecraft_dir() -> Path:
    """Portable game data lives in a ``.minecraft`` subfolder."""

    return current_game_dir() / ".minecraft"


def normalize_custom_minecraft_dir(path: Path | str) -> Path:
    """Every non-official location gets its own ``.minecraft`` folder."""

    selected = Path(path).expanduser()
    if selected.name.casefold() == ".minecraft":
        return selected
    return selected / ".minecraft"


def infer_game_dir_mode(path: Path | str) -> str:
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError:
        return "custom"
    if resolved == official_minecraft_dir().resolve():
        return "official"
    if resolved in {current_minecraft_dir().resolve(), current_game_dir().resolve()}:
        return "current"
    return "custom"


def default_minecraft_dir() -> Path:
    """Standard Minecraft location: ``%APPDATA%\\.minecraft`` on Windows.

    Game versions, libraries, assets, mods, saves, resource packs and
    shader packs belong here, not next to the launcher executable.
    """

    if TEST_MODE:
        return project_root() / "test_game"
    override = os.environ.get("SHL_MINECRAFT_DIR")
    if override:
        return Path(override).expanduser()
    return official_minecraft_dir()


def ensure_launcher_metadata(game_dir: Path | str | None = None,
                            game_dir_mode: str = "") -> None:
    """Create/update the portable .SHL/version.json metadata folder.

    The file lives next to the launcher, never in AppData. Failure is
    non-fatal because an installed copy may sit in a read-only folder.
    """
    folder = executable_dir() / ".SHL"
    path = folder / "version.json"
    now = datetime.now(timezone.utc).isoformat()
    old: dict[str, object] = {}
    try:
        if path.exists():
            old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    payload = {
        "format": 1,
        "product": APP_BUILD_NAME,
        "display_name": APP_NAME,
        "version": APP_VERSION,
        "build": APP_BUILD,
        "channel": "alpha",
        "created_at": old.get("created_at") or now,
        "updated_at": now,
    }
    if game_dir is not None:
        payload["game_dir"] = str(game_dir)
        payload["game_dir_mode"] = game_dir_mode or infer_game_dir_mode(game_dir)
    elif old.get("game_dir"):
        payload["game_dir"] = old["game_dir"]
        payload["game_dir_mode"] = old.get("game_dir_mode") or "custom"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    except OSError:
        pass


def ensure_game_dirs(minecraft_dir: Path) -> None:
    for name in ("versions", "libraries", "assets", "assets/indexes", "assets/objects", "mods",
                 "resourcepacks", "shaderpacks", "saves", "logs"):
        (minecraft_dir / name).mkdir(parents=True, exist_ok=True)


def ensure_all() -> None:
    # Startup must survive a locked/read-only data or log directory; the
    # launcher can still open and the user can move the directory later.
    for factory in (data_dir, logs_dir, cache_dir, runtimes_dir):
        try:
            factory()
        except OSError:
            pass
    try:
        ensure_launcher_metadata()
    except OSError:
        pass


def app_icon_path() -> Path:
    ico = icons_dir() / "app_icon.ico"
    return ico if ico.exists() else icons_dir() / "app_icon.png"


__all__ = [
    "APP_ID", "is_compiled", "project_root", "executable_dir", "icons_dir", "resource_dir",
    "generated_dir", "data_dir", "logs_dir", "cache_dir", "runtimes_dir", "settings_file",
    "version_profiles_file", "current_game_dir", "current_minecraft_dir",
    "normalize_custom_minecraft_dir", "official_minecraft_dir",
    "infer_game_dir_mode", "default_minecraft_dir", "ensure_game_dirs",
    "ensure_launcher_metadata", "ensure_all", "app_icon_path",
]
