from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ..constants import APP_NAME, APP_NAME_EN, APP_VERSION
from ..paths import data_dir
from ..storage import Settings, valid_player_name
from ..utils import (
    CancelledError, LauncherError, format_size, is_windows, now_stamp, read_json,
    required_java_major, rules_allow, short_path, split_cli_arguments, write_json,
)
from .java import JavaInfo
from .libraries import classpath_files, collect_library_files, extract_natives, missing_library_files
from .minecraft import InstalledVersion, MinecraftManager

LOG = logging.getLogger("shl.launch")


class LaunchError(LauncherError):
    pass


@dataclass
class LaunchPlan:
    version: InstalledVersion
    command: list[str]
    cwd: Path
    env: dict[str, str]
    java: JavaInfo
    game_dir: Path
    natives_dir: Path
    memory_mb: int
    log_path: Path
    maximize: bool = False
    warnings: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def display_command(self) -> str:
        # Never copy an access token into launcher logs or the clipboard. The
        # real Popen argument list is untouched.
        safe: list[str] = []
        redact_next = False
        secret_flags = {"--accessToken", "--session", "--auth-access-token"}
        for part in self.command:
            if redact_next:
                safe.append("***")
                redact_next = False
                continue
            if part in secret_flags:
                safe.append(part)
                redact_next = True
                continue
            safe.append(part)
        if is_windows():
            return subprocess.list2cmdline(safe)
        import shlex
        return " ".join(shlex.quote(part) for part in safe)


