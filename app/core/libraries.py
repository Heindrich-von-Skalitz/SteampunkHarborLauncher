from __future__ import annotations

import logging
import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..utils import LauncherError, is_windows, rules_allow

LOG = logging.getLogger("shl.libraries")

DEFAULT_LIBRARY_BASE = "https://libraries.minecraft.net/"
NATIVE_OS_KEY = "windows" if is_windows() else ("osx" if os.sys.platform == "darwin" else "linux")


@dataclass
class LibraryFile:
    name: str
    path: Path
    urls: list[str] = field(default_factory=list)
    sha1: str = ""
    size: int = 0
    is_native: bool = False
    excludes: list[str] = field(default_factory=list)

    @property
    def filename(self) -> str:
        return self.path.name


def _maven_path(name: str) -> Path | None:
    """Convert group:artifact:version[:classifier][@ext] to a repository path."""
    if not name or ":" not in name:
        return None
    ext = "jar"
    if "@" in name:
        name, ext = name.rsplit("@", 1)
    parts = name.split(":")
    if len(parts) < 3:
        return None
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = parts[3] if len(parts) > 3 else ""
    filename = f"{artifact}-{version}"
    if classifier:
        filename += f"-{classifier}"
    filename += f".{ext or 'jar'}"
    return Path(*group.split(".")) / artifact / version / filename


def _native_classifier(lib: dict[str, Any]) -> str:
    natives = lib.get("natives") or {}
    if not isinstance(natives, dict):
        return ""
    raw = str(natives.get(NATIVE_OS_KEY) or "")
    if not raw:
        # Some legacy metadata uses windows-32 / windows-64 keys.
        for key in ("windows-64", "windows-32"):
            raw = str(natives.get(key) or "")
            if raw:
                break
    if not raw:
        return ""
    arch = "64" if os.environ.get("PROCESSOR_ARCHITECTURE", "").upper() in {"AMD64", "ARM64"} else "32"
    return raw.replace("${arch}", arch)


def _extract_excludes(lib: dict[str, Any]) -> list[str]:
    extract = lib.get("extract") or {}
    exclude = extract.get("exclude") or []
    return [str(x) for x in exclude] if isinstance(exclude, list) else []


def collect_library_files(meta: dict[str, Any], minecraft_root: Path,
                          features: dict[str, bool] | None = None) -> list[LibraryFile]:
    root = Path(minecraft_root)
    libraries_root = root / "libraries"
    result: dict[str, LibraryFile] = {}
    for lib in meta.get("libraries", []):
        if not isinstance(lib, dict):
            continue
        rules = lib.get("rules")
        if not rules_allow(rules, features):
            continue
        name = str(lib.get("name") or "")
        downloads = lib.get("downloads") or {}
        artifact = downloads.get("artifact") or {}
        classifier_key = _native_classifier(lib) if is_windows() else ""
        classifiers = downloads.get("classifiers") or {}
        native_data = {}
        if classifier_key:
            native_data = classifiers.get(classifier_key) or {}
            if not native_data:
                native_data = classifiers.get(f"natives-{NATIVE_OS_KEY}") or {}
            if not native_data and any(
                    name.endswith(f":{classifier_key}") for _ in [0]):
                native_data = artifact
        artifact_path = artifact.get("path")
        artifact_url = artifact.get("url") or ""
        # A number of legacy libraries (notably lwjgl-platform and
        # jinput-platform) have only classifier jars and no base artifact. Do
        # not invent a 404 base artifact from their Maven name.
        has_download_section = isinstance(downloads, dict) and bool(
            downloads.get("artifact") is not None or downloads.get("classifiers"))
        if artifact_path:
            target = libraries_root / artifact_path
            item = LibraryFile(
                name=name, path=target, urls=[artifact_url] if artifact_url else [],
                sha1=str(artifact.get("sha1") or ""), size=int(artifact.get("size") or 0),
                is_native=False, excludes=_extract_excludes(lib),
            )
            result[str(target).casefold()] = item
        elif name and not has_download_section:
            maven_path = _maven_path(name)
            if maven_path:
                target = libraries_root / maven_path
                base = str(lib.get("url") or DEFAULT_LIBRARY_BASE)
                if not base.endswith("/"):
                    base += "/"
                result[str(target).casefold()] = LibraryFile(
                    name=name, path=target, urls=[base + maven_path.as_posix()],
                    sha1=str(lib.get("sha1") or ""), size=int(lib.get("size") or 0),
                    is_native=False, excludes=_extract_excludes(lib),
                )
        if native_data:
            native_path = native_data.get("path")
            if not native_path:
                # Derive native jar path from base artifact data.
                parts = name.split(":")
                if len(parts) >= 3:
                    ext = "jar"
                    native_name = f"{parts[0]}:{parts[1]}:{parts[2]}:{classifier_key}@{ext}"
                    native_path_raw = _maven_path(native_name)
                    native_path = native_path_raw.as_posix() if native_path_raw else ""
            if native_path:
                target = libraries_root / native_path
                base = str(lib.get("url") or DEFAULT_LIBRARY_BASE)
                if not base.endswith("/"):
                    base += "/"
                native_url = native_data.get("url") or (base + native_path)
                result[str(target).casefold()] = LibraryFile(
                    name=name + " (natives)", path=target, urls=[str(native_url)],
                    sha1=str(native_data.get("sha1") or ""),
                    size=int(native_data.get("size") or 0), is_native=True,
                    excludes=_extract_excludes(lib),
                )
    return list(result.values())


