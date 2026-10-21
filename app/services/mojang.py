from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..constants import (
    FABRIC_META, FABRIC_META_MIRROR, MIRROR_MANIFEST, NEOFORGE_VERSIONS,
    OFFICIAL_MANIFEST, QUILT_META, QUILT_META_MIRROR,
)
from ..paths import cache_dir
from ..utils import NetworkError, LauncherError, read_json, request_json, write_json

LOG = logging.getLogger("shl.mojang")
MANIFEST_TTL = 60 * 60 * 6


def _cache_versions_dir() -> Path:
    path = cache_dir() / "versions"
    path.mkdir(parents=True, exist_ok=True)
    return path


class MojangService:
    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self._manifest: dict[str, Any] | None = None
        self._manifest_mtime = 0.0
        self._manifest_cache = cache_dir() / "version_manifest_v2.json"

    @property
    def source(self) -> str:
        return str(self.settings.get("download_source") or "auto")

    def _manifest_urls(self) -> list[str]:
        source = self.source
        if source == "official":
            return [OFFICIAL_MANIFEST]
        if source == "mirror":
            return [MIRROR_MANIFEST]
        return [OFFICIAL_MANIFEST, MIRROR_MANIFEST]

    def get_manifest(self, *, force: bool = False) -> dict[str, Any]:
        if self._manifest and not force:
            age = time.time() - self._manifest_mtime
            if age < 300:
                return self._manifest
        cached = read_json(self._manifest_cache, None)
        if isinstance(cached, dict) and not force:
            try:
                age = time.time() - self._manifest_cache.stat().st_mtime
            except OSError:
                age = 10 ** 9
            if age < MANIFEST_TTL and cached.get("versions"):
                self._manifest = cached
                self._manifest_mtime = time.time()
                # A stale cache should still be refreshed silently when possible.
                if age < 600:
                    return cached
        errors: list[str] = []
        for url in self._manifest_urls():
            try:
                data = request_json(url, timeout=30, context="获取 Minecraft 版本清单",
                                    headers={"Accept": "application/json"})
                if isinstance(data, dict) and isinstance(data.get("versions"), list):
                    self._manifest = data
                    self._manifest_mtime = time.time()
                    data["_source"] = "BMCLAPI" if "bmclapi" in url else "Mojang"
                    write_json(self._manifest_cache, data)
                    return data
            except Exception as exc:
                errors.append(str(exc))
                LOG.warning("Manifest source failed (%s): %s", url, exc)
        if isinstance(cached, dict) and cached.get("versions"):
            LOG.warning("Using stale cached manifest after network failure")
            cached["_source"] = "本地缓存"
            self._manifest = cached
            self._manifest_mtime = 0
            return cached
        raise NetworkError("无法获取 Minecraft 版本清单，请检查网络或在设置中切换下载源。" +
                           (f"（{errors[-1][:120]}）" if errors else ""))

    def versions(self, *, include_snapshots: bool = False, force: bool = False) -> list[dict[str, Any]]:
        manifest = self.get_manifest(force=force)
        versions = [v for v in manifest.get("versions", []) if isinstance(v, dict)]
        if not include_snapshots:
            versions = [v for v in versions if v.get("type") == "release"]
        return versions

    def latest(self) -> dict[str, str]:
        try:
            manifest = self.get_manifest()
            latest = manifest.get("latest") or {}
            return {"release": str(latest.get("release", "")),
                    "snapshot": str(latest.get("snapshot", ""))}
        except Exception:
            return {"release": "", "snapshot": ""}

    def version_entry(self, version_id: str) -> dict[str, Any] | None:
        try:
            for version in self.get_manifest().get("versions", []):
                if version.get("id") == version_id:
                    return version
        except Exception:
            pass
        return None

    def metadata(self, version_id: str, *, force: bool = False) -> dict[str, Any]:
        cached_path = _cache_versions_dir() / f"{version_id}.json"
        cached = read_json(cached_path, None)
        if isinstance(cached, dict) and not force:
            return cached
        entry = self.version_entry(version_id)
        official_url = str((entry or {}).get("url") or "")
        candidates: list[str] = []
        if official_url:
            candidates.append(official_url)
        if self.source != "official":
            candidates.append(f"https://bmclapi2.bangbang93.com/version/{version_id}/json")
        if not candidates:
            candidates = [f"https://bmclapi2.bangbang93.com/version/{version_id}/json"]
        errors: list[str] = []
        for url in candidates:
            try:
                data = request_json(url, timeout=40, context=f"获取 {version_id} 元数据",
                                    headers={"Accept": "application/json"})
                if isinstance(data, dict) and data.get("id"):
                    write_json(cached_path, data)
                    return data
            except Exception as exc:
                errors.append(str(exc))
                LOG.warning("Version metadata failed (%s): %s", url, exc)
        # Fall back to an already installed JSON.
        root = Path(self.settings.minecraft_dir)
        local = read_json(root / "versions" / version_id / f"{version_id}.json", None)
        if isinstance(local, dict):
            return local
        raise NetworkError(f"无法获取 Minecraft {version_id} 的安装信息。" +
                           (f"（{errors[-1][:160]}）" if errors else ""))

    def client_url(self, version_id: str, meta: dict[str, Any] | None = None) -> list[str]:
        meta = meta or self.metadata(version_id)
        official = ((meta.get("downloads") or {}).get("client") or {}).get("url") or ""
        candidates = []
        if official:
            candidates.append(official)
        if self.source != "official":
            candidates.append(f"https://bmclapi2.bangbang93.com/version/{version_id}/client")
        return candidates

    @staticmethod
    def mirror_url(url: str) -> str:
        """Map a Mojang file URL to a BMCLAPI equivalent when practical."""
        try:
            parsed = urlparse(url)
        except Exception:
            return url
        host = parsed.netloc.lower()
        if host == "resources.download.minecraft.net":
            return url.replace("https://resources.download.minecraft.net/",
                               "https://bmclapi2.bangbang93.com/assets/", 1)
        if host in {"libraries.minecraft.net", "maven.minecraftforge.net", "maven.neoforged.net"}:
            return url.replace(f"https://{host}/", "https://bmclapi2.bangbang93.com/maven/", 1)
        if host in {"piston-data.mojang.com", "piston-meta.mojang.com", "launcher.mojang.com",
                    "launchermeta.mojang.com"}:
            return url.replace(f"https://{host}/", "https://bmclapi2.bangbang93.com/", 1)
        return url

    def url_candidates(self, url: str) -> list[str]:
        source = self.source
        if source == "official" or not url:
            return [url]
        mirror = self.mirror_url(url)
        if mirror == url:
            return [url]
        # "mirror" means mirror-first, not mirror-only: BMCLAPI does not carry
        # every Mojang object (for example some logging configs), so a 404 must
        # fall back to the official URL instead of failing the install.
        if source == "mirror":
            return [mirror, url]
        return [url, mirror]