def _dedupe_artifacts(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _strip_jvm_overrides(args: list[str]) -> list[str]:
    """Remove heap/GC arguments that our presets replace."""
    prefixes = (
        "-Xmx", "-Xms", "-XX:+UseG1GC", "-XX:+UseZGC", "-XX:+ZGenerational",
        "-XX:G1NewSizePercent=", "-XX:G1ReservePercent=", "-XX:G1HeapRegionSize=",
        "-XX:MaxGCPauseMillis=", "-XX:MinHeapFreeRatio=", "-XX:MaxHeapFreeRatio=",
        "-XX:+PerfDisableSharedMem", "-XX:+UseCompactObjectHeaders",
        "-XX:+UnlockExperimentalVMOptions",
    )
    result = []
    for arg in args:
        if any(arg.startswith(prefix) for prefix in prefixes):
            continue
        result.append(arg)
    return result


def _remove_classpath_args(args: list[str]) -> list[str]:
    result: list[str] = []
    index = 0
    while index < len(args):
        if args[index] in {"-cp", "-classpath"}:
            index += 2
            continue
        if args[index].startswith("-Djava.library.path="):
            index += 1
            continue
        result.append(args[index])
        index += 1
    return result


def _resolve_gc_mode(settings: Settings, java: JavaInfo) -> str:
    mode = str(settings.get("gc_mode") or "auto")
    if mode in {"g1", "zgc", "none"}:
        return mode
    return "g1"


def _gc_arguments(mode: str, java: JavaInfo) -> list[str]:
    if mode == "none":
        return []
    if mode == "zgc" and java.major >= 15:
        args = ["-XX:+UnlockExperimentalVMOptions", "-XX:+UseZGC"]
        if java.major in {21, 22}:
            args.append("-XX:+ZGenerational")
        return args
    args = ["-XX:+UnlockExperimentalVMOptions", "-XX:+UseG1GC",
            "-XX:G1NewSizePercent=20", "-XX:G1ReservePercent=20",
            "-XX:G1HeapRegionSize=32M", "-XX:MaxGCPauseMillis=50"]
    if java.major >= 8:
        args.append("-XX:+PerfDisableSharedMem")
    if java.major >= 12:
        args += ["-XX:MinHeapFreeRatio=25", "-XX:MaxHeapFreeRatio=40"]
    return args


def _replace_placeholders(arg: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        arg = arg.replace("${" + key + "}", str(value))
    # Unknown feature placeholders must not leak to the game parser.
    import re
    arg = re.sub(r"\$\{[^}]+\}", "", arg)
    return arg


def _flatten_arguments(arguments: list[Any], features: dict[str, bool]) -> list[str]:
    result: list[str] = []
    for entry in arguments:
        if isinstance(entry, str):
            result.append(entry)
        elif isinstance(entry, dict):
            if not rules_allow(entry.get("rules"), features):
                continue
            value = entry.get("value")
            if isinstance(value, str):
                result.append(value)
            elif isinstance(value, list):
                result.extend(str(item) for item in value)
    return result


def _dedupe_game_args(args: list[str]) -> list[str]:
    """Prevent duplicate key/value flags such as --width/--height."""
    result: list[str] = []
    index = 0
    seen: dict[str, int] = {}
    while index < len(args):
        arg = args[index]
        if arg.startswith("--") and "=" not in arg and index + 1 < len(args) \
                and not args[index + 1].startswith("--"):
            if arg in seen and arg not in {"--server"}:
                # Last value wins for duplicate flags.
                result[seen[arg] + 1] = args[index + 1]
            else:
                seen[arg] = len(result)
                result.extend([arg, args[index + 1]])
            index += 2
        else:
            if arg.startswith("-X") or arg.startswith("-D"):
                index += 1
                continue
            if arg not in result or arg.startswith("--tweakClass"):
                result.append(arg)
            index += 1
    return result


class LaunchBuilder:
    def __init__(self, settings: Settings, manager: MinecraftManager) -> None:
        self.settings = settings
        self.manager = manager

    def preflight(self, version: InstalledVersion, account: dict[str, Any] | None,
                  java: JavaInfo | None) -> list[str]:
        warnings: list[str] = []
        if not version or version.broken:
            raise LaunchError(f"版本无法使用：{version.error if version else '未选择版本'}")
        if not account:
            raise LaunchError("还没有选择账号，请先在顶部账号卡中登录。")
        if not valid_player_name(str(account.get("name") or "")):
            raise LaunchError("玩家名必须是 3-16 位字母、数字或下划线。")
        if not java:
            raise LaunchError(f"没有找到满足要求的 Java {required_java_major(version.data)}+。")
        missing_java = java.major < required_java_major(version.data)
        if missing_java:
            raise LaunchError(
                f"该版本至少需要 Java {required_java_major(version.data)}，"
                f"当前选择的是 Java {java.major}。")
        client = self.manager.client_jar(version)
        if not client:
            raise LaunchError("客户端主文件缺失，请先在下载页补全版本文件。")
        meta = version.data
        libraries = collect_library_files(meta, self.manager.root)
        missing = missing_library_files(libraries)
        if missing:
            sample = "、".join(item.path.name for item in missing[:3])
            raise LaunchError(f"缺少 {len(missing)} 个支持库（{sample}…），请先补全版本文件。")
        game_dir = self.manager.game_dir(version)
        if ";" in str(game_dir) or "!" in str(game_dir):
            warnings.append("游戏路径包含 ; 或 !，可能会导致类路径异常。")
        index = meta.get("assetIndex") or {}
        index_id = str(index.get("id") or "")
        if index_id and not (self.manager.root / "assets" / "indexes" / f"{index_id}.json").exists():
            warnings.append("资源索引尚未安装，游戏内可能缺少声音与语言文件。")
        memory = self.settings.memory_mb_for(version.id)
        total = self.settings.total_ram_mb
        if memory > total * 0.82:
            warnings.append(f"分配内存 {memory} MB 已超过物理内存的 82%，可能导致系统卡顿。")
        return warnings

    def prepare_natives(self, version: InstalledVersion, *, force: bool = False) -> Path:
        target = version.path / f"{version.id}-natives"
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Read-only game libraries are common in shared installations. The
            # native DLLs can safely live in the launcher's data directory.
            safe_id = "".join(c if c.isalnum() or c in "._-" else "_" for c in version.id)
            target = data_dir() / "natives" / safe_id
            target.mkdir(parents=True, exist_ok=True)
        meta = version.data
        libraries = collect_library_files(meta, self.manager.root)
        native_libraries = [item for item in libraries if item.is_native]
        marker_path = target / ".shl-natives.json"
        expected = sorted(
            f"{item.path.name}:{item.path.stat().st_size}:{int(item.path.stat().st_mtime)}"
            for item in native_libraries if item.path.exists()
        )
        if not force and target.exists() and any(
                p.suffix.lower() in {".dll", ".so", ".dylib", ".jnilib"} for p in target.iterdir()):
            marker = read_json(marker_path, {})
            if isinstance(marker, dict) and marker.get("expected") == expected:
                return target
        if native_libraries:
            if target.exists():
                try:
                    shutil.rmtree(target)
                except OSError as exc:
                    # Another instance may still be using these DLLs.
                    if target.exists() and any(target.glob("*.dll")):
                        LOG.warning("Unable to refresh natives (%s); reusing them", exc)
                        return target
                    raise LaunchError(f"无法准备 Natives：{exc}") from exc
            target.mkdir(parents=True, exist_ok=True)
            count = extract_natives(native_libraries, target)
            write_json(marker_path, {"expected": expected, "count": count})
        else:
            target.mkdir(parents=True, exist_ok=True)
        return target

    def build(self, version: InstalledVersion, account: dict[str, Any], java: JavaInfo) -> LaunchPlan:
        warnings = self.preflight(version, account, java)
        meta = version.data
        game_dir = self.manager.game_dir(version)
        try:
            if not game_dir.exists():
                game_dir.mkdir(parents=True, exist_ok=True)
            elif not game_dir.is_dir():
                raise NotADirectoryError(str(game_dir))
        except OSError as exc:
            raise LaunchError(
                f"无法使用游戏目录：{game_dir}\n"
                f"请让启动器有写入权限，或在设置中改用官方/自定义游戏目录。\n{exc}") from exc
        natives_dir = self.prepare_natives(version)
        client_jar = self.manager.client_jar(version)
        if not client_jar:
            raise LaunchError("客户端主文件缺失。")
        libraries = collect_library_files(meta, self.manager.root)
        main_class = str(meta.get("mainClass") or "")
        # NeoForge/Forge modern installers use BootstrapLauncher and build a
        # module layer themselves. Putting the vanilla client jar on -cp there
        # creates a duplicate `minecraft` module and the game exits with a
        # module ResolutionException.
        include_client_jar = "bootstraplauncher" not in main_class.casefold()
        classpath = classpath_files(
            libraries, client_jar, include_client_jar=include_client_jar)
        native_arg = natives_dir
        if is_windows() and any(ord(c) > 127 for c in str(natives_dir)):
            native_arg = Path(short_path(natives_dir))
        cp_parts = []
        for item in _dedupe_artifacts(classpath):
            if is_windows() and any(ord(c) > 127 for c in str(item)):
                cp_parts.append(short_path(item))
            else:
                cp_parts.append(str(item))
        classpath_string = os.pathsep.join(cp_parts)
        memory = int(self.settings.memory_mb_for(version.id))
        version_window = str(self.settings.version_profile(version.id).get("window_mode") or "inherit")
        window_mode = (str(self.settings.get("window_mode") or "default")
                       if version_window in {"", "inherit"} else version_window)
        if window_mode == "windowed":
            window_mode = "default"
        maximize_window = window_mode == "maximized"
        version_profile = self.settings.version_profile(version.id)
        width = int(version_profile.get("width") or self.settings.get("window_width") or 1280)
        height = int(version_profile.get("height") or self.settings.get("window_height") or 720)
        custom_resolution = window_mode == "custom"
        features = {
            "is_demo_user": False,
            "has_custom_resolution": custom_resolution,
            "has_quick_plays_support": False,
            "is_quick_play_singleplayer": False,
            "is_quick_play_multiplayer": False,
            "is_quick_play_realms": False,
        }
        index = meta.get("assetIndex") or {}
        index_id = str(index.get("id") or "legacy")
        index_data = read_json(self.manager.root / "assets" / "indexes" / f"{index_id}.json", {})
        game_assets = self.manager.root / "assets" / "virtual" / index_id
        version_type = str(meta.get("type") or "release")
        account_token = str(account.get("access_token") or account.get("uuid") or "0")
        user_type = "msa" if account.get("type") == "microsoft" else "legacy"
        xuid = str(account.get("xuid") or "")
        client_id = str(account.get("client_id") or "")
        profile = self.settings.version_profile(version.id)
        java_override = str(profile.get("java_path") or "").strip()
        if java_override and Path(java_override) != Path(java.path):
            # The caller-created JavaInfo is authoritative; this is only a
            # consistency check for custom profiles.
            if not Path(java_override).exists():
                warnings.append("版本设置的 Java 路径已失效，已改用自动选择的 Java。")
        placeholders = {
            "auth_player_name": str(account.get("name") or "Player"),
            "version_name": version.id,
            "game_directory": str(game_dir),
            "assets_root": str(self.manager.root / "assets"),
            "game_assets": str(game_assets),
            "assets_index_name": index_id,
            "auth_uuid": str(account.get("uuid") or "").replace("-", ""),
            "auth_access_token": account_token,
            "access_token": account_token,
            "auth_session": account_token,
            "clientid": client_id,
            "auth_xuid": xuid,
            "user_type": user_type,
            "user_properties": "{}",
            "version_type": "机械冒险港",
            "natives_directory": str(native_arg),
            "library_directory": str(self.manager.root / "libraries"),
            "libraries_directory": str(self.manager.root / "libraries"),
            "classpath_separator": os.pathsep,
            "classpath": classpath_string,
            "launcher_name": APP_NAME_EN,
            "launcher_version": APP_VERSION,
            "primary_jar": str(client_jar),
            "resolution_width": str(width),
            "resolution_height": str(height),
            "quickPlayPath": "",
            "quickPlaySingleplayer": "",
            "quickPlayMultiplayer": "",
            "quickPlayRealms": "",
        }
        arguments = meta.get("arguments") or {}
        if isinstance(arguments, dict) and (arguments.get("jvm") or arguments.get("game")):
            jvm_args = _flatten_arguments(arguments.get("jvm") or [], features)
            game_args = _flatten_arguments(arguments.get("game") or [], features)
        else:
            legacy = str(meta.get("minecraftArguments") or "")
            jvm_args = [
                "-Djava.library.path=${natives_directory}",
                "-cp", "${classpath}",
                "-Dminecraft.launcher.brand=${launcher_name}",
                "-Dminecraft.launcher.version=${launcher_version}",
            ]
            game_args = split_cli_arguments(legacy)
        # Remove inherited heap/GC flags and platform-specific classpath flags.
        jvm_args = _strip_jvm_overrides([_replace_placeholders(arg, placeholders) for arg in jvm_args])
        jvm_args = _remove_classpath_args(jvm_args)
        game_args = [_replace_placeholders(arg, placeholders) for arg in game_args]

        # Memory and GC tuning.
        jvm_args += [f"-Xms{min(memory, 2048)}m", f"-Xmx{memory}m"]
        jvm_args += _gc_arguments(_resolve_gc_mode(self.settings, java), java)
        jvm_args += [
            "-Dlog4j2.formatMsgNoLookups=true",
            "-Dfile.encoding=UTF-8",
            f"-Dstdout.encoding=UTF-8", f"-Dstderr.encoding=UTF-8",
            f"-Dminecraft.launcher.brand={APP_NAME_EN}",
            f"-Dminecraft.launcher.version={APP_VERSION}",
            f"-Djava.library.path={native_arg}",
        ]
        logging_info = (meta.get("logging") or {}).get("client") or {}
        logging_arg = logging_info.get("argument")
        logging_file = (logging_info.get("file") or {})
        config_id = str(logging_file.get("id") or "")
        if isinstance(logging_arg, str) and config_id:
            config_path = self.manager.root / "assets" / "log_configs" / config_id
            if config_path.exists():
                jvm_args.append(logging_arg.replace("${path}", str(config_path)))
        custom_jvm = str(self.settings.get("jvm_args") or "")
        if custom_jvm:
            jvm_args.extend(split_cli_arguments(custom_jvm))
        version_jvm = str(profile.get("jvm_args") or "")
        if version_jvm:
            jvm_args.extend(split_cli_arguments(version_jvm))
        jvm_args.append("-cp")
        jvm_args.append(classpath_string)

        # Window size and quick join.
        if custom_resolution:
            game_args += ["--width", str(width), "--height", str(height)]
        if window_mode == "fullscreen":
            game_args.append("--fullscreen")
        server = str(profile.get("server") or self.settings.get("server_quick_join") or "").strip()
        if server:
            server_arg = self._server_arguments(version, server)
            game_args += server_arg
        custom_game = str(self.settings.get("game_args") or "")
        if custom_game:
            game_args.extend(split_cli_arguments(custom_game))
        version_game = str(profile.get("game_args") or "")
        if version_game:
            game_args.extend(split_cli_arguments(version_game))
        game_args = _dedupe_game_args([arg for arg in game_args if arg != ""])
        main_class = str(meta.get("mainClass") or "")
        if not main_class:
            raise LaunchError("版本元数据缺少 mainClass，无法启动。")
        command = [str(java.path)] + jvm_args + [main_class] + game_args
        env = os.environ.copy()
        env.setdefault("JAVA_HOME", str(java.home))
        env["SHL_LAUNCH_VERSION"] = version.id
        log_dir = game_dir / "logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"shl-{now_stamp()}-{version.id}.log"
            # Verify writability before the process starts.
            with open(log_path, "a", encoding="utf-8"):
                pass
        except OSError:
            log_dir = data_dir() / "logs" / "game"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"shl-{now_stamp()}-{version.id}.log"
        return LaunchPlan(
            version=version, command=command, cwd=game_dir, env=env, java=java,
            game_dir=game_dir, natives_dir=natives_dir, memory_mb=memory,
            log_path=log_path, maximize=maximize_window, warnings=warnings,
        )

    def _server_arguments(self, version: InstalledVersion, server: str) -> list[str]:
        # Minecraft 1.20 (23w14a) introduced Quick Play. Older versions use
        # --server/--port. Unknown options can crash the argument parser.
        new_protocol = False
        release = str(version.release_time or "")
        try:
            if release and datetime.fromisoformat(release.replace("Z", "+00:00")) >= datetime(
                    2023, 4, 4, tzinfo=timezone.utc):
                new_protocol = True
        except ValueError:
            pass
        if not new_protocol:
            try:
                parts = version.base_version.split(".")
                new_protocol = int(parts[0]) == 1 and int(parts[1]) >= 20
            except (ValueError, IndexError):
                new_protocol = False
        if new_protocol:
            return ["--quickPlayMultiplayer", server]
        host, port = server, "25565"
        if ":" in server:
            host, port = server.rsplit(":", 1)
        return ["--server", host, "--port", port]


class GameSession(QObject):
    started = pyqtSignal(int)
    logged = pyqtSignal(str)
    exited = pyqtSignal(int)
    failed = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, plan: LaunchPlan, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.plan = plan
        self.process: subprocess.Popen[bytes] | None = None
        self._reader: threading.Thread | None = None
        self._log_handle = None
        self._closed = False

    def start(self) -> None:
        flags = 0
        if is_windows():
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        try:
            self._log_handle = open(self.plan.log_path, "a", encoding="utf-8", buffering=1)
            self._log_handle.write(f"=== {APP_NAME} launch ===\n")
            self._log_handle.write(f"Time: {self.plan.created_at}\n")
            self._log_handle.write(f"Java: {self.plan.java.display} @ {self.plan.java.path}\n")
            self._log_handle.write(f"Game dir: {self.plan.game_dir}\n")
            self._log_handle.write(f"Command: {self.plan.display_command}\n\n")
            self._log_handle.flush()
            LOG.info("Launching %s: %s", self.plan.version.id, self.plan.display_command)
            self.process = subprocess.Popen(
                self.plan.command, cwd=str(self.plan.cwd), env=self.plan.env,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=flags, bufsize=0,
            )
        except (OSError, ValueError) as exc:
            self._write_system(f"启动失败：{exc}")
            self.failed.emit(f"无法启动 Java 进程：{exc}")
            self.finished.emit()
            self._close_log()
            return
        self.started.emit(self.process.pid)
        self._reader = threading.Thread(target=self._read_output, name="shl-game-log", daemon=True)
        self._reader.start()
        if self.plan.maximize and is_windows():
            threading.Thread(target=self._maximize_when_ready, name="shl-game-maximize",
                             daemon=True).start()

    def _maximize_when_ready(self) -> None:
        """Maximize the Minecraft window once its Java process creates it."""
        try:
            import ctypes
            import time
            from ctypes import wintypes
        except Exception:
            return
        user32 = ctypes.windll.user32
        deadline = time.time() + 90
        while time.time() < deadline:
            process = self.process
            if process is None or process.poll() is not None:
                return
            found: list[int] = []

            @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def callback(hwnd, _lparam):
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value != process.pid or not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                title = buffer.value
                if "minecraft" in title.lower() or not found:
                    found.append(int(hwnd))
                    if "minecraft" in title.lower():
                        return False
                return True

            try:
                user32.EnumWindows(callback, 0)
            except Exception:
                return
            if found:
                user32.ShowWindow(found[0], 3)  # SW_MAXIMIZE
                self._write_system("窗口已最大化（有边框）。")
                return
            time.sleep(0.5)

    def _read_output(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        event_level = "INFO"
        event_thread = ""
        event_logger = ""
        try:
            while True:
                line = self.process.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not text:
                    continue
                # Minecraft's default log4j configuration writes XML events to
                # stdout. Turn those into familiar, readable log lines while
                # preserving ordinary Java/exception output.
                import re
                event = re.search(
                    r'<log4j:Event\s+logger="([^"]*)"[^>]*\blevel="([^"]*)"[^>]*\bthread="([^"]*)"',
                    text)
                if event:
                    event_logger, event_level, event_thread = event.groups()
                    continue
                if "<log4j:Message" in text and "<![CDATA[" in text:
                    match = re.search(r"<!\[CDATA\[(.*?)\]\]>", text, re.S)
                    if match:
                        message = match.group(1)
                        prefix = f"[{event_level}/{event_thread}] " if event_thread else f"[{event_level}] "
                        formatted = prefix + message
                        self._write_system(formatted)
                        self.logged.emit(formatted)
                    continue
                if text.strip() in {"</log4j:Event>", "</log4j:EventSet>", "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"}:
                    continue
                if "<log4j:" in text:
                    continue
                self._write_system(text)
                self.logged.emit(text)
        except Exception as exc:
            self._write_system(f"读取游戏日志失败：{exc}")
        finally:
            code = self.process.wait() if self.process else -1
            self._write_system(f"\n=== Minecraft exited with code {code} ===\n")
            self._close_log()
            self.exited.emit(code)
            self.finished.emit()

    def _write_system(self, text: str) -> None:
        if self._log_handle:
            try:
                self._log_handle.write(text + ("\n" if not text.endswith("\n") else ""))
                self._log_handle.flush()
            except (OSError, ValueError):
                pass

    def _close_log(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._log_handle:
            try:
                self._log_handle.close()
            except OSError:
                pass
        self._log_handle = None

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def terminate(self, force: bool = False) -> None:
        if not self.process or self.process.poll() is not None:
            return
        try:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
        except OSError:
            pass

    def wait(self, timeout: float | None = None) -> int | None:
        if not self.process:
            return None
        return self.process.wait(timeout=timeout)
