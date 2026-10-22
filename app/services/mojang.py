from __future__ import annotations

import logging
import re
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
from ..utils import (
    LauncherError, NetworkError, is_allowed_download_host, read_json, request_json,
    safe_component, write_json,
)

LOG = logging.getLogger("shl.mojang")
MANIFEST_TTL = 60 * 60 * 6
HASH_RE = re.compile(r"^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$")
LIBRARY_PATH_RE = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)+$")


def _valid_metadata_url(value: Any) -> bool:
    try:
        parsed = urlparse(str(value or ""))
        port = parsed.port
    except ValueError:
        return False
    return (parsed.scheme == "https" and bool(parsed.hostname)
            and not parsed.username and not parsed.password
            and port in {None, 443}
            and is_allowed_download_host(parsed.hostname.lower().rstrip(".")))


def _valid_library_path(value: Any) -> bool:
    text = str(value or "").replace("\\", "/").lstrip("/")
    if not LIBRARY_PATH_RE.fullmatch(text):
        return False
    return all(part not in {".", ".."} for part in text.split("/"))


def _hash_ok(value: Any) -> bool:
    return bool(HASH_RE.fullmatch(str(value or "")))


def validate_mirror_metadata(data: dict[str, Any], version_id: str) -> None:
    """方案三: strict structural validation for metadata without official hashes."""
    if not isinstance(data, dict) or str(data.get("id") or "") != str(version_id):
        raise LauncherError("镜像元数据版本 ID 不正确。")
    client = ((data.get("downloads") or {}).get("client") or {})
    if not _valid_metadata_url(client.get("url")) or not _hash_ok(client.get("sha1")):
        raise LauncherError("镜像元数据的客户端下载地址或哈希非法。")
    index = data.get("assetIndex") or {}
    if index:
        if not _valid_metadata_url(index.get("url")) or not _hash_ok(index.get("sha1")):
            raise LauncherError("镜像元数据的资源索引地址或哈希非法。")
        safe_component(str(index.get("id") or ""), "资源索引 ID")
    logging = ((data.get("logging") or {}).get("client") or {})
    log_file = logging.get("file") or {}
    if log_file:
        if not _valid_metadata_url(log_file.get("url")) or not _hash_ok(log_file.get("sha1")):
            raise LauncherError("镜像元数据的日志配置地址或哈希非法。")
        safe_component(str(log_file.get("id") or ""), "日志配置 ID")
    for lib in data.get("libraries") or []:
        if not isinstance(lib, dict):
            continue
        downloads = lib.get("downloads") or {}
        entries = [downloads.get("artifact") or {}]
        entries.extend((downloads.get("classifiers") or {}).values())
        for entry in entries:
            if not isinstance(entry, dict) or not entry:
                continue
            path = entry.get("path")
            if path and not _valid_library_path(path):
                raise LauncherError(f"镜像元数据包含非法库路径：{path!r}")
            if entry.get("url") and not _valid_metadata_url(entry.get("url")):
                raise LauncherError(f"镜像元数据包含非法库地址：{entry.get('url')!r}")
            if entry.get("sha1") and not _hash_ok(entry.get("sha1")):
                raise LauncherError(f"镜像元数据包含非法库哈希：{entry.get('sha1')!r}")


def validate_loader_profile(data: dict[str, Any], label: str) -> None:
    """Validate Fabric/Quilt profile structure and download URLs."""
    if not isinstance(data, dict) or not data.get("id"):
        raise LauncherError(f"{label} 安装配置格式不正确。")
    libraries = data.get("libraries")
    if not isinstance(libraries, list) or not libraries:
        raise LauncherError(f"{label} 安装配置没有库列表。")
    for library in libraries:
        if not isinstance(library, dict) or not library.get("name"):
            raise LauncherError(f"{label} 安装配置包含非法库条目。")
        url = library.get("url")
        if url and not _valid_metadata_url(url):
            raise LauncherError(f"{label} 安装配置包含非法库地址：{url!r}")
        sha1 = library.get("sha1")
        if sha1 and not _hash_ok(sha1):
            raise LauncherError(f"{label} 安装配置包含非法库哈希：{sha1!r}")


def _maven_path(name: str) -> str:
    raw = str(name or "").split("@", 1)[0]
    parts = raw.split(":")
    if len(parts) < 3:
        return ""
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = f"-{parts[3]}" if len(parts) >= 4 else ""
    return (group.replace(".", "/") + "/" + artifact + "/" + version + "/"
            + artifact + "-" + version + classifier + ".jar")


