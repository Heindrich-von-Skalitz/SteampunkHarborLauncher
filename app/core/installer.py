from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from ..constants import NEOFORGE_MAVEN
from ..paths import cache_dir
from ..services.mojang import LoaderService, MojangService
from ..storage import Settings
from ..utils import (
    CancelledError, DownloadError, LauncherError, download_file, format_size,
    is_windows, read_json, request_text, safe_child, safe_component, sha1_file,
    sha256_file, validate_component, write_json,
)
from .libraries import collect_library_files, extract_natives, missing_library_files
from .minecraft import MinecraftManager

LOG = logging.getLogger("shl.installer")
Progress = Callable[[int, str], None]
LogFn = Callable[[str], None]


@dataclass
class DownloadSpec:
    path: Path
    urls: list[str]
    label: str
    sha1: str = ""
    size: int = 0
    check_sha1: bool = False

    @property
    def exists_valid(self) -> bool:
        try:
            if not self.path.exists() or self.path.stat().st_size == 0:
                return False
            if self.size and self.path.stat().st_size != self.size and self.size > 1024:
                return False
            if self.check_sha1 and self.sha1 and sha1_file(self.path).lower() != self.sha1.lower():
                return False
            return True
        except OSError:
            return False


class InstallerService:
    def __init__(self, settings: Settings, manager: MinecraftManager,
                 mojang: MojangService, loaders: LoaderService) -> None:
        self.settings = settings
        self.manager = manager
        self.mojang = mojang
        self.loaders = loaders

    # ------------------------------------------------------------------ public
    def install(self, version_id: str, *, loader: str = "vanilla", loader_version: str = "",
                force: bool = False, progress: Progress | None = None,
                log: LogFn | None = None, cancel: Callable[[], bool] | None = None,
                java_path: str = ""):
        progress = progress or (lambda _p, _m: None)
        log = log or (lambda _m: None)
        cancel = cancel or (lambda: False)
        version_id = validate_component(version_id, "Minecraft 版本")
        if loader_version:
            loader_version = validate_component(loader_version, "加载器版本")
        self.manager.ensure_root()

        def scaled(start: int, end: int) -> Progress:
            def emit(value: int, message: str) -> None:
                progress(int(start + (end - start) * max(0, min(100, value)) / 100), message)
            return emit

        if loader in {"vanilla", "", "original"}:
            self.install_vanilla(version_id, force=force, progress=progress, log=log, cancel=cancel)
            return self.manager.inspect(version_id)
        if loader == "neoforge":
            return self._install_neoforge(version_id, loader_version, java_path,
                                          progress, log, cancel)
        if loader == "forge":
            return self._install_forge_like(version_id, loader, loader_version, java_path,
                                            progress, log, cancel)
        if loader not in {"fabric", "quilt"}:
            raise LauncherError(f"暂不支持在此处安装 {loader} 加载器。")
        log(f"先准备 Minecraft {version_id} 本体")
        self.install_vanilla(version_id, force=False, progress=scaled(0, 58),
                             log=log, cancel=cancel)
        if cancel():
            raise CancelledError("操作已取消")
        progress(60, "查询加载器版本…")
        options = self.loaders.loader_options(loader, version_id)
        if not options:
            raise LauncherError(f"{loader.title()} 尚未支持 Minecraft {version_id}。")
        selected = None
        if loader_version:
            for option in options:
                item = option.get("loader") or option
                if str(item.get("version") or "") == loader_version:
                    selected = item
                    break
            if not selected:
                raise LauncherError(f"没有找到 {loader.title()} {loader_version}。")
        else:
            for option in options:
                item = option.get("loader") or option
                if item.get("stable", False):
                    selected = item
                    break
            selected = selected or (options[0].get("loader") or options[0])
        chosen_version = str(selected.get("version") or "")
        if not chosen_version:
            raise LauncherError("加载器列表缺少版本号。")
        log(f"准备安装 {loader.title()} {chosen_version}")
        progress(65, f"下载 {loader.title()} 安装配置…")
        if loader == "fabric":
            profile = self.loaders.fabric_profile(version_id, chosen_version)
        else:
            profile = self.loaders.quilt_profile(version_id, chosen_version)
        profile_id = str(profile.get("id") or f"{loader}-loader-{chosen_version}-{version_id}")
        profile["id"] = profile_id
        profile["inheritsFrom"] = str(profile.get("inheritsFrom") or version_id)
        target = self.manager.create_instance_folder(profile_id)
        profile_path = target / f"{profile_id}.json"
        write_json(profile_path, profile)
        log(f"加载器配置写入 {profile_path.name}")
        progress(70, "补全加载器依赖库…")
        self._download_profile_libraries(profile, profile_id, profile_path,
                                         scaled(70, 94), log, cancel)
        if cancel():
            raise CancelledError("操作已取消")
        progress(97, "校验安装结果…")
        installed = self.manager.inspect(profile_id)
        if installed.broken:
            raise LauncherError(f"加载器安装后校验失败：{installed.error}")
        progress(100, f"{loader.title()} 安装完成")
        log(f"完成：{profile_id}")
        return installed

    # ------------------------------------------------------------------ vanilla
    def install_vanilla(self, version_id: str, *, force: bool = False,
                        progress: Progress | None = None, log: LogFn | None = None,
                        cancel: Callable[[], bool] | None = None) -> Path:
        progress = progress or (lambda _p, _m: None)
        log = log or (lambda _m: None)
        cancel = cancel or (lambda: False)
        version_id = validate_component(version_id, "Minecraft 版本")
        target = self.manager.create_instance_folder(version_id)
        json_path = target / f"{version_id}.json"
        local_meta = read_json(json_path, None)
        use_local = isinstance(local_meta, dict) and not force
        progress(2, "读取版本信息…")
        if use_local:
            meta = local_meta
            log(f"使用本地版本信息：{json_path.name}")
        else:
            log("从版本清单获取安装信息")
            meta = self.mojang.metadata(version_id, force=True)
            write_json(json_path, meta)
        if not meta.get("id"):
            meta["id"] = version_id
        if not json_path.exists():
            write_json(json_path, meta)
        self._write_launcher_profile(version_id)

        # Client jar ---------------------------------------------------------
        downloads = meta.get("downloads") or {}
        client = downloads.get("client") or {}
        client_jar = target / f"{version_id}.jar"
        if not client_jar.exists() or force:
            progress(5, "下载游戏客户端…")
            urls = self._client_urls(version_id, client.get("url"))
            log(f"客户端：{format_size(client.get('size'))}，{len(urls)} 个下载源")
            download_file(
                urls, client_jar, sha1=client.get("sha1"), size=client.get("size"),
                context="Minecraft 客户端", cancel=cancel,
                progress=lambda done, total: progress(5 + int(done / max(1, total) * 8),
                                                      f"下载游戏客户端 · {format_size(done)} / {format_size(total)}"),
            )
        else:
            log("客户端已存在，跳过下载")

        # Libraries ----------------------------------------------------------
        progress(14, "分析支持库…")
        files = collect_library_files(meta, self.manager.root)
        missing = missing_library_files(files)
        log(f"支持库 {len(files)} 个，缺失 {len(missing)} 个")
        specs = []
        for item in files:
            if item.path.exists() and item.path.stat().st_size > 0:
                continue
            urls = self._library_urls(item.urls)
            if not urls:
                continue
            specs.append(DownloadSpec(item.path, urls, item.name, item.sha1, item.size, True))
        if specs:
            errors = self._download_batch(specs, 14, 38, progress, log, cancel,
                                          phase="支持库", strict=False, max_workers=6)
            if errors:
                # A missing classpath file is fatal; surface it before continuing.
                still_missing = [s for s in specs if not s.exists_valid]
                if still_missing:
                    sample = "、".join(Path(s.label.split(":")[1] if ":" in s.label else s.label).name
                                      for s in still_missing[:3])
                    raise LauncherError(f"有 {len(still_missing)} 个支持库下载失败：{sample}")
        else:
            progress(38, "支持库完整")

        # Asset index --------------------------------------------------------
        progress(40, "检查资源索引…")
        asset_index_info = meta.get("assetIndex") or {}
        index_id = str(asset_index_info.get("id") or "")
        if index_id:
            index_id = safe_component(index_id, "资源索引 ID")
        index_data: dict[str, Any] | None = None
        assets_root = self.manager.root / "assets"
        if index_id:
            index_path = safe_child(assets_root / "indexes", f"{index_id}.json")
            index_data = read_json(index_path, None)
            if not isinstance(index_data, dict) or not isinstance(index_data.get("objects"), dict):
                index_urls = self._asset_index_urls(asset_index_info)
                download_file(
                    index_urls, index_path, sha1=asset_index_info.get("sha1"),
                    size=asset_index_info.get("size"), context="资源索引", cancel=cancel,
                )
                index_data = read_json(index_path, None)
            if not isinstance(index_data, dict):
                raise LauncherError("资源索引无法读取，安装可能不完整。")
            log(f"资源索引 {index_id}：{len(index_data.get('objects') or {})} 个对象")
        else:
            log("此版本没有资源索引（远古版本），跳过资源补全")

        # Assets -------------------------------------------------------------
        asset_errors: list[str] = []
        if index_data and isinstance(index_data.get("objects"), dict):
            objects = index_data["objects"]
            progress(42, f"盘点 {len(objects)} 个资源文件…")
            asset_specs: dict[str, DownloadSpec] = {}
            for name, info in objects.items():
                if not isinstance(info, dict):
                    continue
                digest = str(info.get("hash") or "")
                if not re.fullmatch(r"[0-9a-fA-F]{40}", digest):
                    raise LauncherError(f"资源哈希格式非法：{digest[:80]!r}")
                path = safe_child(assets_root / "objects", f"{digest[:2]}/{digest}")
                if path.exists() and path.stat().st_size > 0:
                    continue
                urls = self._asset_urls(digest)
                asset_specs[str(path).casefold()] = DownloadSpec(
                    path=path, urls=urls, label=name, sha1=digest,
                    size=int(info.get("size") or 0), check_sha1=True,
                )
            log(f"资源缺失 {len(asset_specs)} 个")
            if asset_specs:
                asset_errors = self._download_batch(
                    list(asset_specs.values()), 42, 87, progress, log, cancel,
                    phase="资源文件", strict=False, max_workers=12,
                )
            else:
                progress(87, "资源文件完整")
            progress(88, "整理旧版资源目录…")
            try:
                self._materialize_virtual_assets(index_id, index_data)
            except OSError as exc:
                log(f"旧版资源映射失败（不影响现代版本）：{exc}")

        # Logging config -----------------------------------------------------
        logging = meta.get("logging") or {}
        logging_client = logging.get("client") or {}
        logging_file = logging_client.get("file") or {}
        if logging_file.get("url"):
            config_id = safe_component(
                str(logging_file.get("id") or "client-log4j.xml"), "日志配置 ID")
            config_path = safe_child(assets_root / "log_configs", config_id)
            if not config_path.exists():
                progress(89, "下载日志配置…")
                download_file(
                    self.mojang.url_candidates(str(logging_file["url"])), config_path,
                    sha1=logging_file.get("sha1"), size=logging_file.get("size"),
                    context="日志配置", cancel=cancel,
                )

        # Natives ------------------------------------------------------------
        progress(92, "准备 Natives…")
        natives_dir = target / f"{version_id}-natives"
        # Refresh the library list: downloaded native jars are now present.
        files = collect_library_files(meta, self.manager.root)
        native_files = [item for item in files if item.is_native]
        if native_files:
            try:
                if natives_dir.exists():
                    shutil.rmtree(natives_dir, ignore_errors=False)
                natives_dir.mkdir(parents=True, exist_ok=True)
                count = extract_natives(native_files, natives_dir)
                log(f"已解压 {count} 个 Natives 文件")
            except OSError as exc:
                raise LauncherError(f"无法更新 Natives（游戏可能正在运行）：{exc}") from exc
        progress(96, "校验安装结果…")
        if not client_jar.exists():
            raise LauncherError("客户端主文件缺失，安装未完成。")
        meta_info = {"installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                     "asset_errors": len(asset_errors), "version": version_id}
        write_json(target / "_shl_install.json", meta_info)
        if asset_errors:
            progress(100, f"安装完成，但有 {len(asset_errors)} 个资源下载失败")
            log(f"资源下载失败 {len(asset_errors)} 个，可稍后点击“补全文件”重试")
        else:
            progress(100, f"Minecraft {version_id} 安装完成")
        return target

    # ------------------------------------------------------------------ forge family
    def _install_neoforge(self, version_id: str, loader_version: str, java_path: str,
                          progress: Progress, log: LogFn,
                          cancel: Callable[[], bool]) -> Any:
        """Install NeoForge with its official installer.

        NeoForge's official Maven uses Minecraft-derived version numbers. The
        vendored minecraft-launcher-lib 8.0 predates Minecraft's modern short
        version scheme (26.1, ...) and reports no 26.1 loaders, so we read the
        authoritative Maven metadata ourselves and invoke the official
        ``--install-client`` installer directly, entirely inside the configured
        game directory and launcher cache.
        """
        java = Path(java_path)
        if not java_path or not java.is_file():
            raise LauncherError("安装 NeoForge 需要可运行的 Java，请先在设置中检测 Java。")
        version_id = validate_component(version_id, "Minecraft 版本")
        log(f"NeoForge 安装使用 Java：{java}")
        self.install_vanilla(
            version_id, force=False,
            progress=lambda p, msg: progress(2 + int(p * 0.32), msg),
            log=log, cancel=cancel)
        if cancel():
            raise CancelledError("操作已取消")
        progress(36, f"读取 NeoForge {version_id} 版本列表…")
        try:
            stable = self.loaders.neoforge_versions(version_id, stable_only=True)
        except Exception as exc:
            raise LauncherError(f"无法获取 NeoForge 版本列表：{exc}") from exc
        all_versions = stable
        if not all_versions:
            try:
                all_versions = self.loaders.neoforge_versions(version_id, stable_only=False)
            except Exception as exc:
                raise LauncherError(f"无法获取 NeoForge 版本列表：{exc}") from exc
            if all_versions:
                log(f"NeoForge 尚未为 Minecraft {version_id} 发布稳定版，"
                    "将自动选择该版本对应的最新测试版。")
                progress(37, f"暂无稳定版，使用 NeoForge 测试版…")
        if not all_versions:
            raise LauncherError(f"NeoForge 官方 Maven 尚未发布 Minecraft {version_id} 的版本。")
        chosen = validate_component(
            loader_version.strip() or all_versions[0], "NeoForge 版本")
        if chosen not in all_versions:
            raise LauncherError(f"NeoForge 版本不在官方列表中：{chosen}")
        log(f"NeoForge 版本：{chosen}（该游戏版本共 {len(all_versions)} 个可用版本）")

        install_root = cache_dir() / "neoforge" / chosen
        install_root.mkdir(parents=True, exist_ok=True)
        installer = install_root / f"neoforge-{chosen}-installer.jar"
        url = (f"{NEOFORGE_MAVEN}/releases/net/neoforged/neoforge/"
               f"{chosen}/neoforge-{chosen}-installer.jar")
        progress(40, f"下载 NeoForge {chosen} 安装组件…")
        expected_sha1 = ""
        expected_sha256 = ""
        for suffix, target_name in ((".sha256", "sha256"), (".sha1", "sha1")):
            try:
                text = request_text(url + suffix, timeout=20,
                                    context="NeoForge 安装器哈希").strip()
            except Exception:
                continue
            digest = text.split()[0].lower() if text else ""
            if target_name == "sha256" and len(digest) == 64:
                expected_sha256 = digest
                break
            if target_name == "sha1" and len(digest) == 40:
                expected_sha1 = digest
                break
        if not expected_sha1 and not expected_sha256:
            raise LauncherError("无法获取 NeoForge 安装器官方哈希，已拒绝执行。")
        try:
            download_file(
                url, installer, cancel=cancel, context="NeoForge 安装组件",
                sha1=expected_sha1 or None, sha256=expected_sha256 or None,
                progress=lambda done, total: progress(
                    40 + int(done / max(1, total) * 10),
                    f"下载 NeoForge 安装组件 · {format_size(done)} / {format_size(total)}"),
            )
        except DownloadError as exc:
            raise LauncherError(str(exc)) from exc
        if not zipfile.is_zipfile(installer):
            installer.unlink(missing_ok=True)
            raise LauncherError("NeoForge 安装组件不是有效的 JAR 文件，请稍后重试。")
        progress(51, "运行 NeoForge 官方安装器…")
        log("运行 NeoForge 官方安装器；首次安装需要下载加载器库与处理器，可能需要几分钟")
        env = os.environ.copy()
        java_home = java.parent.parent
        env["JAVA_HOME"] = str(java_home)
        env["PATH"] = str(java.parent) + os.pathsep + env.get("PATH", "")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if is_windows() else 0
        try:
            result = subprocess.run(
                [str(java), "-jar", str(installer), "--install-client", str(self.manager.root)],
                cwd=str(install_root), env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=1800, creationflags=creationflags,
            )
        except subprocess.TimeoutExpired as exc:
            raise LauncherError("NeoForge 官方安装器执行超时（30 分钟）。") from exc
        except OSError as exc:
            raise LauncherError(f"无法执行 NeoForge 官方安装器：{exc}") from exc
        if result.stdout:
            for line in [ln for ln in result.stdout.splitlines() if ln.strip()][-12:]:
                log(f"[NeoForge] {line[:240]}")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            detail = detail[-900:] if detail else f"退出码 {result.returncode}"
            raise LauncherError(f"NeoForge 官方安装器失败：{detail}")
        progress(90, "检查 NeoForge 安装结果…")
        expected = f"neoforge-{chosen}"
        candidates: list[Any] = []
        found = self.manager.find(expected)
        if found:
            candidates.append(found)
        for item in self.manager.scan():
            if "neoforge" in item.loaders and all(item.id != c.id for c in candidates):
                candidates.append(item)
        installed = next(
            (item for item in candidates if not item.broken and item.loaders), None)
        if installed is None:
            raise LauncherError(
                f"NeoForge 安装器已运行，但没有在 {self.manager.root} 中找到可用的 NeoForge 版本。")
        progress(100, f"NeoForge {chosen} 安装完成")
        log(f"NeoForge 安装完成：{installed.id}")
        return installed

    def _install_forge_like(self, version_id: str, loader: str, loader_version: str,
                            java_path: str, progress: Progress, log: LogFn,
                            cancel: Callable[[], bool]) -> Any:
        """Install Forge through its official install processors.

        We use the independently maintained BSD-2-Clause
        ``minecraft-launcher-lib`` for this step, then hand the resulting
        profile back to our own version scanner and launcher. NeoForge has its
        own Maven-only path above because newer Minecraft versions use a version
        scheme the vendored helper does not understand.
        """
        if not java_path:
            raise LauncherError(f"安装 {loader.title()} 需要 Java，请先在设置中检测 Java。")
        try:
            from minecraft_launcher_lib.mod_loader import get_mod_loader
        except ImportError as exc:
            raise LauncherError("缺少 minecraft-launcher-lib，无法运行 Forge 官方安装处理器。") from exc
        progress(2, f"准备 Minecraft {version_id} 本体…")
        self.install_vanilla(version_id, force=False,
                             progress=lambda p, msg: progress(2 + int(p * 0.33), msg),
                             log=log, cancel=cancel)
        progress(36, f"读取 {loader.title()} 版本列表…")
        try:
            mod = get_mod_loader(loader)
            versions = mod.get_loader_versions(version_id, stable_only=True)
            if not versions:
                versions = mod.get_loader_versions(version_id, stable_only=False)
            if not versions:
                raise LauncherError(f"{loader.title()} 不支持 Minecraft {version_id}。")
            chosen = loader_version.strip() or versions[0]
            if chosen not in versions:
                # A manually specified version can be valid even if the loader
                # did not advertise it; allow it and let the official installer
                # provide the authoritative error.
                log(f"使用指定的 {loader.title()} 版本 {chosen}")
            log(f"将通过 {loader.title()} 官方安装处理器安装 {chosen}")
            progress(40, f"下载 {loader.title()} 安装组件…")
            max_value = [100]
            last_value = [0]

            def status(message: str) -> None:
                if cancel():
                    raise CancelledError("操作已取消")
                compact = " ".join(str(message).split())
                log(compact)
                value = last_value[0]
                progress(max(40, min(90, 40 + int((value / max(1, max_value[0])) * 48))),
                         compact[:90])

            def set_max(value: int) -> None:
                max_value[0] = max(1, int(value))

            def set_progress(value: int) -> None:
                if cancel():
                    raise CancelledError("操作已取消")
                last_value[0] = max(0, int(value))
                progress(max(40, min(91, 40 + int((last_value[0] / max(1, max_value[0])) * 50))),
                         f"运行 {loader.title()} 安装处理器…")

            callback = {"setStatus": status, "setMax": set_max, "setProgress": set_progress}
            installed_id = mod.install(
                version_id, str(self.manager.root), loader_version=chosen,
                callback=callback, java=java_path,
            )
        except CancelledError:
            raise
        except Exception as exc:
            raise LauncherError(
                f"{loader.title()} 官方安装器执行失败：{exc}\n"
                "请确认网络可以访问加载器官方源，并检查 Java 是否可运行。"
            ) from exc
        progress(94, "校验加载器版本…")
        installed = self.manager.inspect(str(installed_id))
        if installed.broken:
            raise LauncherError(f"{loader.title()} 安装完成，但版本信息校验失败：{installed.error}")
        progress(100, f"{loader.title()} {chosen} 安装完成")
        log(f"完成：{installed_id}")
        return installed

    # ------------------------------------------------------------------ loader libs
    def _download_profile_libraries(self, profile: dict[str, Any], profile_id: str,
                                    profile_path: Path, progress: Progress, log: LogFn,
                                    cancel: Callable[[], bool]) -> None:
        files = collect_library_files(profile, self.manager.root)
        missing = missing_library_files(files)
        log(f"加载器支持库 {len(files)} 个，缺失 {len(missing)} 个")
        specs: list[DownloadSpec] = []
        for item in files:
            if item.path.exists() and item.path.stat().st_size > 0:
                continue
            urls = self._library_urls(item.urls)
            if not urls:
                continue
            specs.append(DownloadSpec(item.path, urls, item.name, item.sha1, item.size, True))
        if specs:
            errors = self._download_batch(specs, 0, 82, progress, log, cancel,
                                          phase="加载器支持库", strict=False, max_workers=6)
            still_missing = [s for s in specs if not s.exists_valid]
            if still_missing:
                raise LauncherError(f"有 {len(still_missing)} 个加载器支持库下载失败。")
        # Loader profiles can contain natives (older Quilt/Fabric builds).
        files = collect_library_files(profile, self.manager.root)
        native_files = [item for item in files if item.is_native]
        if native_files:
            natives_dir = self.manager.version_folder(profile_id) / f"{profile_id}-natives"
            if natives_dir.exists():
                shutil.rmtree(natives_dir, ignore_errors=True)
            extract_natives(native_files, natives_dir)
        # Ensure the profile JSON includes its inherited version explicitly.
        data = read_json(profile_path, profile)
        if isinstance(data, dict):
            data.setdefault("inheritsFrom", "unknown")
            write_json(profile_path, data)
        progress(96, "加载器文件已就绪")

    # ------------------------------------------------------------------ downloading
    def _download_batch(self, specs: list[DownloadSpec], start: int, end: int,
                        progress: Progress, log: LogFn, cancel: Callable[[], bool],
                        *, phase: str, strict: bool, max_workers: int) -> list[str]:
        total = len(specs)
        if not total:
            progress(end, f"{phase}完整")
            return []
        lock = threading.Lock()
        done = 0
        errors: list[str] = []
        downloaded_bytes = 0
        total_bytes = sum(spec.size for spec in specs) or 0
        last_emit = 0.0
        skipped = 0

        def worker(spec: DownloadSpec) -> tuple[bool, str, int, int]:
            if cancel():
                raise CancelledError("操作已取消")
            if spec.exists_valid:
                return True, spec.label, spec.size, 0
            try:
                download_file(
                    spec.urls, spec.path, sha1=spec.sha1 if spec.check_sha1 else None,
                    size=spec.size or None, context=spec.label, cancel=cancel,
                )
                return True, spec.label, spec.size, 1
            except CancelledError:
                raise
            except Exception as exc:
                return False, f"{spec.label}：{exc}", spec.size, 1

        max_workers = max(1, min(max_workers, int(self.settings.get("parallel_downloads") or 8)))
        executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="shl-dl")
        try:
            futures = {executor.submit(worker, spec): spec for spec in specs}
            for future in as_completed(futures):
                if cancel():
                    for pending in futures:
                        pending.cancel()
                    raise CancelledError("操作已取消")
                try:
                    ok, label, size, downloaded = future.result()
                except CancelledError:
                    raise
                except Exception as exc:
                    ok, label, size, downloaded = False, str(exc), 0, 0
                with lock:
                    done += 1
                    downloaded_bytes += size
                    if not ok:
                        errors.append(label)
                    if ok and downloaded == 0:
                        skipped += 1
                    now = time.monotonic()
                    should_emit = now - last_emit > 0.08 or done == total or not ok
                    if should_emit:
                        last_emit = now
                        pct = start + int((end - start) * done / total)
                        if total_bytes:
                            detail = f"{format_size(downloaded_bytes)} / {format_size(total_bytes)}"
                        else:
                            detail = f"{done} / {total}"
                        progress(pct, f"下载{phase} · {detail}")
                if not ok and strict:
                    for pending in futures:
                        pending.cancel()
                    raise LauncherError(f"{phase}下载失败：{label}")
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
        if errors:
            log(f"{phase}有 {len(errors)} 个文件失败，例如：{errors[0][:180]}")
        else:
            log(f"{phase}完成：{total - skipped} 个下载，{skipped} 个已存在")
        progress(end, f"{phase}下载完成")
        return errors

    # ------------------------------------------------------------------ URLs
    def _source_mode(self) -> str:
        value = str(self.settings.get("download_source") or "auto")
        return "mirror_priority" if value == "mirror" else value

    def _source_order(self, urls: list[str], *, assets: bool = False) -> list[str]:
        source = self._source_mode()
        unique = [u for u in dict.fromkeys(urls) if u]
        mirrors = [self.mojang.mirror_url(url) for url in unique]
        if source == "official":
            return unique
        if source == "mirror_only":
            return [u for u in dict.fromkeys(mirrors)
                    if "bmclapi" in (urlparse(u).hostname or "")]
        if source == "mirror_priority":
            return list(dict.fromkeys(mirrors + unique))
        return list(dict.fromkeys(unique + mirrors))

    def _client_urls(self, version_id: str, original: str | None) -> list[str]:
        source = self._source_mode()
        urls = []
        if original and source != "mirror_only":
            urls.append(str(original))
        if source != "official":
            urls.append(f"https://bmclapi2.bangbang93.com/version/{version_id}/client")
        return self._source_order(urls)

    def _asset_urls(self, digest: str) -> list[str]:
        official = f"https://resources.download.minecraft.net/{digest[:2]}/{digest}"
        mirror = f"https://bmclapi2.bangbang93.com/assets/{digest[:2]}/{digest}"
        source = self._source_mode()
        if source == "official":
            return [official]
        if source == "mirror_only":
            return [mirror]
        if source == "mirror_priority":
            return [mirror, official]
        return [official, mirror]

    def _asset_index_urls(self, info: dict[str, Any]) -> list[str]:
        source = self._source_mode()
        urls = []
        if info.get("url") and source != "mirror_only":
            urls.append(str(info["url"]))
            if source != "official":
                urls.append(self.mojang.mirror_url(str(info["url"])))
        index_id = str(info.get("id") or "")
        if index_id and source != "official":
            index_id = safe_component(index_id, "资源索引 ID")
            urls.append(f"https://bmclapi2.bangbang93.com/assets/indexes/{index_id}.json")
        return self._source_order(urls)

    def _library_urls(self, urls: list[str]) -> list[str]:
        return self._source_order(urls)

    # ------------------------------------------------------------------ misc
    def _materialize_virtual_assets(self, index_id: str, index_data: dict[str, Any]) -> None:
        if not index_data.get("virtual"):
            return
        index_id = safe_component(index_id, "资源索引 ID")
        base = self.manager.root / "assets" / "objects"
        virtual = safe_child(self.manager.root / "assets" / "virtual", index_id)
        virtual.mkdir(parents=True, exist_ok=True)
        for name, info in (index_data.get("objects") or {}).items():
            if not isinstance(info, dict):
                continue
            digest = str(info.get("hash") or "")
            if not re.fullmatch(r"[0-9a-fA-F]{40}", digest):
                raise LauncherError(f"资源哈希格式非法：{digest[:80]!r}")
            source = safe_child(base, f"{digest[:2]}/{digest}")
            if not source.exists():
                continue
            target = safe_child(virtual, str(name))
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or target.stat().st_size != source.stat().st_size:
                shutil.copyfile(source, target)

    def _write_launcher_profile(self, version_id: str) -> None:
        profile_path = self.manager.root / "launcher_profiles.json"
        try:
            data = read_json(profile_path, {})
            if not isinstance(data, dict):
                data = {}
            profiles = data.get("profiles") if isinstance(data.get("profiles"), dict) else {}
            key = f"shl-{version_id}"[:36]
            profiles[key] = {
                "name": f"机械冒险港 · {version_id}",
                "type": "custom",
                "lastVersionId": version_id,
                "created": time.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "icon": "Grass",
            }
            data["profiles"] = profiles
            write_json(profile_path, data)
        except OSError as exc:
            LOG.debug("Could not update launcher_profiles.json: %s", exc)

    def repair(self, version_id: str, *, progress: Progress | None = None,
               log: LogFn | None = None, cancel: Callable[[], bool] | None = None) -> Path:
        installed = self.manager.inspect(version_id)
        base = installed
        while base.inherits_from:
            try:
                base = self.manager.inspect(base.inherits_from)
            except LauncherError:
                break
        log(f"开始补全版本 {version_id}（基础版本 {base.id}）")
        self.install_vanilla(base.id, force=False, progress=progress, log=log, cancel=cancel)
        if installed.inherits_from:
            profile = installed.data
            self._download_profile_libraries(profile, version_id, installed.json_path,
                                             progress or (lambda _p, _m: None),
                                             log or (lambda _m: None),
                                             cancel or (lambda: False))
        return installed.path
