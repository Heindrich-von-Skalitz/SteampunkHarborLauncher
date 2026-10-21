from __future__ import annotations

import logging
from typing import Any, Callable
from urllib.parse import quote

from ..utils import LauncherError, NetworkError, download_file, request_json
from .modrinth import SEARCH_ALIASES

LOG = logging.getLogger("shl.curseforge")

API = "https://api.curseforge.com/v1"
SITE = "https://www.curseforge.com"
CLASS_IDS = {"mod": 6, "modpack": 4471, "resourcepack": 12, "shader": 6552, "datapack": 6945}
CLASS_PATHS = {"mod": "mc-mods", "modpack": "modpacks", "resourcepack": "texture-packs",
               "shader": "shaders", "datapack": "data-packs"}
LOADER_IDS = {"forge": 1, "fabric": 4, "quilt": 5, "neoforge": 6}
LOADER_NAMES = {1: "forge", 4: "fabric", 5: "quilt", 6: "neoforge"}
SORT_FIELDS = {"relevance": 2, "downloads": 6, "newest": 3}


class CurseForgeError(LauncherError):
    pass


class CurseForgeService:
    """Minimal CurseForge API client.

    CurseForge requires an API key; without one the launcher keeps Modrinth
    search working and silently skips CurseForge results.
    """

    def __init__(self, settings: Any = None) -> None:
        self.settings = settings

    @property
    def api_key(self) -> str:
        import os

        return (os.environ.get("CURSEFORGE_API_KEY", "")
                or str(self.settings.get("curseforge_api_key") if self.settings else "")).strip()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise CurseForgeError("未配置 CurseForge API Key，无法搜索 CurseForge。")
        return {"Accept": "application/json", "x-api-key": self.api_key}

    def search(self, query: str, *, project_type: str = "mod", game_version: str = "",
               loader: str = "", offset: int = 0, limit: int = 20,
               index: str = "relevance") -> dict[str, Any]:
        params: dict[str, Any] = {
            "gameId": 432,
            "classId": CLASS_IDS.get(project_type, 6),
            "searchFilter": SEARCH_ALIASES.get(query.strip(), query.strip()) or query,
            "index": max(0, offset),
            "pageSize": max(1, min(50, limit)),
            "sortField": SORT_FIELDS.get(index, 2),
            "sortOrder": "desc",
        }
        if game_version:
            params["gameVersion"] = game_version
        loader_id = LOADER_IDS.get(loader)
        if loader_id:
            params["modLoaderType"] = loader_id
        try:
            data = request_json(
                f"{API}/mods/search", timeout=30, params=params,
                headers=self._headers(), context="搜索 CurseForge",
            )
        except Exception as exc:
            raise CurseForgeError(f"无法连接 CurseForge：{exc}") from exc
        items = data.get("data") if isinstance(data, dict) else None
        pagination = data.get("pagination") if isinstance(data, dict) else {}
        hits = [self._normalize(item, project_type)
                for item in (items or []) if isinstance(item, dict)]
        return {"hits": hits, "total_hits": int((pagination or {}).get("totalCount") or 0)}

    @staticmethod
    def _normalize(item: dict[str, Any], project_type: str) -> dict[str, Any]:
        loaders: set[str] = set()
        versions: set[str] = set()
        for entry in item.get("latestFilesIndexes") or []:
            if isinstance(entry, dict):
                name = LOADER_NAMES.get(int(entry.get("modLoader") or 0))
                if name:
                    loaders.add(name)
                if entry.get("gameVersion"):
                    versions.add(str(entry["gameVersion"]))
        authors = item.get("authors") or []
        author = ", ".join(str(a.get("name") or "") for a in authors if isinstance(a, dict))
        logo = item.get("logo") or {}
        links = item.get("links") or {}
        slug = str(item.get("slug") or item.get("id") or "")
        website = str(links.get("websiteUrl") or "")
        if not website and slug:
            website = f"{SITE}/minecraft/{CLASS_PATHS.get(project_type, 'mc-mods')}/{slug}"
        return {
            "source": "curseforge",
            "project_id": str(item.get("id") or ""),
            "slug": slug,
            "title": str(item.get("name") or ""),
            "description": str(item.get("summary") or ""),
            "author": author,
            "downloads": int(item.get("downloadCount") or 0),
            "follows": int(item.get("thumbsUpCount") or 0),
            "icon_url": str(logo.get("thumbnailUrl") or logo.get("url") or ""),
            "categories": sorted(loaders),
            "versions": sorted(versions, reverse=True),
            "website_url": website,
            "project_type": project_type,
        }

    def files(self, project_id: str, *, game_version: str = "",
              loader: str = "") -> list[dict[str, Any]]:
        params: dict[str, Any] = {"pageSize": 50}
        if game_version:
            params["gameVersion"] = game_version
        loader_id = LOADER_IDS.get(loader)
        if loader_id:
            params["modLoaderType"] = loader_id
        try:
            data = request_json(
                f"{API}/mods/{quote(str(project_id))}/files", timeout=30,
                params=params, headers=self._headers(), context="读取 CurseForge 文件",
            )
        except Exception as exc:
            raise CurseForgeError(f"读取 CurseForge 文件失败：{exc}") from exc
        items = data.get("data") if isinstance(data, dict) else None
        files = [item for item in (items or []) if isinstance(item, dict)]
        files.sort(key=lambda item: str(item.get("fileDate") or ""), reverse=True)
        return files

    @staticmethod
    def primary_file(files: list[dict[str, Any]]) -> dict[str, Any] | None:
        for item in files:
            if item.get("downloadUrl"):
                return item
        return files[0] if files else None

    @staticmethod
    def file_urls(file: dict[str, Any]) -> list[str]:
        url = str(file.get("downloadUrl") or "")
        if url:
            return [url]
        file_id = int(file.get("id") or 0)
        name = str(file.get("fileName") or "")
        if file_id and name:
            return [f"https://edge.forgecdn.net/files/{file_id // 1000}/{file_id % 1000}/{name}"]
        return []

    @staticmethod
    def sha1(file: dict[str, Any]) -> str:
        for item in file.get("hashes") or []:
            if isinstance(item, dict) and int(item.get("algo") or 0) == 1:
                return str(item.get("value") or "")
        return ""

    def download(self, file: dict[str, Any], destination, *,
                 progress: Callable[[int, int], None] | None = None,
                 cancel: Callable[[], bool] | None = None):
        urls = self.file_urls(file)
        if not urls:
            raise CurseForgeError("CurseForge 没有为这个文件提供下载地址。")
        return download_file(
            urls, destination, sha1=self.sha1(file),
            size=int(file.get("fileLength") or 0) or None,
            progress=progress, cancel=cancel,
            context=str(file.get("fileName") or "CurseForge 文件"),
        )

    @staticmethod
    def project_url(hit: dict[str, Any]) -> str:
        url = str(hit.get("website_url") or "")
        if url:
            return url
        slug = str(hit.get("slug") or "")
        kind = str(hit.get("project_type") or "mod")
        return f"{SITE}/minecraft/{CLASS_PATHS.get(kind, 'mc-mods')}/{slug}"

    @staticmethod
    def project_type_label(project_type: str) -> str:
        return {"mod": "模组", "modpack": "整合包", "resourcepack": "资源包",
                "shader": "光影", "datapack": "数据包"}.get(project_type, project_type)