def _ensure_loader_hashes(data: dict[str, Any], label: str) -> None:
    """Fill missing Fabric/Quilt artifact hashes from their Maven .sha1 files."""
    for library in data.get("libraries") or []:
        if not isinstance(library, dict) or library.get("sha1"):
            continue
        base = str(library.get("url") or "").rstrip("/")
        path = _maven_path(str(library.get("name") or ""))
        if not base or not path:
            continue
        try:
            text = request_text(f"{base}/{path}.sha1", timeout=20,
                                context=f"获取 {label} 库哈希")
            match = re.search(r"\b[0-9a-fA-F]{40}\b", text)
            if not match:
                raise LauncherError(f"{label} 库缺少 SHA-1：{library.get('name')}")
            library["sha1"] = match.group(0).lower()
        except Exception as exc:
            raise LauncherError(f"无法校验 {label} 库 {library.get('name')}：{exc}") from exc


def validate_manifest(data: dict[str, Any]) -> None:
    versions = data.get("versions") if isinstance(data, dict) else None
    if not isinstance(versions, list) or not versions:
        raise LauncherError("镜像版本清单格式不正确。")
    for item in versions:
        if not isinstance(item, dict):
            raise LauncherError("镜像版本清单包含非法条目。")
        safe_component(str(item.get("id") or ""), "版本 ID")
        if not _valid_metadata_url(item.get("url")):
            raise LauncherError(f"镜像版本清单包含非法地址：{item.get('url')!r}")



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
        self._official_manifest: dict[str, Any] | None = None
        self._official_manifest_mtime = 0.0
        self.last_fallback_notice = ""

    @property
    def source(self) -> str:
        value = str(self.settings.get("download_source") or "auto")
        return "mirror_priority" if value == "mirror" else value

    def _manifest_urls(self) -> list[str]:
        # "mirror_only" never falls back to official. "mirror" prefers BMCLAPI
        # but can use official. "auto" prefers official and falls back to BMCL.
        if self.source == "mirror_only":
            return [MIRROR_MANIFEST]
        if self.source == "mirror_priority":
            return [MIRROR_MANIFEST, OFFICIAL_MANIFEST]
        if self.source == "official":
            return [OFFICIAL_MANIFEST]
        return [OFFICIAL_MANIFEST, MIRROR_MANIFEST]

    def get_manifest(self, *, force: bool = False) -> dict[str, Any]:
        if self._manifest and not force:
            age = time.time() - self._manifest_mtime
            if age < 300:
                return self._manifest
        cached = read_json(self._manifest_cache, None)
        cached_sources = {"Mojang", "本地缓存"}
        if self.source != "official":
            cached_sources.add("BMCLAPI")
        if isinstance(cached, dict) and cached.get("_source") not in cached_sources:
            cached = None
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
                    if "bmclapi" in url:
                        validate_manifest(data)
                        if self.source in {"auto", "official_priority"}:
                            self.last_fallback_notice = (
                                "官方元数据连接失败，已自动切换至第三方镜像获取版本信息。")
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

    def _official_version_url(self, version_id: str) -> str:
        """Resolve a version URL from an official manifest anchor.

        Never use the current (possibly BMCLAPI-backed) manifest here: that was
        the F-01 deadlock where a mirror URL was used to "verify" the mirror.
        """
        now = time.time()
        manifest = self._official_manifest
        if manifest is not None and now - self._official_manifest_mtime < 300:
            entries = manifest.get("versions") if isinstance(manifest, dict) else None
        elif isinstance(self._manifest, dict) and self._manifest.get("_source") == "Mojang":
            entries = self._manifest.get("versions")
            manifest = self._manifest
        else:
            try:
                data = request_json(OFFICIAL_MANIFEST, timeout=30,
                                    context="获取官方版本清单锚点",
                                    headers={"Accept": "application/json"})
                if isinstance(data, dict) and isinstance(data.get("versions"), list):
                    self._official_manifest = data
                    self._official_manifest_mtime = now
                    manifest = data
                    entries = data.get("versions")
                else:
                    entries = None
            except Exception as exc:
                LOG.warning("Official manifest anchor failed: %s", exc)
                entries = None
        for item in entries or []:
            if isinstance(item, dict) and str(item.get("id") or "") == version_id:
                url = str(item.get("url") or "")
                return url if _valid_metadata_url(url) else ""
        return ""

    def metadata(self, version_id: str, *, force: bool = False) -> dict[str, Any]:
        cached_path = _cache_versions_dir() / f"{version_id}.json"
        source_path = cached_path.with_suffix(".source")
        cached = read_json(cached_path, None)
        allow_mirror = self.source != "official"
        cached_sources = {"Mojang"}
        if allow_mirror:
            cached_sources.add("BMCLAPI")
        cached_sidecar = ""
        try:
            cached_sidecar = source_path.read_text(encoding="utf-8").strip()
        except OSError:
            pass
        trusted_cache = cached_sidecar in cached_sources
        if isinstance(cached, dict) and trusted_cache and not force:
            return cached
        mirror_url = f"https://bmclapi2.bangbang93.com/version/{version_id}/json"
        errors: list[str] = []

        def store(data: dict[str, Any], label: str) -> dict[str, Any]:
            write_json(cached_path, data)
            try:
                source_path.write_text(label, encoding="utf-8")
            except OSError:
                pass
            return data

        def fetch(url: str) -> dict[str, Any] | None:
            try:
                data = request_json(url, timeout=40,
                                    context=f"获取 {version_id} 元数据",
                                    headers={"Accept": "application/json"})
                if isinstance(data, dict) and data.get("id"):
                    return data
            except Exception as exc:
                errors.append(str(exc))
                LOG.warning("Version metadata failed (%s): %s", url, exc)
            return None

        def official_attempt() -> dict[str, Any] | None:
            official_url = self._official_version_url(version_id)
            if not official_url:
                return None
            official = fetch(official_url)
            if official is None:
                return None
            host = (urlparse(official_url).hostname or "").lower()
            label = "BMCLAPI" if "bmclapi" in host or "sakura" in host else "Mojang"
            return store(official, label)

        def mirror_attempt() -> dict[str, Any] | None:
            mirror = fetch(mirror_url)
            if mirror is None:
                return None
            try:
                validate_mirror_metadata(mirror, version_id)
            except LauncherError as exc:
                LOG.warning("BMCLAPI metadata rejected: %s", exc)
                return None
            return store(mirror, "BMCLAPI")

        if self.source == "mirror_only":
            mirror = mirror_attempt()
            if mirror is not None:
                LOG.warning("BMCLAPI metadata used in mirror-only mode (third-party trust)")
                return mirror
        elif self.source == "mirror_priority":
            mirror = mirror_attempt()
            if mirror is not None:
                return mirror
            official = official_attempt()
            if official is not None:
                return official
        else:
            official = official_attempt()
            if official is not None:
                return official
            if self.source == "official":
                raise NetworkError(
                    f"仅官方源模式无法从 Mojang 获取 Minecraft {version_id} 的安装信息。" +
                    (f"（{errors[-1][:160]}）" if errors else ""))
            mirror = mirror_attempt()
            if mirror is not None:
                LOG.warning("Official metadata unavailable; BMCLAPI fallback (third-party)")
                if self.source in {"auto", "official_priority"}:
                    self.last_fallback_notice = (
                        "官方元数据连接失败，已自动切换至第三方镜像获取版本信息。")
                return mirror

        # Fall back to an already installed JSON (local, trusted filesystem).
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
        if official and self.source != "mirror_only":
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
        if source == "mirror_only":
            return [mirror] if mirror != url else []
        if mirror == url:
            return [url]
        if source == "mirror_priority":
            return [mirror, url]
        return [url, mirror]


