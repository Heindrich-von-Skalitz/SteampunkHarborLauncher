from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import struct
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..paths import cache_dir, data_dir
from ..utils import LauncherError, format_size, now_stamp, unique_path
from .minecraft import InstalledVersion, MinecraftManager

try:
    import tomllib
except ImportError:  # pragma: no cover
    tomllib = None  # type: ignore

LOG = logging.getLogger("shl.mods")

LOADER_LABELS = {"fabric": "Fabric", "quilt": "Quilt", "forge": "Forge",
                 "neoforge": "NeoForge", "liteloader": "LiteLoader", "unknown": "未知"}


@dataclass
class ModInfo:
    path: Path
    filename: str
    enabled: bool
    name: str
    mod_id: str = ""
    version: str = ""
    authors: list[str] = field(default_factory=list)
    description: str = ""
    loader: str = "unknown"
    required_loaders: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    icon_path: str = ""
    size: int = 0
    sha1: str = ""
    parse_error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    incompatible: bool = False
    issues: list[str] = field(default_factory=list)

    @property
    def author_label(self) -> str:
        return "、".join(self.authors[:3]) if self.authors else "未知作者"

    @property
    def display_size(self) -> str:
        return format_size(self.size)

    @property
    def loader_label(self) -> str:
        return LOADER_LABELS.get(self.loader, self.loader.title())


@dataclass
class ResourceInfo:
    path: Path
    name: str
    kind: str
    size: int = 0
    description: str = ""
    icon_path: str = ""
    modified: float = 0.0
    enabled: bool = False


@dataclass
class SaveInfo:
    path: Path
    name: str
    modified: float
    icon_path: str = ""
    game_version: str = ""
    size: int = 0


