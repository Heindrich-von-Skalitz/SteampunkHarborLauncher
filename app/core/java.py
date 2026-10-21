from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from ..constants import ADOPTIUM_ASSETS
from ..paths import cache_dir, data_dir, runtimes_dir
from ..storage import Settings
from ..utils import (
    CancelledError, LauncherError, download_file, format_size, is_windows, now_stamp,
    parse_java_version, read_json, request_json, run_hidden, safe_extract_zip, write_json,
)

LOG = logging.getLogger("shl.java")


@dataclass
class JavaInfo:
    path: Path
    version: str = "未知"
    major: int = 0
    vendor: str = "Java"
    arch: str = "64 位"
    source: str = "自动检测"
    home: Path = field(default_factory=Path)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        if not self.home or self.home == Path("."):
            self.home = self.path.parent.parent

    @property
    def display(self) -> str:
        return f"Java {self.version} · {self.vendor}"

    @property
    def short(self) -> str:
        return f"Java {self.major}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path), "version": self.version, "major": self.major,
            "vendor": self.vendor, "arch": self.arch, "source": self.source,
            "home": str(self.home),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "JavaInfo | None":
        try:
            info = cls(
                path=Path(value["path"]), version=str(value.get("version", "未知")),
                major=int(value.get("major", 0)), vendor=str(value.get("vendor", "Java")),
                arch=str(value.get("arch", "64 位")), source=str(value.get("source", "自动检测")),
                home=Path(value.get("home") or Path(value["path"]).parent.parent),
            )
            return info if info.path.exists() else None
        except (KeyError, TypeError, ValueError):
            return None

    @property
    def identity(self) -> str:
        return str(self.path).casefold()


def _title_vendor(output: str, home: str = "") -> str:
    text = f"{output} {home}".lower()
    for needle, name in (
        ("temurin", "Eclipse Temurin"), ("adoptium", "Eclipse Temurin"),
        ("microsoft", "Microsoft OpenJDK"), ("corretto", "Amazon Corretto"),
        ("zulu", "Azul Zulu"), ("graal", "GraalVM"), ("semeru", "IBM Semeru"),
        ("ibm", "IBM J9"), ("oracle", "Oracle JDK"), ("jbr", "JetBrains Runtime"),
        ("dragonwell", "Alibaba Dragonwell"), ("openjdk", "OpenJDK"),
    ):
        if needle in text:
            return name
    return "Java"


