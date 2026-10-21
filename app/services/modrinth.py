from __future__ import annotations

import json
import logging
from typing import Any, Callable
from urllib.parse import urljoin

from ..constants import MODRINTH_API, MODRINTH_SITE
from ..utils import LauncherError, NetworkError, download_file, request_json

LOG = logging.getLogger("shl.modrinth")

PROJECT_TYPES = {"mod", "modpack", "resourcepack", "shader", "datapack"}
LOADER_CATEGORIES = {"fabric", "quilt", "forge", "neoforge"}
SEARCH_ALIASES = {
    "机械动力": "create",
    "機械動力": "create",
    "钠": "sodium",
    "鋰": "lithium",
    "锂": "lithium",
    "铱": "iridium",
    "iris光影": "iris shaders",
    "应用能源": "applied energistics",
    "通用机械": "mekanism",
    "植物魔法": "botania",
    "匠魂": "tinkers construct",
}


class ModrinthError(LauncherError):
    pass


class ModrinthService:
    def __init__(self) -> None:
        self.headers = {"Accept": "application/json"}

    def search(self, query: str, *, project_type: str = "mod", game_version: str = "",
               loader: str = "", offset: int = 0, limit: int = 20,
               index: str = "relevance") -> dict[str, Any]:
        facets: list[list[str]] = [[f"project_type:{project_type}"]]
        query = SEARCH_ALIASES.get(query.strip(), query.strip()) or query
        if game_version:
            facets.append([f"versions:{game_version}"])
        if loader and project_type in {"mod", "modpack"}:
            facets.append([f"categories:{loader}"])
        params = {
            "query": query,
            "facets": json.dumps(facets, separators=(",", ":")),
            "index": index,
            "offset": max(0, offset),
            "limit": max(1, min(100, limit)),
        }
        try:
            return request_json(
                f"{MODRINTH_API}/search", timeout=30, params=params, headers=self.headers,
                context="搜索 Modrinth",
            )
        except Exception as exc:
            raise ModrinthError(f"无法连接 Modrinth：{exc}") from exc

    def project(self, project_id: str) -> dict[str, Any]:
        return request_json(f"{MODRINTH_API}/project/{project_id}", timeout=30,
                            headers=self.headers, context="读取 Modrinth 工程")

    def versions(self, project_id: str, *, game_version: str = "", loader: str = "") -> list[dict[str, Any]]:
        params: dict[str, str] = {}
        if game_version:
            params["game_versions"] = json.dumps([game_version])
        if loader:
            params["loaders"] = json.dumps([loader])
        try:
            data = request_json(
                f"{MODRINTH_API}/project/{project_id}/version",
                timeout=30, params=params, headers=self.headers, context="读取 Modrinth 版本",
            )
        except Exception as exc:
            raise ModrinthError(f"读取 Modrinth 文件版本失败：{exc}") from exc
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    def pick_version(self, versions: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not versions:
            return None
        with_files = [v for v in versions if v.get("files")]
        if not with_files:
            return versions[0]
        return max(with_files, key=lambda v: str(v.get("date_published") or ""))

    @staticmethod
    def primary_file(version: dict[str, Any]) -> dict[str, Any] | None:
        files = version.get("files") or []
        if not files:
            return None
        for file in files:
            if file.get("primary"):
                return file
        return files[0]

    @staticmethod
    def file_urls(file: dict[str, Any]) -> list[str]:
        urls = [str(u) for u in (file.get("urls") or []) if u]
        if not urls and file.get("url"):
            urls = [str(file["url"])]
        return urls

    def download(self, version: dict[str, Any], destination,
                 progress: Callable[[int, int], None] | None = None,
                 cancel: Callable[[], bool] | None = None):
        file = self.primary_file(version)
        if not file:
            raise ModrinthError("此版本没有可下载文件。")
        urls = self.file_urls(file)
        if not urls:
            raise ModrinthError("下载地址为空。")
        hashes = file.get("hashes") or {}
        sha1 = hashes.get("sha1")
        size = int(file.get("size") or 0)
        return download_file(
            urls, destination, sha1=sha1, size=size or None,
            progress=progress, cancel=cancel, context=str(file.get("filename") or "Modrinth 文件"),
        )

    @staticmethod
    def project_url(slug: str) -> str:
        return f"{MODRINTH_SITE}/project/{slug}"

    @staticmethod
    def project_type_label(project_type: str) -> str:
        return {
            "mod": "模组", "modpack": "整合包", "resourcepack": "资源包",
            "shader": "光影", "datapack": "数据包",
        }.get(project_type, project_type)

    @staticmethod
    def loader_for_instance(loaders: list[str]) -> str:
        for name in ("neoforge", "forge", "quilt", "fabric"):
            if name in loaders:
                return name
        return ""

    @staticmethod
    def normalize_loader(value: str) -> str:
        return value.lower() if value.lower() in LOADER_CATEGORIES else ""


def _invert_date(value: str) -> tuple:
    # Sort ISO timestamps descending without converting to datetime.
    return tuple(-ord(char) for char in value)