class ModManager:
    def __init__(self, settings, minecraft: MinecraftManager) -> None:
        self.settings = settings
        self.minecraft = minecraft
        self._icon_dir = cache_dir() / "mod_icons"
        self._icon_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ scanning
    def scan(self, version: InstalledVersion | None) -> tuple[list[ModInfo], list[str]]:
        if not version:
            return [], []
        mods_dir = self.minecraft.mods_dir(version)
        if not mods_dir.exists():
            return [], []
        files = sorted(
            [p for p in mods_dir.iterdir()
             if p.is_file() and (p.name.lower().endswith(".jar")
                                 or p.name.lower().endswith(".jar.disabled")
                                 or p.name.lower().endswith(".zip"))],
            key=lambda p: p.name.lower(),
        )
        mods: list[ModInfo] = []
        for file in files:
            mods.append(self.parse_mod(file, version))
        issues = self._detect_issues(mods, version)
        return mods, issues

    def _detect_issues(self, mods: list[ModInfo], version: InstalledVersion) -> list[str]:
        issues: list[str] = []
        ids: dict[str, list[ModInfo]] = {}
        loaders = set(version.loaders)
        for mod in mods:
            if mod.mod_id:
                ids.setdefault(mod.mod_id.lower(), []).append(mod)
            if not mod.enabled:
                continue
            if mod.incompatible:
                issues.append(
                    f"{mod.name} 需要 {', '.join(LOADER_LABELS.get(x, x) for x in mod.required_loaders)}，"
                    f"当前版本为 {version.loader_label}。"
                )
        for mod_id, group in ids.items():
            enabled = [m for m in group if m.enabled]
            if len(enabled) > 1:
                names = "、".join(m.name for m in enabled[:3])
                issues.append(f"检测到重复的 Mod ID「{mod_id}」：{names}。")
        if version.loaders and "fabric" in loaders:
            wrong = [m for m in mods if m.enabled and m.loader == "forge"]
            if wrong:
                issues.append(f"有 {len(wrong)} 个 Forge Mod 被放进了 Fabric 版本，游戏可能无法启动。")
        return issues

    def parse_mod(self, file: Path, version: InstalledVersion | None = None) -> ModInfo:
        enabled = not file.name.lower().endswith(".disabled")
        info = ModInfo(
            path=file, filename=file.name, enabled=enabled,
            name=file.name.replace(".jar.disabled", "").replace(".jar", "").replace(".zip", ""),
        )
        try:
            stat = file.stat()
            info.size = stat.st_size
            # A cheap identity for icon caching. We intentionally do not hash
            # every jar on each refresh; large modpacks can contain hundreds of
            # megabytes and the UI must stay responsive.
            info.sha1 = hashlib.sha1(
                f"{file.name}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")).hexdigest()
        except OSError:
            info.parse_error = "文件无法读取"
            return info
        try:
            with zipfile.ZipFile(file) as archive:
                names = set(archive.namelist())
                if "fabric.mod.json" in names:
                    self._parse_fabric(info, archive, "fabric")
                elif "quilt.mod.json" in names:
                    self._parse_quilt(info, archive)
                elif "META-INF/neoforge.mods.toml" in names:
                    self._parse_toml(info, archive, "META-INF/neoforge.mods.toml", "neoforge")
                elif "META-INF/mods.toml" in names:
                    self._parse_toml(info, archive, "META-INF/mods.toml", "forge")
                elif "mcmod.info" in names:
                    self._parse_mcmod(info, archive)
                else:
                    self._parse_manifest(info, archive)
        except (zipfile.BadZipFile, OSError, KeyError, ValueError) as exc:
            info.parse_error = f"无法读取 Mod 元数据：{exc}"
            LOG.info("Mod metadata parse failed for %s: %s", file, exc)
        if version is not None:
            info.incompatible = self.is_incompatible(info, version)
        return info

    def is_incompatible(self, info: ModInfo, version: InstalledVersion) -> bool:
        if not info.required_loaders:
            return False
        if not version.loaders:
            return True
        active = set(version.loaders)
        for loader in info.required_loaders:
            if loader == "fabric" and active & {"fabric", "quilt"}:
                return False
            if loader in active:
                return False
            if loader == "forge" and "neoforge" in active:
                # 1.20.1 NeoForge was a Forge fork and can load many Forge mods;
                # elsewhere the APIs are incompatible. Keep the warning conservative.
                return version.base_version != "1.20.1"
        return True

    def _read_json(self, archive: zipfile.ZipFile, name: str) -> dict[str, Any]:
        raw = archive.read(name)
        for encoding in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                value = json.loads(raw.decode(encoding))
                return value if isinstance(value, dict) else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
        return {}

    def _parse_fabric(self, info: ModInfo, archive: zipfile.ZipFile, loader: str) -> None:
        data = self._read_json(archive, "fabric.mod.json")
        info.metadata = data
        info.loader = loader
        info.required_loaders = [loader]
        info.name = str(data.get("name") or info.name)
        info.mod_id = str(data.get("id") or "")
        info.version = str((data.get("version") if not isinstance(data.get("version"), dict)
                            else data["version"].get("$comment")) or data.get("version") or "")
        info.description = self._flatten_text(data.get("description"))
        authors = data.get("authors") or []
        if isinstance(authors, str):
            authors = [authors]
        info.authors = [str(a.get("name") if isinstance(a, dict) else a) for a in authors]
        depends = data.get("depends") or {}
        if isinstance(depends, dict):
            info.dependencies = [str(key) for key in depends.keys() if key not in {"minecraft", "fabricloader", "java"}]
        icon = data.get("icon")
        if isinstance(icon, dict):
            icon = next(iter(icon.values()), "")
        if isinstance(icon, str) and icon in archive.namelist():
            info.icon_path = self._extract_icon(archive, icon, info.sha1)
        if data.get("environment") == "server":
            info.issues.append("这是服务端专用 Mod。")

    def _parse_quilt(self, info: ModInfo, archive: zipfile.ZipFile) -> None:
        data = self._read_json(archive, "quilt.mod.json")
        info.metadata = data
        info.loader = "quilt"
        info.required_loaders = ["quilt"]
        quilt_loader = data.get("quilt_loader") or {}
        info.mod_id = str(quilt_loader.get("id") or "")
        info.version = str(quilt_loader.get("version") or "")
        metadata = quilt_loader.get("metadata") or {}
        info.name = str(metadata.get("name") or info.mod_id or info.name)
        info.description = self._flatten_text(metadata.get("description"))
        contributors = metadata.get("contributors") or {}
        if isinstance(contributors, dict):
            info.authors = [str(key) for key in contributors.keys()]
        icon = metadata.get("icon")
        if isinstance(icon, str) and icon in archive.namelist():
            info.icon_path = self._extract_icon(archive, icon, info.sha1)

    def _parse_toml(self, info: ModInfo, archive: zipfile.ZipFile, member: str,
                    loader: str) -> None:
        info.loader = loader
        info.required_loaders = [loader]
        if tomllib is None:
            info.parse_error = "当前 Python 缺少 tomllib"
            return
        raw = archive.read(member)
        try:
            data = tomllib.loads(raw.decode("utf-8-sig", errors="replace"))
        except Exception as exc:
            info.parse_error = f"Mods TOML 解析失败：{exc}"
            return
        info.metadata = data
        mods = data.get("mods") or []
        if isinstance(mods, list) and mods and isinstance(mods[0], dict):
            first = mods[0]
            info.mod_id = str(first.get("modId") or "")
            info.version = str(first.get("version") or "")
            info.name = str(first.get("displayName") or info.mod_id or info.name)
            info.authors = str(first.get("authors") or "").split(",")
            info.authors = [a.strip() for a in info.authors if a.strip()]
            info.description = str(first.get("description") or "")
            logo = str(first.get("logoFile") or "")
            if logo and logo in archive.namelist():
                info.icon_path = self._extract_icon(archive, logo, info.sha1)
        deps = data.get("dependencies") or {}
        if isinstance(deps, dict):
            for key, entries in deps.items():
                if key.lower() in {"minecraft", "forge", "neoforge", "fabricloader", "quilt_loader"}:
                    continue
                if isinstance(entries, list):
                    info.dependencies.extend(str(e.get("modId")) for e in entries
                                             if isinstance(e, dict) and e.get("modId"))
        info.issues.append("该 Mod 在启动器中仅作基础兼容性检查，复杂依赖请以游戏日志为准。")

    def _parse_mcmod(self, info: ModInfo, archive: zipfile.ZipFile) -> None:
        try:
            raw = archive.read("mcmod.info").decode("utf-8-sig", errors="replace")
            data = json.loads(raw)
        except Exception:
            return
        first = data[0] if isinstance(data, list) and data else {}
        if not isinstance(first, dict):
            return
        info.loader = "forge"
        info.required_loaders = ["forge"]
        info.mod_id = str(first.get("modid") or "")
        info.version = str(first.get("version") or "")
        info.name = str(first.get("name") or info.name)
        info.description = str(first.get("description") or "")
        info.authors = [str(first.get("authorList") or first.get("author") or "")]
        logo = str(first.get("logoFile") or "")
        if logo and logo in archive.namelist():
            info.icon_path = self._extract_icon(archive, logo, info.sha1)
        info.metadata = first

    def _parse_manifest(self, info: ModInfo, archive: zipfile.ZipFile) -> None:
        path = "META-INF/MANIFEST.MF"
        if path in archive.namelist():
            raw = archive.read(path).decode("utf-8", errors="replace")
            fields = {}
            for line in raw.splitlines():
                if ": " in line:
                    key, value = line.split(": ", 1)
                    fields[key.strip()] = value.strip()
            info.name = fields.get("Implementation-Title") or info.name
            info.version = fields.get("Implementation-Version") or ""
            info.authors = [fields.get("Created-By")] if fields.get("Created-By") else []
            info.metadata = fields
        if info.loader == "unknown":
            haystack = " ".join(archive.namelist()).lower()
            if "fabric" in haystack:
                info.loader = "fabric"
                info.required_loaders = ["fabric"]
            elif "neoforge" in haystack:
                info.loader = "neoforge"
                info.required_loaders = ["neoforge"]
            elif "forge" in haystack:
                info.loader = "forge"
                info.required_loaders = ["forge"]

    @staticmethod
    def _flatten_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            return str(value.get("text") or value.get("translate") or "")
        if isinstance(value, list):
            return " ".join(ModManager._flatten_text(v) for v in value)
        return ""

    def _extract_icon(self, archive: zipfile.ZipFile, member: str, digest: str) -> str:
        suffix = Path(member).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            return ""
        target = self._icon_dir / f"{digest[:16]}-{hashlib.sha1(member.encode()).hexdigest()[:8]}{suffix}"
        if target.exists() and target.stat().st_size:
            return str(target)
        try:
            data = archive.read(member)
            if len(data) > 5 * 1024 * 1024:
                return ""
            target.write_bytes(data)
            return str(target)
        except (OSError, KeyError):
            return ""

    # ------------------------------------------------------------------ actions
    def toggle(self, mod: ModInfo) -> ModInfo:
        if mod.filename.lower().endswith(".disabled"):
            new_name = mod.filename[: -len(".disabled")]
            target = mod.path.with_name(new_name)
            if target.exists():
                raise LauncherError(f"无法启用：{new_name} 已存在。")
            mod.path.rename(target)
        else:
            target = mod.path.with_name(mod.path.name + ".disabled")
            if target.exists():
                raise LauncherError(f"无法禁用：{target.name} 已存在。")
            mod.path.rename(target)
        return self.parse_mod(target)

    def delete(self, mod: ModInfo, *, permanent: bool = False) -> Path:
        if not mod.path.exists():
            raise LauncherError("Mod 文件已经被移动或删除。")
        if permanent:
            mod.path.unlink()
            return mod.path
        trash = data_dir() / "trash" / "mods" / f"{now_stamp()}-{mod.filename}"
        trash.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(mod.path), str(trash))
        return trash

    def import_file(self, source: Path, version: InstalledVersion) -> ModInfo:
        source = Path(source)
        if not source.exists() or source.suffix.lower() not in {".jar", ".zip"}:
            raise LauncherError("请选择 .jar 格式的 Mod 文件。")
        try:
            with zipfile.ZipFile(source) as archive:
                if "fabric.mod.json" not in archive.namelist() \
                        and "META-INF/mods.toml" not in archive.namelist() \
                        and "META-INF/neoforge.mods.toml" not in archive.namelist() \
                        and "mcmod.info" not in archive.namelist():
                    # Some valid mods only carry a manifest; reject obvious
                    # non-mod zips such as a world save.
                    if not any(n.startswith("META-INF/") for n in archive.namelist()):
                        raise LauncherError("这个文件不像是一个 Minecraft Mod。")
        except zipfile.BadZipFile as exc:
            raise LauncherError("文件不是有效的 JAR / ZIP。") from exc
        target_dir = self.minecraft.mods_dir(version, create=True)
        target = unique_path(target_dir / source.name)
        shutil.copy2(source, target)
        return self.parse_mod(target, version)

    # ------------------------------------------------------------------ resources
    def scan_resources(self, version: InstalledVersion | None, kind: str) -> list[ResourceInfo]:
        if not version:
            return []
        if kind == "resourcepacks":
            folder = self.minecraft.resourcepacks_dir(version)
        elif kind == "shaderpacks":
            folder = self.minecraft.shaderpacks_dir(version)
        else:
            return []
        if not folder.exists():
            return []
        result: list[ResourceInfo] = []
        selected = self.resourcepack_names(version) if kind == "resourcepacks" else set()
        for entry in folder.iterdir():
            try:
                if entry.is_dir():
                    size = sum(p.stat().st_size for p in entry.rglob("*") if p.is_file())
                elif entry.suffix.lower() in {".zip", ".jar"}:
                    size = entry.stat().st_size
                else:
                    continue
                display_name = entry.name
                description = ""
                icon_path = ""
                if entry.is_file() and entry.suffix.lower() == ".zip":
                    try:
                        with zipfile.ZipFile(entry) as archive:
                            if "pack.mcmeta" in archive.namelist():
                                meta = json.loads(archive.read("pack.mcmeta").decode("utf-8-sig", errors="replace"))
                                pack = meta.get("pack") or {}
                                description = self._flatten_text(pack.get("description"))
                            if "pack.png" in archive.namelist():
                                data = archive.read("pack.png")
                                if len(data) < 5 * 1024 * 1024:
                                    icon = self._icon_dir / f"pack-{hashlib.sha1(str(entry).encode()).hexdigest()[:16]}.png"
                                    if not icon.exists():
                                        icon.write_bytes(data)
                                    icon_path = str(icon)
                    except (zipfile.BadZipFile, OSError, ValueError):
                        pass
                elif entry.is_dir():
                    meta_path = entry / "pack.mcmeta"
                    if meta_path.exists():
                        try:
                            meta = json.loads(meta_path.read_text(encoding="utf-8-sig", errors="replace"))
                            description = self._flatten_text((meta.get("pack") or {}).get("description"))
                        except (OSError, ValueError):
                            pass
                    pack_icon = entry / "pack.png"
                    if pack_icon.exists():
                        icon_path = str(pack_icon)
                result.append(ResourceInfo(
                    path=entry, name=display_name, kind=kind, size=size,
                    description=description, icon_path=icon_path,
                    modified=entry.stat().st_mtime,
                    enabled=(kind == "resourcepacks" and (
                        f"file/{display_name}" in selected or display_name in selected)),
                ))
            except OSError:
                continue
        result.sort(key=lambda item: item.name.lower())
        return result

    def _options_path(self, version: InstalledVersion) -> Path:
        return self.minecraft.game_dir(version) / "options.txt"

    def resourcepack_names(self, version: InstalledVersion) -> set[str]:
        path = self._options_path(version)
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return set()
        for line in lines:
            if not line.startswith("resourcePacks:"):
                continue
            raw = line.split(":", 1)[1].strip()
            try:
                values = json.loads(raw)
            except ValueError:
                return set()
            if isinstance(values, list):
                return {str(value) for value in values}
        return set()

    def set_resourcepack_enabled(self, version: InstalledVersion, resource: ResourceInfo,
                                 enabled: bool) -> None:
        path = self._options_path(version)
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        key = f"file/{resource.name}"
        names = self.resourcepack_names(version)
        if enabled:
            names.add(key)
        else:
            names.discard(key)
            names.discard(resource.name)
        value = "resourcePacks:" + json.dumps(sorted(names), ensure_ascii=False,
                                               separators=(",", ":"))
        replaced = False
        for index, line in enumerate(lines):
            if line.startswith("resourcePacks:"):
                lines[index] = value
                replaced = True
                break
        if not replaced:
            lines.append(value)
        temp_path = path.with_name(path.name + ".tmp")
        try:
            temp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.replace(temp_path, path)
        except OSError as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise LauncherError(f"无法写入 options.txt：{exc}") from exc

    def open_mods_folder(self, version: InstalledVersion) -> Path:
        folder = self.minecraft.mods_dir(version, create=True)
        return folder

    # ------------------------------------------------------------------ saves
    def scan_saves(self, version: InstalledVersion | None) -> list[SaveInfo]:
        if not version:
            return []
        folder = self.minecraft.saves_dir(version)
        if not folder.exists():
            return []
        result = []
        for entry in folder.iterdir():
            if not entry.is_dir() or not (entry / "level.dat").exists():
                continue
            name = entry.name
            game_version = ""
            try:
                raw = (entry / "level.dat").read_bytes()
                data = _read_gzip_nbt(raw)
                if data:
                    level = _nbt_find(data, "LevelName")
                    version_data = _nbt_find(data, "Version")
                    if isinstance(version_data, dict):
                        game_version = str(version_data.get("Name") or "")
                    # Some saves expose Data/LevelName at the root.
                    if isinstance(level, str) and level:
                        name = level
            except Exception:
                pass
            icon = entry / "icon.png"
            result.append(SaveInfo(
                path=entry, name=name, modified=entry.stat().st_mtime,
                icon_path=str(icon) if icon.exists() else "", game_version=game_version,
                size=sum(p.stat().st_size for p in entry.rglob("*") if p.is_file()),
            ))
        result.sort(key=lambda item: item.modified, reverse=True)
        return result