class LoaderService:
    def __init__(self, settings: Any) -> None:
        self.settings = settings

    @property
    def source(self) -> str:
        value = str(self.settings.get("download_source") or "auto")
        return "mirror_priority" if value == "mirror" else value

    def _meta_bases(self, kind: str) -> list[str]:
        if self.source == "mirror_only":
            return [FABRIC_META_MIRROR] if kind == "fabric" else [QUILT_META_MIRROR]
        if self.source == "mirror_priority":
            return ([FABRIC_META_MIRROR, FABRIC_META] if kind == "fabric"
                    else [QUILT_META_MIRROR, QUILT_META])
        if self.source == "official_priority":
            return ([FABRIC_META, FABRIC_META_MIRROR] if kind == "fabric"
                    else [QUILT_META, QUILT_META_MIRROR])
        return [FABRIC_META] if kind == "fabric" else [QUILT_META]

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
                    validate_loader_profile(data, "Fabric")
                    _ensure_loader_hashes(data, "Fabric")
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
                    validate_loader_profile(data, "Quilt")
                    _ensure_loader_hashes(data, "Quilt")
                    return data
            except Exception as exc:
                errors.append(str(exc))
        raise NetworkError("无法获取 Quilt 安装配置。" +
                           (f"（{errors[-1][:180]}）" if errors else ""))