class JavaManager:
    CACHE_FILE = cache_dir() / "java_cache.json"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cached: list[JavaInfo] = []
        self._discovered: list[JavaInfo] = []
        self._load_cache()

    # ------------------------------------------------------------------ cache
    def _load_cache(self) -> None:
        raw = read_json(self.CACHE_FILE, [])
        if isinstance(raw, dict):
            raw = raw.get("javas", [])
        for item in raw or []:
            info = JavaInfo.from_dict(item) if isinstance(item, dict) else None
            if info:
                self._cached.append(info)
        self._discovered = list(self._cached)

    def _save_cache(self) -> None:
        # The cache is an optimization only. A read-only AppData, antivirus
        # lock or portable setup must never turn Java discovery into an
        # unhandled background exception.
        try:
            write_json(self.CACHE_FILE, {
                "updated": now_stamp(),
                "javas": [item.to_dict() for item in self._discovered],
            })
        except (OSError, ValueError) as exc:
            LOG.warning("Could not write Java cache %s: %s", self.CACHE_FILE, exc)

    def cached(self) -> list[JavaInfo]:
        return sorted(self._discovered, key=lambda item: (-item.major, item.vendor.lower()))

    def discover(self) -> list[JavaInfo]:
        """Perform a reasonably bounded scan of common Java locations."""
        results: dict[str, JavaInfo] = {}
        for info in self._discovered:
            if info.path.exists():
                results[info.identity] = info
        for candidate, source in self._candidate_paths():
            key = str(candidate.resolve()).casefold()
            if key in results:
                continue
            if not candidate.exists():
                continue
            probed = self.probe(candidate, source=source)
            if probed:
                results[probed.identity] = probed
                self._discovered = [x for x in self._discovered if x.identity != probed.identity]
                self._discovered.append(probed)
        ordered = sorted(results.values(), key=lambda item: (-item.major, item.vendor.lower()))
        self._discovered = ordered
        self._save_cache()
        return ordered

    def rescan(self) -> list[JavaInfo]:
        return self.discover()

    def probe(self, java_path: Path, *, source: str = "手动导入") -> JavaInfo | None:
        java_path = Path(java_path)
        exe = java_path
        if exe.is_dir():
            possibilities = [exe / "bin" / "java.exe", exe / "java.exe", exe / "bin" / "java"]
            exe = next((p for p in possibilities if p.exists()), exe / "java.exe")
        if not exe.exists():
            # A user may hand us java.exe even on non-Windows systems.
            if java_path.name in {"java.exe", "java"} and java_path.exists():
                exe = java_path
            else:
                return None
        if not exe.is_file():
            return None
        try:
            proc = run_hidden([str(exe), "-version"], timeout=7)
            output = proc.stdout or ""
        except (OSError, subprocess.TimeoutExpired):
            return None
        version, major = parse_java_version(output)
        if major <= 0:
            return None
        arch = "64 位" if "64-bit" in output or "64-Bit" in output or "amd64" in output.lower() else "32 位"
        vendor = _title_vendor(output, str(exe.parent.parent))
        return JavaInfo(
            path=exe, version=version, major=major, vendor=vendor, arch=arch,
            source=source, home=exe.parent.parent,
        )

    def add_manual(self, java_path: Path | str) -> JavaInfo:
        info = self.probe(Path(java_path), source="手动添加")
        if not info:
            raise LauncherError("这个文件不是可用的 Java，请选择 java.exe 或 bin 文件夹。")
        self._discovered = [x for x in self._discovered if x.identity != info.identity]
        self._discovered.insert(0, info)
        self._save_cache()
        return info

    def remove(self, info: JavaInfo) -> None:
        self._discovered = [x for x in self._discovered if x.identity != info.identity]
        self._save_cache()

    # ------------------------------------------------------------------ candidates
    def _candidate_paths(self) -> Iterable[tuple[Path, str]]:
        """Yield (java executable, source) pairs without probing yet."""
        seen: set[str] = set()

        def add(path: Path | str | None, source: str) -> Iterable[tuple[Path, str]]:
            if not path:
                return
            p = Path(path).expanduser()
            for exe in self._java_exe_candidates(p):
                key = str(exe.resolve()).casefold() if exe.exists() else str(exe).casefold()
                if key in seen or not exe.exists():
                    continue
                seen.add(key)
                yield exe, source

        # A manually configured Java always comes first.
        configured = self.settings.get("java_path") or ""
        if configured:
            yield from add(configured, "手动指定")

        env_home = os.environ.get("JAVA_HOME") or os.environ.get("JDK_HOME")
        if env_home:
            yield from add(env_home, "JAVA_HOME")

        for path_entry in (os.environ.get("PATH") or "").split(os.pathsep):
            if not path_entry:
                continue
            exe = Path(path_entry) / ("java.exe" if is_windows() else "java")
            if exe.exists():
                key = str(exe.resolve()).casefold()
                if key not in seen:
                    seen.add(key)
                    yield exe, "系统 PATH"

        candidates: list[tuple[Path, str]] = []
        game_dir = self.settings.minecraft_dir
        candidates += [
            (game_dir / "runtime", "官方启动器运行时"),
            (Path(os.environ.get("APPDATA", "")) / ".minecraft" / "runtime", "官方启动器运行时"),
            (data_dir() / "runtimes", "启动器下载的 Java"),
            (Path.home() / ".jdks", "本地 JDK"),
            (Path.home() / "scoop" / "apps", "Scoop"),
            (Path.home() / ".gradle" / "jdks", "Gradle JDK"),
        ]
        if is_windows():
            local = Path(os.environ.get("LOCALAPPDATA", ""))
            program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            program_files_x86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
            candidates += [
                (program_files / "Java", "系统安装"),
                (program_files_x86 / "Java", "系统安装"),
                (program_files / "Eclipse Adoptium", "系统安装"),
                (program_files / "Microsoft", "系统安装"),
                (program_files / "Amazon Corretto", "系统安装"),
                (program_files / "Zulu", "系统安装"),
                (program_files / "BellSoft", "系统安装"),
                (program_files / "GraalVM", "系统安装"),
                (program_files / "Semeru", "系统安装"),
                (local / "Programs" / "Eclipse Adoptium", "系统安装"),
                (local / "Programs" / "Microsoft", "系统安装"),
                (local / "Programs" / "Zulu", "系统安装"),
                (local / "Programs" / "Amazon Corretto", "系统安装"),
                (local / "Packages", "微软商店运行时"),
            ]
            # Add all registry-known installations.
            for root in self._registry_java_homes():
                candidates.append((root, "注册表"))

        # Bounded walk for Java homes.
        for root, source in candidates:
            if not root or not root.exists():
                continue
            if root.name == "Packages":
                for pattern in (
                    "Microsoft.4297127D64EC6_*/LocalCache/Local/runtime/*/windows-*/**/bin/java.exe",
                    "Microsoft.4297127D64EC6_*/LocalCache/Local/runtime/*/bin/java.exe",
                ):
                    for exe in root.glob(pattern):
                        key = str(exe.resolve()).casefold()
                        if key not in seen:
                            seen.add(key)
                            yield exe, source
                continue
            for exe in self._walk_java(root, max_depth=5):
                key = str(exe.resolve()).casefold()
                if key not in seen:
                    seen.add(key)
                    yield exe, source

    @staticmethod
    def _java_exe_candidates(root: Path) -> list[Path]:
        if root.is_file():
            return [root]
        if root.name in {"bin", "jre", "jdk"}:
            return [root / "java.exe"] if is_windows() else [root / "java"]
        return [
            root / "bin" / "java.exe", root / "bin" / "java",
            root / "java.exe", root / "java",
        ]

    @staticmethod
    def _walk_java(root: Path, max_depth: int = 5) -> Iterable[Path]:
        root = Path(root)
        exe_name = "java.exe" if is_windows() else "java"
        if root.name == "bin" and (root / exe_name).exists():
            yield root / exe_name
            return
        root_depth = len(root.parts)
        skip = {"node_modules", "cache", "downloads", "temp", "logs", "assets", "versions",
                ".git", "site-packages", "windows", "winsxs"}
        for current, dirs, files in os.walk(root):
            current_path = Path(current)
            depth = len(current_path.parts) - root_depth
            dirs[:] = [d for d in dirs if d.lower() not in skip and not d.startswith(".")]
            if depth >= max_depth:
                dirs[:] = []
                continue
            if current_path.name == "bin" and exe_name in files:
                yield current_path / exe_name
                dirs[:] = []
        # A few known structures do not have a "bin" at a uniform depth.
        for pattern in ("*/bin/" + exe_name, "*/*/bin/" + exe_name, "*/*/*/bin/" + exe_name):
            for exe in root.glob(pattern):
                if exe.is_file():
                    yield exe

    @staticmethod
    def _registry_java_homes() -> list[Path]:
        if not is_windows():
            return []
        homes: list[Path] = []
        try:
            import winreg
        except ImportError:
            return homes
        roots = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\JavaSoft\Java Runtime Environment"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\JavaSoft\Java Development Kit"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\JavaSoft\Java Runtime Environment"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\JavaSoft\Java Development Kit"),
        ]
        for hive, key in roots:
            try:
                with winreg.OpenKey(hive, key) as root_key:
                    index = 0
                    while True:
                        try:
                            sub = winreg.EnumKey(root_key, index)
                        except OSError:
                            break
                        index += 1
                        try:
                            with winreg.OpenKey(root_key, sub) as sub_key:
                                home, _ = winreg.QueryValueEx(sub_key, "JavaHome")
                                if home:
                                    homes.append(Path(home))
                        except OSError:
                            continue
            except OSError:
                continue
        return homes

    # ------------------------------------------------------------------ choosing
    def configured(self) -> JavaInfo | None:
        path = self.settings.get("java_path") or ""
        if not path:
            return None
        return self.probe(Path(path), source="手动指定")

    def choose(self, required_major: int, *, explicit_path: str = "") -> JavaInfo | None:
        if explicit_path:
            info = self.probe(Path(explicit_path), source="版本设置")
            if info and info.major >= required_major:
                return info
            if info:
                raise LauncherError(
                    f"版本需要 Java {required_major}+，但指定的 Java 为 {info.major}。")
        configured = self.configured()
        if configured and configured.major >= required_major:
            return configured
        candidates = [item for item in self.cached() if item.major >= required_major]
        if not candidates:
            # The cache could be stale or Java was installed while running.
            try:
                candidates = [item for item in self.discover() if item.major >= required_major]
            except Exception as exc:
                LOG.warning("Java rescan failed: %s", exc)
        if not candidates:
            return None
        # Lowest compatible major is usually the most stable for older versions;
        # on ties, prefer a launcher-downloaded/official runtime.
        def rank(item: JavaInfo) -> tuple:
            source_rank = 0 if "下载" in item.source or "运行时" in item.source else 1
            return (item.major, source_rank, item.vendor)
        return sorted(candidates, key=rank)[0]

    # ------------------------------------------------------------------ download
    def download_temurin(self, major_version: int = 21,
                         progress: Callable[[int, str], None] | None = None,
                         cancel: Callable[[], bool] | None = None) -> JavaInfo:
        def emit(percent: int, text: str) -> None:
            if progress:
                progress(max(0, min(100, percent)), text)

        major_version = 25 if int(major_version) >= 25 else 21
        emit(3, f"读取 Eclipse Temurin Java {major_version} 发布信息…")
        url = (f"https://api.adoptium.net/v3/assets/latest/{major_version}/hotspot"
               "?architecture=x64&image_type=jre&os=windows&vendor=eclipse")
        assets = request_json(
            url, timeout=30,
            headers={"Accept": "application/json", "User-Agent": "SteampunkHarborLauncher/1.0"},
            context="获取 Java 下载信息",
        )
        if not isinstance(assets, list) or not assets:
            raise LauncherError(f"没有找到可用的 Temurin Java {major_version} 安装包。")
        binary = assets[0].get("binary") or {}
        package = binary.get("package") or {}
        url = package.get("link")
        checksum = package.get("checksum")
        name = package.get("name") or f"temurin-jre{major_version}.zip"
        size = package.get("size") or 0
        if not url:
            raise LauncherError("Temurin 发布信息缺少下载地址。")
        target_dir = runtimes_dir() / f"temurin-{major_version}"
        target_dir.mkdir(parents=True, exist_ok=True)
        archive_path = cache_dir() / str(name)
        emit(8, f"下载 Java {major_version}（{format_size(size)}）…")

        def dl_progress(done: int, total: int) -> None:
            total = total or size or 1
            emit(8 + int(done / max(1, total) * 75),
                 f"下载 Java {major_version} · {format_size(done)} / {format_size(total)}")

        download_file(
            url, archive_path, sha256=checksum, size=size, progress=dl_progress,
            cancel=cancel, context=f"Java {major_version} 运行时",
        )
        emit(86, "解压 Java 运行时…")
        try:
            with zipfile.ZipFile(archive_path) as archive:
                safe_extract_zip(archive, target_dir)
        except (zipfile.BadZipFile, OSError) as exc:
            raise LauncherError(f"Java 运行时解压失败：{exc}") from exc
        candidates = list(target_dir.rglob("java.exe"))
        if not candidates:
            candidates = list(target_dir.rglob("bin/java"))
        if not candidates:
            raise LauncherError("解压完成，但没有找到 Java 可执行文件。")
        emit(96, "验证 Java 运行时…")
        info = self.probe(candidates[0], source="启动器下载的 Java")
        if not info:
            raise LauncherError("下载的 Java 无法运行。")
        self._discovered = [x for x in self._discovered if x.identity != info.identity]
        self._discovered.insert(0, info)
        self._save_cache()
        emit(100, f"Java {info.major} 已就绪")
        return info