# --------------------------------------------------------------------------- tiny NBT reader
def _read_gzip_nbt(raw: bytes) -> dict[str, Any]:
    import gzip
    import io
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            data = stream.read()
    except OSError:
        return {}
    try:
        tag, value, _ = _nbt_payload(data, 0)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _nbt_payload(data: bytes, offset: int) -> tuple[int, Any, int]:
    tag = data[offset]
    offset += 1
    if tag == 0:
        return tag, None, offset
    name, offset = _nbt_string(data, offset)
    if tag == 1:
        return tag, struct.unpack_from(">b", data, offset)[0], offset + 1
    if tag == 2:
        return tag, struct.unpack_from(">h", data, offset)[0], offset + 2
    if tag == 3:
        return tag, struct.unpack_from(">i", data, offset)[0], offset + 4
    if tag == 4:
        return tag, struct.unpack_from(">q", data, offset)[0], offset + 8
    if tag == 5:
        return tag, struct.unpack_from(">f", data, offset)[0], offset + 4
    if tag == 6:
        return tag, struct.unpack_from(">d", data, offset)[0], offset + 8
    if tag == 7:
        length = struct.unpack_from(">i", data, offset)[0]
        offset += 4
        return tag, data[offset:offset + length], offset + length
    if tag == 8:
        length = struct.unpack_from(">H", data, offset)[0]
        offset += 2
        try:
            return tag, data[offset:offset + length].decode("utf-8", errors="replace"), offset + length
        except Exception:
            return tag, "", offset + length
    if tag == 9:
        item_type = data[offset]
        length = struct.unpack_from(">i", data, offset + 1)[0]
        offset += 5
        items = []
        for _ in range(length):
            _, value, offset = _nbt_payload_value(data, offset, item_type)
            items.append(value)
        return tag, items, offset
    if tag == 10:
        result = {}
        while True:
            child_type = data[offset]
            if child_type == 0:
                return tag, result, offset + 1
            child_name, offset = _nbt_string(data, offset + 1)
            _, value, offset = _nbt_payload_value(data, offset, child_type)
            result[child_name] = value
    if tag == 11:
        length = struct.unpack_from(">i", data, offset)[0]
        offset += 4
        values = list(struct.unpack_from(f">{length}i", data, offset))
        return tag, values, offset + length * 4
    if tag == 12:
        length = struct.unpack_from(">i", data, offset)[0]
        offset += 4
        values = list(struct.unpack_from(f">{length}q", data, offset))
        return tag, values, offset + length * 8
    raise ValueError(f"Unknown NBT tag {tag}")