def classpath_files(files: Iterable[LibraryFile], client_jar: Path | None = None,
                    *, include_client_jar: bool = True) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    # Vanilla/Fabric/Quilt need the client jar on the classpath. Modern
    # Forge/NeoForge use BootstrapLauncher module layers and must NOT get the
    # vanilla jar there: it becomes automatic module `_1._21._1` and exports
    # the same packages as NeoForge's `minecraft` module, which triggers
    # `java.lang.module.ResolutionException`.
    for item in files:
        if item.is_native:
            continue
        key = str(item.path).casefold()
        if key not in seen:
            seen.add(key)
            paths.append(item.path)
    if include_client_jar and client_jar and client_jar.exists():
        key = str(client_jar).casefold()
        if key not in seen:
            paths.append(client_jar)
    return paths


def extract_natives(files: Iterable[LibraryFile], natives_dir: Path) -> int:
    natives_dir = Path(natives_dir)
    natives_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for item in files:
        if not item.is_native or not item.path.exists():
            continue
        try:
            with zipfile.ZipFile(item.path) as archive:
                for member in archive.infolist():
                    if member.is_dir():
                        continue
                    filename = member.filename
                    if any(filename.startswith(ex) for ex in item.excludes):
                        continue
                    if "/" in filename or "\\" in filename:
                        # Natives normally sit at the root. Skip META-INF and
                        # nested service files that cannot be loaded by LWJGL.
                        if not filename.lower().endswith((".dll", ".so", ".dylib", ".jnilib")):
                            continue
                    suffix = Path(filename).suffix.lower()
                    if suffix not in {".dll", ".so", ".dylib", ".jnilib"}:
                        continue
                    target = natives_dir / Path(filename).name
                    with archive.open(member) as source, open(target, "wb") as dest:
                        while chunk := source.read(1024 * 256):
                            dest.write(chunk)
                    count += 1
        except (zipfile.BadZipFile, OSError) as exc:
            raise LauncherError(f"解压 Natives 失败（{item.filename}）：{exc}") from exc
    if not count and files:
        raise LauncherError("没有从依赖库中解压出任何 Natives 文件，版本可能不完整。")
    return count


def missing_library_files(files: Iterable[LibraryFile]) -> list[LibraryFile]:
    missing: list[LibraryFile] = []
    for item in files:
        if not item.path.exists() or item.path.stat().st_size == 0:
            missing.append(item)
    return missing


def parse_maven_name(name: str) -> dict[str, str]:
    parts = name.split(":")
    return {
        "group": parts[0] if len(parts) > 0 else "",
        "artifact": parts[1] if len(parts) > 1 else "",
        "version": parts[2] if len(parts) > 2 else "",
        "classifier": parts[3] if len(parts) > 3 else "",
    }


def native_library_names(files: Iterable[LibraryFile]) -> list[str]:
    return [item.name for item in files if item.is_native]