class LoaderService:
    def __init__(self, settings: Any) -> None:
        self.settings = settings

    @property
    def source(self) -> str:
        return str(self.settings.get("download_source") or "auto")

    def _meta_bases(self, kind: str) -> list[str]:
        if kind == "fabric":
            if self.source == "official":
                return [FABRIC_META]
            if self.source == "mirror":
                return [FABRIC_META_MIRROR]
            # The mirror is generally faster for the primarily Chinese audience
            # of this launcher, while the official endpoint remains a fallback.
            return [FABRIC_META_MIRROR, FABRIC_META]
        if self.source == "official":
            return [QUILT_META]
        if self.source == "mirror":
            return [QUILT_META_MIRROR]
        return [QUILT_META_MIRROR, QUILT_META]

    @staticmethod
    def _neoforge_prefix(game_version: str) -> str:
        text = str(game_version or "").strip()
        if text == "1.20.1":
            # NeoForge 1.20.1 was published on a Forge-derived 47.1 line before
            # the project switched to Minecraft-derived version numbers.
            return "47.1."
        parts = text.split(".")
        if parts and parts[0].isdigit() and int(parts[0]) >= 26:
            # Minecraft's short version scheme (26.1, 26.1.1, 26.1.2) is
            # embedded in the first three NeoForge components. An omitted
            # patch component means patch zero; NeoForge 26.1.2.x targets
            # Minecraft 26.1.2 and must not be offered for bare 26.1.
            while len(parts) < 3:
                parts.append("0")
            return ".".join(parts[:3]) + "."
        if len(parts) >= 2 and parts[0] == "1":
            return ".".join(parts[1:]) + "."
        return text + "."

    def neoforge_versions(self, game_version: str, stable_only: bool = True) -> list[str]:
        """Read NeoForge versions from its official Maven metadata.

        minecraft-launcher-lib 8.0 predates Minecraft's new short version scheme
        (26.1, 26.2, ...) and normalizes every NeoForge version to a 1.x
        Minecraft ID, so its version list is empty for 26.1. NeoForge version
        numbers are Minecraft-derived, which makes the official Maven metadata
        the authoritative source. The official installer itself is still used
        for the actual processor run.
        """
        data = request_json(
            NEOFORGE_VERSIONS, timeout=35, context="获取 NeoForge 版本列表",
            headers={"Accept": "application/json"},
        )
        raw = data.get("versions") if isinstance(data, dict) else None
        if not isinstance(raw, list):
            raise NetworkError("NeoForge 版本元数据格式不正确。")
        prefix = self._neoforge_prefix(game_version)
        matched: list[str] = []
        for value in raw:
            version = str(value or "").strip()
            if not version.startswith(prefix):
                continue
            lowered = version.lower()
            if stable_only and any(tag in lowered for tag in ("alpha", "beta", "snapshot", "pre")):
                continue
            matched.append(version)
        # Maven returns oldest→newest; present newest first.
        matched.reverse()
        return matched

    def loader_options(self, kind: str, game_version: str) -> list[dict[str, Any]]:
        errors: list[str] = []
        for base in self._meta_bases(kind):
            try:
                if kind == "fabric":
                    data = request_json(f"{base}/versions/loader/{game_version}", timeout=30,
                                        context=f"获取 Fabric 加载器列表")
                    if isinstance(data, list):
                        return [item for item in data if isinstance(item, dict)]
                else:
                    data = request_json(f"{base}/versions/loader/{game_version}", timeout=30,
                                        context=f"获取 Quilt 加载器列表")
                    if isinstance(data, dict) and isinstance(data.get("loader"), list):
                        return data["loader"]
                    if isinstance(data, list):
                        return data
            except Exception as exc:
                errors.append(str(exc))
        raise NetworkError(f"无法获取 {kind.title()} 加载器列表。" +
                           (f"（{errors[-1][:160]}）" if errors else ""))

    def loader_versions(self, kind: str, game_version: str) -> list[dict[str, Any]]:
        """Return selectable loader versions with a stable flag.

        Fabric/Quilt come from their meta APIs, NeoForge from the official
        Maven and Forge from minecraft-launcher-lib's Maven parser.
        """
        if kind in {"fabric", "quilt"}:
            rows: list[dict[str, Any]] = []
            for option in self.loader_options(kind, game_version):
                item = option.get("loader") if isinstance(option.get("loader"), dict) else option
                version = str(item.get("version") or "")
                if version:
                    rows.append({"version": version, "stable": bool(item.get("stable", False))})
            return rows
        if kind == "neoforge":
            return [{"version": version, "stable": "beta" not in version.lower()}
                    for version in self.neoforge_versions(game_version, stable_only=False)]
        if kind == "forge":
            from minecraft_launcher_lib.forge import list_forge_versions
            prefix = f"{game_version}-"
            rows = [{"version": version[len(prefix):], "stable": True}
                    for version in list_forge_versions()
                    if str(version).startswith(prefix)]
            rows.reverse()
            return rows
        return []

    def fabric_profile(self, game_version: str, loader_version: str) -> dict[str, Any]:
        errors: list[str] = []
        for base in self._meta_bases("fabric"):
            url = f"{base}/versions/loader/{game_version}/{loader_version}/profile/json"
            try:
                data = request_json(url, timeout=40, context="获取 Fabric 安装配置")
                if isinstance(data, dict) and data.get("id"):
                    return data
            except Exception as exc:
                errors.append(str(exc))
        raise NetworkError("无法获取 Fabric 安装配置。" +
                           (f"（{errors[-1][:180]}）" if errors else ""))

    def quilt_profile(self, game_version: str, loader_version: str) -> dict[str, Any]:
        errors: list[str] = []
        for base in self._meta_bases("quilt"):
            url = f"{base}/versions/loader/{game_version}/{loader_version}/profile/json"
            try:
                data = request_json(url, timeout=40, context="获取 Quilt 安装配置")
                if isinstance(data, dict) and data.get("id"):
                    return data
            except Exception as exc:
                errors.append(str(exc))
        raise NetworkError("无法获取 Quilt 安装配置。" +
                           (f"（{errors[-1][:180]}）" if errors else ""))