def _nbt_payload_value(data: bytes, offset: int, tag: int) -> tuple[int, Any, int]:
    if tag == 8:
        length = struct.unpack_from(">H", data, offset)[0]
        offset += 2
        return tag, data[offset:offset + length].decode("utf-8", errors="replace"), offset + length
    if tag == 10:
        result = {}
        while True:
            child_type = data[offset]
            if child_type == 0:
                return tag, result, offset + 1
            child_name, offset = _nbt_string(data, offset + 1)
            _, value, offset = _nbt_payload_value(data, offset, child_type)
            result[child_name] = value
    # Reuse the top-level reader for scalar/list/array types. It expects a
    # leading name only for named payloads, so handle common scalar cases.
    if tag == 1:
        return tag, struct.unpack_from(">b", data, offset)[0], offset + 1
    if tag == 2:
        return tag, struct.unpack_from(">h", data, offset)[0], offset + 2
    if tag == 3:
        return tag, struct.unpack_from(">i", data, offset)[0], offset + 4
    if tag == 4:
        return tag, struct.unpack_from(">q", data, offset)[0], offset + 8
    if tag == 5:
        return tag, struct.unpack_from(">f", data, offset)[0], offset + 4
    if tag == 6:
        return tag, struct.unpack_from(">d", data, offset)[0], offset + 8
    if tag == 7:
        length = struct.unpack_from(">i", data, offset)[0]
        offset += 4
        return tag, data[offset:offset + length], offset + length
    if tag == 9:
        item_type = data[offset]
        length = struct.unpack_from(">i", data, offset + 1)[0]
        offset += 5
        items = []
        for _ in range(length):
            _, value, offset = _nbt_payload_value(data, offset, item_type)
            items.append(value)
        return tag, items, offset
    if tag == 11:
        length = struct.unpack_from(">i", data, offset)[0]
        offset += 4
        return tag, list(struct.unpack_from(f">{length}i", data, offset)), offset + length * 4
    if tag == 12:
        length = struct.unpack_from(">i", data, offset)[0]
        offset += 4
        return tag, list(struct.unpack_from(f">{length}q", data, offset)), offset + length * 8
    raise ValueError(f"Unknown NBT tag {tag}")


def _nbt_string(data: bytes, offset: int) -> tuple[str, int]:
    length = struct.unpack_from(">H", data, offset)[0]
    offset += 2
    return data[offset:offset + length].decode("utf-8", errors="replace"), offset + length


def _nbt_find(data: dict[str, Any], key: str) -> Any:
    if key in data:
        return data[key]
    for value in data.values():
        if isinstance(value, dict):
            found = _nbt_find(value, key)
            if found is not None:
                return found
    return ""
