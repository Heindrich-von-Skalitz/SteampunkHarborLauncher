from __future__ import annotations

import json
import logging
import os
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..services.modrinth import ModrinthService
from ..storage import Settings
from ..utils import (
    CancelledError, LauncherError, copy_tree_merge, download_file, now_stamp,
    read_json, safe_child, sha1_file, write_json,
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


class ModpackService:
    def __init__(self, settings: Settings, minecraft: MinecraftManager,
                 installer: InstallerService, modrinth: ModrinthService) -> None:
        self.settings = settings
        self.minecraft = minecraft
        self.installer = installer
        self.modrinth = modrinth

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
                       cancel: Callable[[], bool] | None = None) -> InstalledVersion:
        progress = progress or (lambda _p, _m: None)
        log = log or (lambda _m: None)
        cancel = cancel or (lambda: False)
        path = Path(archive_path)
        if not path.exists() or not zipfile.is_zipfile(path):
            raise LauncherError("请选择有效的 .mrpack 或 .zip 整合包。")
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "modrinth.index.json" not in names:
                if "manifest.json" in names:
                    raise LauncherError(
                        "检测到 CurseForge 整合包。由于 CurseForge 下载 API 需要专属密钥，"
                        "请先在 Modrinth 发布页获取 .mrpack 格式，或手动放入实例。")
                if "mcbbs.packmeta" in names:
                    raise LauncherError("暂不支持 MCBBS 格式整合包，请使用 Modrinth (.mrpack) 整合包。")
                # Generic archive: wrap current instance and extract its contents.
                return self._import_generic(archive, path, java_path, progress, log, cancel)
            try:
                index = json.loads(archive.read("modrinth.index.json").decode("utf-8-sig"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise LauncherError("整合包索引损坏。") from exc
        if not isinstance(index, dict):
            raise LauncherError("整合包索引格式不正确。")
        dependencies = index.get("dependencies") or {}
        game_version = str(dependencies.get("minecraft") or "")
        if not game_version:
            raise LauncherError("整合包没有声明 Minecraft 版本。")
        pack_name = self._unique_instance_name(str(index.get("name") or path.stem))
        progress(2, f"整合包：{pack_name} · Minecraft {game_version}")
        loader = "vanilla"
        loader_version = ""
        for key, candidate in (("fabric-loader", "fabric"), ("quilt-loader", "quilt"),
                               ("forge", "forge"), ("neoforge", "neoforge")):
            if dependencies.get(key):
                loader = candidate
                loader_version = str(dependencies[key])
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
            hashes = entry.get("hashes") or {}
            sha1 = hashes.get("sha1")
            try:
                download_file(
                    downloads, target, sha1=str(sha1) if sha1 else None,
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
            for prefix in ("overrides/", "client-overrides/"):
                for info in archive.infolist():
                    if info.is_dir() or not info.filename.startswith(prefix):
                        continue
                    rel = info.filename[len(prefix):]
                    if not rel:
                        continue
                    target = safe_child(game_dir, rel)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        with archive.open(info) as src, open(target, "wb") as dst:
                            shutil.copyfileobj(src, dst)
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
                        cancel: Callable[[], bool]) -> InstalledVersion:
        current = self.minecraft.find(self.settings.selected_version)
        if not current:
            raise LauncherError("导入普通压缩包前，请先选择一个已安装版本作为基础。")
        pack_name = self._unique_instance_name(path.stem)
        wrapper_dir = self.minecraft.create_instance_folder(pack_name)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        write_json(wrapper_dir / f"{pack_name}.json", {
            "id": pack_name, "inheritsFrom": current.id, "type": "release",
            "time": now, "releaseTime": now,
        })
        self.settings.save_version_profile(pack_name, {"isolation": "on"})
        progress(20, "解压外部文件…")
        for info in archive.infolist():
            if info.is_dir():
                continue
            target = safe_child(wrapper_dir, info.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
        progress(100, "压缩包导入完成")
        return self.minecraft.inspect(pack_name)

    def _unique_instance_name(self, desired: str) -> str:
        base = "".join(ch for ch in desired if ch.isalnum() or ch in " ._-")[:48].strip()
        if not base:
            base = "Imported Pack"
        candidate = base
        index = 2
        while (self.minecraft.versions_dir / candidate).exists():
            candidate = f"{base} ({index})"
            index += 1
        return candidate


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
