from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import tomllib
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..services.modrinth import ModrinthService
from ..storage import Settings
from ..utils import (
    CancelledError, LauncherError, copy_tree_merge, copy_zip_member,
    download_file, now_stamp, read_json, safe_child, safe_component, sha1_file,
    validate_component, validate_zip_limits, write_json,
)
from .installer import InstallerService
from .minecraft import InstalledVersion, MinecraftManager
from .mods import ModManager

LOG = logging.getLogger("shl.modpack")
Progress = Callable[[int, str], None]
LogFn = Callable[[str], None]


def source_path(version: InstalledVersion) -> Path:
    return version.path / ".shl" / "mod_sources.json"


def load_sources(version: InstalledVersion) -> dict[str, Any]:
    data = read_json(source_path(version), {})
    return data if isinstance(data, dict) else {}


def record_source(version: InstalledVersion, filename: str, project: dict[str, Any],
                  version_data: dict[str, Any], file_data: dict[str, Any]) -> None:
    data = load_sources(version)
    data[filename] = {
        "project_id": project.get("project_id") or project.get("id") or "",
        "slug": project.get("slug") or "",
        "title": project.get("title") or "",
        "version_id": version_data.get("id") or "",
        "version_number": version_data.get("version_number") or "",
        "filename": file_data.get("filename") or filename,
        "downloads": (file_data.get("urls") or
                      ([file_data["url"]] if file_data.get("url") else [])),
        "hashes": file_data.get("hashes") or {},
        "size": file_data.get("size") or 0,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    write_json(source_path(version), data)


GENERIC_KEEP_DIRS = {
    "mods", "config", "defaultconfigs", "saves", "resourcepacks",
    "shaderpacks", "datapacks", "scripts", "kubejs", "journeymap",
    "xaero", "iris", "patchouli_books", "openloader", "customnpcs",
    "structures", "schematics", "replay_recordings", "local",
}
GENERIC_KEEP_FILES = {
    "options.txt", "optionsof.txt", "optionsshaders.txt", "servers.dat",
    "realms_persistence.json",
}
MAX_MOD_SCAN = 5
MAX_MOD_JAR = 16 * 1024 * 1024
MAX_MANIFEST = 1024 * 1024


def _mc_version_from_text(value: Any) -> str:
    match = re.search(r"(?<![\d.])(\d+\.\d+(?:\.\d+)?)", str(value or ""))
    return match.group(1) if match else ""


def _toml_minecraft_version(data: dict[str, Any]) -> str:
    deps = data.get("dependencies") or {}
    if not isinstance(deps, dict):
        return ""
    for rows in deps.values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and str(row.get("modId") or "") == "minecraft":
                return _mc_version_from_text(row.get("versionRange") or row.get("version"))
    return ""


def _sniff_mod_jar(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> tuple[str, str]:
    if int(info.file_size or 0) > MAX_MOD_JAR:
        return "", ""
    with archive.open(info) as handle:
        data = handle.read(MAX_MOD_JAR + 1)
    if len(data) > MAX_MOD_JAR:
        return "", ""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as jar:
            names = {name.lower(): name for name in jar.namelist()}
            if "fabric.mod.json" in names:
                try:
                    meta = json.loads(jar.read(names["fabric.mod.json"]).decode("utf-8-sig"))
                    depends = meta.get("depends") or {}
                    return "fabric", _mc_version_from_text(depends.get("minecraft"))
                except Exception:
                    return "fabric", ""
            if "quilt.mod.json" in names:
                try:
                    meta = json.loads(jar.read(names["quilt.mod.json"]).decode("utf-8-sig"))
                    depends = ((meta.get("quilt_loader") or {}).get("depends") or {})
                    return "quilt", _mc_version_from_text(depends.get("minecraft"))
                except Exception:
                    return "quilt", ""
            for key, loader in (
                ("meta-inf/neoforge.mods.toml", "neoforge"),
                ("meta-inf/mods.toml", "forge"),
            ):
                if key in names:
                    try:
                        meta = tomllib.loads(jar.read(names[key]).decode("utf-8-sig"))
                        return loader, _toml_minecraft_version(meta)
                    except Exception:
                        return loader, ""
    except (OSError, zipfile.BadZipFile):
        return "", ""
    return "", ""


def _generic_prefix(names: list[str]) -> str:
    clean = [n.replace("\\", "/").lstrip("/") for n in names
             if n and not n.startswith("__MACOSX/") and not n.endswith("/")]
    for first in {n.split("/")[0] for n in clean if "/" in n}:
        if first.lower() in {".minecraft", "overrides"}:
            return first + "/"
    for top in sorted({n.split("/")[0] for n in clean if "/" in n}, key=len):
        rest = [n[len(top) + 1:] for n in clean if n.startswith(top + "/")]
        for item in rest:
            first_nested = item.split("/")[0]
            if first_nested.lower() in {".minecraft", "overrides"}:
                return top + "/" + first_nested + "/"
        if any(r.split("/")[0].lower() in GENERIC_KEEP_DIRS for r in rest):
            return top + "/"
    return ""


def sniff_generic_archive(archive: zipfile.ZipFile,
                          fallback_version: str = "") -> dict[str, Any]:
    """Heuristically identify a non-standard modpack archive."""
    names = [n.replace("\\", "/") for n in archive.namelist()]
    prefix = _generic_prefix(names)
    relative = [n[len(prefix):] for n in names if n.startswith(prefix)]
    kept = [n for n in relative
            if n and (n.split("/")[0].lower() in GENERIC_KEEP_DIRS
                      or n.lower() in GENERIC_KEEP_FILES)]
    loader = ""
    base_version = ""
    scanned = 0
    infos = {n.replace("\\", "/"): info for n, info in
             ((name, archive.getinfo(name)) for name in archive.namelist())}
    for rel in relative:
        if not rel.lower().startswith("mods/") or not rel.lower().endswith(".jar"):
            continue
        scanned += 1
        if scanned > MAX_MOD_SCAN:
            break
        info = infos.get(prefix + rel)
        if info is None:
            continue
        found_loader, found_version = _sniff_mod_jar(archive, info)
        if found_loader and not loader:
            loader = found_loader
            base_version = found_version
    return {
        "kind": "generic",
        "prefix": prefix,
        "base_version": base_version or fallback_version,
        "loader": loader or "vanilla",
        "loader_version": "",
        "found": bool(kept),
        "skipped": max(0, len(relative) - len(kept)),
        "scanned_mods": scanned,
    }


def sniff_curseforge_manifest(archive: zipfile.ZipFile,
                              fallback_version: str = "") -> dict[str, Any]:
    data: dict[str, Any] = {}
    try:
        info = archive.getinfo("manifest.json")
        with archive.open(info) as handle:
            raw = handle.read(MAX_MANIFEST + 1)
        if len(raw) <= MAX_MANIFEST:
            loaded = json.loads(raw.decode("utf-8-sig"))
            if isinstance(loaded, dict):
                data = loaded
    except Exception:
        data = {}
    minecraft = data.get("minecraft") if isinstance(data.get("minecraft"), dict) else {}
    base_version = _mc_version_from_text(minecraft.get("version")) or fallback_version
    loader = "vanilla"
    loader_version = ""
    for item in minecraft.get("modLoaders") or []:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("id") or "")
        for candidate in ("neoforge", "forge", "fabric", "quilt"):
            if raw.lower().startswith(candidate + "-"):
                loader = candidate
                loader_version = raw[len(candidate) + 1:]
                break
        if loader != "vanilla":
            break
    return {
        "kind": "curseforge",
        "prefix": "overrides/" if any(
            n.replace("\\", "/").startswith("overrides/") for n in archive.namelist()) else "",
        "base_version": base_version,
        "loader": loader,
        "loader_version": loader_version,
        "found": True,
        "skipped": 0,
        "scanned_mods": 0,
    }


class ModpackService:
    def __init__(self, settings: Settings, minecraft: MinecraftManager,
                 installer: InstallerService, modrinth: ModrinthService) -> None:
        self.settings = settings
        self.minecraft = minecraft
        self.installer = installer
        self.modrinth = modrinth
        self.last_import_warning = ""

    # ------------------------------------------------------------------ export
    def export_mrpack(self, version: InstalledVersion, output: Path, *, name: str,
                      pack_version: str = "1.0.0", summary: str = "",
                      include_config: bool = True, progress: Progress | None = None,
                      log: LogFn | None = None,
                      cancel: Callable[[], bool] | None = None) -> Path:
        progress = progress or (lambda _p, _m: None)
        log = log or (lambda _m: None)
        cancel = cancel or (lambda: False)
        if not name.strip():
            raise LauncherError("整合包名称不能为空。")
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        sources = load_sources(version)
        mods_dir = self.minecraft.mods_dir(version)
        game_dir = self.minecraft.game_dir(version)
        dependencies: dict[str, str] = {"minecraft": version.base_version or version.id}
        if "fabric" in version.loaders:
            dependencies["fabric-loader"] = version.loader_versions.get("fabric", "")
        if "quilt" in version.loaders:
            dependencies["quilt-loader"] = version.loader_versions.get("quilt", "")
        if "forge" in version.loaders:
            dependencies["forge"] = version.loader_versions.get("forge", "")
        if "neoforge" in version.loaders:
            dependencies["neoforge"] = version.loader_versions.get("neoforge", "")
        dependencies = {key: value for key, value in dependencies.items() if value}
        files: list[dict[str, Any]] = []
        override_mods: list[Path] = []
        mod_files = []
        if mods_dir.exists():
            mod_files = sorted([p for p in mods_dir.iterdir()
                                if p.is_file() and p.name.endswith(".jar")])
        total_steps = max(1, len(mod_files) + 6)
        step = 0

        def tick(message: str) -> None:
            nonlocal step
            step += 1
            progress(min(95, int(step / total_steps * 95)), message)
            if cancel():
                raise CancelledError("已取消整合包导出")

        progress(2, "分析模组来源…")
        for mod_file in mod_files:
            if cancel():
                raise CancelledError("已取消整合包导出")
            source = sources.get(mod_file.name) or {}
            downloads = source.get("downloads") or []
            hashes = source.get("hashes") or {}
            digest = sha1_file(mod_file)
            usable_hashes = {key: str(value) for key, value in hashes.items()
                             if key in {"sha1", "sha512"} and value}
            if downloads and usable_hashes.get("sha1") \
                    and usable_hashes["sha1"].lower() == digest.lower():
                files.append({
                    "path": f"mods/{mod_file.name}",
                    "hashes": usable_hashes,
                    "env": {"client": "required", "server": "optional"},
                    "downloads": downloads,
                    "fileSize": mod_file.stat().st_size,
                })
            else:
                override_mods.append(mod_file)
            tick(f"整理 {mod_file.name}")

        index = {
            "formatVersion": 1,
            "game": "minecraft",
            "versionId": pack_version or "1.0.0",
            "name": name.strip(),
            "summary": summary.strip() or f"由机械冒险港启动器导出的整合包，基于 Minecraft {version.base_version}。",
            "files": files,
            "dependencies": dependencies,
        }
        if "forge" in version.loaders or "neoforge" in version.loaders:
            # The standard has no separate install flag for Forge packs.
            index["dependencies"] = dependencies
        progress(18, "写入整合包索引…")
        overrides: list[tuple[Path, str]] = []
        if include_config:
            for directory in ("config", "defaultconfigs", "kubejs", "scripts"):
                source_dir = game_dir / directory
                if source_dir.exists():
                    overrides.append((source_dir, f"overrides/{directory}"))
            for filename in ("options.txt", "servers.dat"):
                file = game_dir / filename
                if file.exists():
                    overrides.append((file, f"overrides/{filename}"))
        for mod in override_mods:
            overrides.append((mod, f"overrides/mods/{mod.name}"))
        progress(28, "打包覆盖文件…")
        try:
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                archive.writestr("modrinth.index.json",
                                 json.dumps(index, ensure_ascii=False, indent=2))
                for source, arc_root in overrides:
                    if cancel():
                        raise CancelledError("已取消整合包导出")
                    if source.is_file():
                        archive.write(source, arc_root)
                    elif source.is_dir():
                        for root, dirs, filenames in os.walk(source):
                            dirs[:] = [d for d in dirs if d not in {
                                ".cache", "logs", "crash-reports", "screenshots", "__pycache__"}]
                            for filename in filenames:
                                file = Path(root) / filename
                                try:
                                    rel = file.relative_to(source)
                                except ValueError:
                                    continue
                                arc = f"{arc_root}/{rel.as_posix()}"
                                try:
                                    archive.write(file, arc)
                                except OSError:
                                    continue
            progress(96, "计算整合包大小…")
            log(f"已导出整合包：{output.name}（{len(files)} 个远程文件，{len(overrides)} 个覆盖项）")
            progress(100, "整合包导出完成")
            return output
        except OSError as exc:
            output.unlink(missing_ok=True)
            raise LauncherError(f"导出整合包失败：{exc}") from exc

    # ------------------------------------------------------------------ import
    def import_modpack(self, archive_path: Path, *, java_path: str = "",
                       progress: Progress | None = None, log: LogFn | None = None,
                       cancel: Callable[[], bool] | None = None,
                       name: str = "") -> InstalledVersion:
        progress = progress or (lambda _p, _m: None)
        log = log or (lambda _m: None)
        cancel = cancel or (lambda: False)
        self.last_import_warning = ""
        path = Path(archive_path)
        if not path.exists() or not zipfile.is_zipfile(path):
            raise LauncherError("请选择有效的 .mrpack 或 .zip 整合包。")
        with zipfile.ZipFile(path) as archive:
            validate_zip_limits(archive)
            names = set(archive.namelist())
            if "modrinth.index.json" not in names:
                current = self.minecraft.find(self.settings.selected_version)
                fallback = current.base_version if current else ""
                if "manifest.json" in names:
                    layout = sniff_curseforge_manifest(archive, fallback)
                else:
                    layout = sniff_generic_archive(archive, fallback)
                return self._import_generic(
                    archive, path, java_path, progress, log, cancel,
                    name=name, layout=layout)
            try:
                index = json.loads(archive.read("modrinth.index.json").decode("utf-8-sig"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise LauncherError("整合包索引损坏。") from exc
        if not isinstance(index, dict):
            raise LauncherError("整合包索引格式不正确。")
        dependencies = index.get("dependencies") or {}
        game_version = validate_component(
            str(dependencies.get("minecraft") or ""), "整合包 Minecraft 版本")
        if not game_version:
            raise LauncherError("整合包没有声明 Minecraft 版本。")
        pack_name = self._unique_instance_name(str(name or index.get("name") or path.stem))
        progress(2, f"整合包：{pack_name} · Minecraft {game_version}")
        loader = "vanilla"
        loader_version = ""
        for key, candidate in (("fabric-loader", "fabric"), ("quilt-loader", "quilt"),
                               ("forge", "forge"), ("neoforge", "neoforge")):
            if dependencies.get(key):
                loader = candidate
                loader_version = validate_component(
                    str(dependencies[key]), f"整合包加载器版本 {key}")
                break
        if loader in {"forge", "neoforge"} and not java_path:
            raise LauncherError(
                f"该整合包需要 {loader.title()}。请先在设置中准备 Java，再导入整合包。")
        log(f"安装依赖：Minecraft {game_version} / {loader} {loader_version}".strip())
        installed = self.installer.install(
            game_version, loader=loader, loader_version=loader_version,
            progress=lambda p, m: progress(5 + int(p * 0.5), m), log=log, cancel=cancel,
            java_path=java_path,
        )
        if cancel():
            raise CancelledError("已取消整合包导入")
        progress(56, "创建整合包实例…")
        wrapper_name = pack_name
        wrapper_dir = self.minecraft.create_instance_folder(wrapper_name)
        wrapper_id = wrapper_name
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        wrapper = {
            "id": wrapper_id,
            "inheritsFrom": installed.id,
            "type": "release",
            "time": now,
            "releaseTime": now,
        }
        write_json(wrapper_dir / f"{wrapper_id}.json", wrapper)
        instance = self.minecraft.inspect(wrapper_id)
        self.settings.save_version_profile(wrapper_id, {
            "isolation": "on",
            "note": f"由整合包「{pack_name}」导入",
            "memory_mode": "inherit",
        })
        game_dir = wrapper_dir
        for folder in ("mods", "config", "defaultconfigs", "resourcepacks", "shaderpacks"):
            (game_dir / folder).mkdir(parents=True, exist_ok=True)
        # Download files.
        files = [f for f in index.get("files", []) if isinstance(f, dict)]
        total = max(1, len(files))
        errors: list[str] = []
        for count, entry in enumerate(files, 1):
            if cancel():
                raise CancelledError("已取消整合包导入")
            path_text = str(entry.get("path") or "")
            if not path_text:
                continue
            env = entry.get("env") or {}
            if str(env.get("client") or "required") == "unsupported":
                continue
            target = safe_child(game_dir, path_text)
            downloads = [str(u) for u in entry.get("downloads") or [] if u]
            if not downloads:
                continue
            hashes = entry.get("hashes") if isinstance(entry.get("hashes"), dict) else {}
            sha1 = str(hashes.get("sha1") or "").lower()
            sha512 = str(hashes.get("sha512") or "").lower()
            if not sha1 and not sha512:
                raise LauncherError(
                    f"整合包文件缺少 SHA-1/SHA-512，已拒绝下载：{path_text}")
            try:
                download_file(
                    downloads, target, sha1=sha1 or None, sha512=sha512 or None,
                    size=entry.get("fileSize") or None, cancel=cancel,
                    context=f"整合包文件 {target.name}",
                )
            except Exception as exc:
                errors.append(f"{path_text}：{exc}")
                log(f"文件下载失败：{path_text}")
            progress(58 + int(count / total * 24), f"下载整合包文件 {count}/{total}")
        if cancel():
            raise CancelledError("已取消整合包导入")
        progress(84, "解压覆盖配置…")
        with zipfile.ZipFile(path) as archive:
            validate_zip_limits(archive)
            budget = [0]
            for prefix in ("overrides/", "client-overrides/"):
                for info in archive.infolist():
                    if info.is_dir() or not info.filename.startswith(prefix):
                        continue
                    rel = info.filename[len(prefix):]
                    if not rel:
                        continue
                    target = safe_child(game_dir, rel)
                    try:
                        copy_zip_member(archive, info, target, budget=budget)
                    except OSError as exc:
                        log(f"覆盖文件解压失败：{rel}（{exc}）")
        # Preserve pack identity for UI.
        write_json(wrapper_dir / ".shl" / "modpack.json", {
            "name": str(index.get("name") or pack_name),
            "version": str(index.get("versionId") or ""),
            "summary": str(index.get("summary") or ""),
            "source": path.name,
            "imported_at": now_iso(),
            "errors": errors,
        })
        self.settings.set("selected_version", wrapper_id)
        progress(100, "整合包导入完成")
        log(f"整合包已导入为实例：{wrapper_id}")
        if errors:
            log(f"有 {len(errors)} 个文件下载失败，可在实例中重新补全。")
        return self.minecraft.inspect(wrapper_id)

    def _import_generic(self, archive: zipfile.ZipFile, path: Path, java_path: str,
                        progress: Progress, log: LogFn,
                        cancel: Callable[[], bool], name: str = "",
                        layout: dict[str, Any] | None = None) -> InstalledVersion:
        current = self.minecraft.find(self.settings.selected_version)
        fallback = current.base_version if current else ""
        layout = layout or sniff_generic_archive(archive, fallback)
        base_version = str(layout.get("base_version") or fallback)
        if not base_version:
            raise LauncherError("无法从压缩包中识别 Minecraft 版本，请先选择一个已安装版本。")
        base_version = validate_component(base_version, "整合包 Minecraft 版本")
        loader = str(layout.get("loader") or "vanilla")
        if loader not in {"vanilla", "fabric", "quilt", "forge", "neoforge"}:
            loader = "vanilla"
        loader_version = str(layout.get("loader_version") or "").strip()
        if loader_version:
            loader_version = validate_component(loader_version, "整合包加载器版本")

        installed = current
        same_base = bool(installed and installed.base_version == base_version)
        same_loader = bool(
            installed and (loader == "vanilla" or loader in installed.loaders))
        if not (same_base and same_loader):
            if self.installer is None:
                raise LauncherError("无法安装整合包依赖的加载器。")
            progress(4, f"准备 Minecraft {base_version} / {loader}…")
            installed = self.installer.install(
                base_version, loader=loader, loader_version=loader_version,
                java_path=java_path,
                progress=lambda p, m: progress(5 + int(p * 0.35), m),
                log=log, cancel=cancel,
            )
        if installed is None:
            raise LauncherError("整合包依赖安装失败。")
        pack_name = self._unique_instance_name(str(name or path.stem))
        wrapper_dir = self.minecraft.create_instance_folder(pack_name)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        write_json(wrapper_dir / f"{pack_name}.json", {
            "id": pack_name, "inheritsFrom": installed.id, "type": "release",
            "time": now, "releaseTime": now,
        })

        prefix = str(layout.get("prefix") or "")
        progress(48, "按目录结构解压整合包…")
        budget = [0]
        kept = 0
        skipped = 0
        for info in archive.infolist():
            if info.is_dir():
                continue
            full = info.filename.replace("\\", "/")
            if prefix and not full.startswith(prefix):
                continue
            rel = full[len(prefix):].lstrip("/") if prefix else full.lstrip("/")
            if not rel:
                continue
            first = rel.split("/")[0].lower()
            if first not in GENERIC_KEEP_DIRS and rel.lower() not in GENERIC_KEEP_FILES:
                skipped += 1
                continue
            target = safe_child(wrapper_dir, rel)
            copy_zip_member(archive, info, target, budget=budget)
            kept += 1
        if kept == 0:
            raise LauncherError(
                "压缩包中没有识别到 mods、config、saves、resourcepacks 等游戏目录。")
        warning = (
            "检测到非标准格式整合包，已按默认结构强制解压。"
            "由于缺失官方元数据，无法保证模组兼容性，请谨慎启动。")
        if loader != "vanilla":
            warning += f" 自动识别加载器：{loader}。"
        if skipped:
            warning += f" 已跳过 {skipped} 个非游戏文件。"
        self.last_import_warning = warning
        self.settings.save_version_profile(pack_name, {
            "isolation": "on",
            "note": warning,
        })
        log(warning)
        progress(100, "非标准整合包导入完成")
        return self.minecraft.inspect(pack_name)

    def _unique_instance_name(self, desired: str) -> str:
        base = "".join(ch for ch in desired if ch.isalnum() or ch in " ._-")[:48].strip()
        try:
            base = safe_component(base or "Imported Pack", "整合包版本名称")
        except LauncherError:
            base = "Imported Pack"
        candidate = base
        index = 2
        while (self.minecraft.versions_dir / candidate).exists():
            candidate = f"{base} ({index})"
            index += 1
        return candidate


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
