from __future__ import annotations

import copy
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..paths import data_dir
from ..storage import Settings
from ..utils import LauncherError, format_size, now_stamp, read_json, required_java_major
from ..constants import LOADER_NAMES

LOG = logging.getLogger("shl.minecraft")

LOADER_LIBRARY_HINTS: list[tuple[str, str, str]] = [
    ("neoforge", "net.neoforged:neoforge", "neoforge"),
    ("neoforge", "net.neoforged:forge", "neoforge"),
    ("forge", "net.minecraftforge:forge", "forge"),
    ("forge", "net.minecraftforge:fmlloader", "forge"),
    ("fabric", "net.fabricmc:fabric-loader", "fabric"),
    ("quilt", "org.quiltmc:quilt-loader", "quilt"),
    ("optifine", "optifine:OptiFine", "optifine"),
    ("optifine", "optifine:optifine", "optifine"),
    ("liteloader", "com.mumfrey:liteloader", "liteloader"),
]

@dataclass
class InstalledVersion:
    id: str
    path: Path
    json_path: Path
    data: dict[str, Any]
    base_version: str
    loaders: list[str] = field(default_factory=list)
    loader_versions: dict[str, str] = field(default_factory=dict)
    release_time: str = ""
    version_type: str = "release"
    java_major: int = 21
    inherits_from: str = ""
    main_class: str = ""
    last_played: str = ""
    display_name: str = ""
    exists: bool = True
    broken: bool = False
    error: str = ""
    override: dict[str, Any] = field(default_factory=dict)

    @property
    def loader_label(self) -> str:
        if not self.loaders:
            return "原版"
        return " + ".join(LOADER_NAMES.get(x, x.title()) for x in self.loaders)

    @property
    def primary_loader(self) -> str:
        for name in ("neoforge", "forge", "fabric", "quilt", "optifine", "liteloader"):
            if name in self.loaders:
                return name
        return "vanilla"

    @property
    def display_type(self) -> str:
        if self.version_type == "release":
            return "正式版"
        if self.version_type == "snapshot":
            return "快照"
        if self.version_type == "old_beta":
            return "旧测试版"
        if self.version_type == "old_alpha":
            return "旧远古版"
        return self.version_type or "自定义"

    @property
    def loader_versions_label(self) -> str:
        parts = []
        for loader in self.loaders:
            version = self.loader_versions.get(loader, "")
            parts.append(f"{LOADER_NAMES.get(loader, loader.title())} {version}".strip())
        return " · ".join(parts)

    @property
    def folder_name(self) -> str:
        return self.path.name

    @property
    def label(self) -> str:
        """Display name; may contain any Unicode characters."""

        return self.display_name or self.id


class MinecraftManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def root(self) -> Path:
        return self.settings.minecraft_dir

    @property
    def versions_dir(self) -> Path:
        return self.root / "versions"

    def ensure_root(self) -> None:
        self.settings.ensure_minecraft_dirs()

    # ------------------------------------------------------------------ inspect
    def _load_version_json(self, version_id: str) -> dict[str, Any]:
        json_path = self.versions_dir / version_id / f"{version_id}.json"
        data = read_json(json_path, None)
        if not isinstance(data, dict):
            raise LauncherError(f"版本 {version_id} 的 JSON 已损坏或不存在。")
        return data

    def resolve(self, version_id: str) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]]]:
        """Resolve inheritsFrom parents; returns merged metadata and the chain."""
        chain: list[tuple[str, dict[str, Any]]] = []
        visited: set[str] = set()
        current = version_id
        while current:
            if current in visited:
                raise LauncherError(f"版本继承出现循环：{current}")
            visited.add(current)
            data = self._load_version_json(current)
            chain.append((current, data))
            parent = data.get("inheritsFrom") or ""
            if not parent:
                break
            current = str(parent)
        merged: dict[str, Any] = {}
        libraries: list[dict[str, Any]] = []
        jvm_args: list[Any] = []
        game_args: list[Any] = []
        for index, (_, data) in enumerate(reversed(chain)):
            for key, value in data.items():
                if key in {"libraries", "arguments", "minecraftArguments", "inheritsFrom"}:
                    continue
                if key == "downloads" and index > 0:
                    # Child download records are not relevant; the base client
                    # jar is resolved separately.
                    continue
                merged[key] = copy.deepcopy(value)
            if isinstance(data.get("libraries"), list):
                libraries.extend(copy.deepcopy(data["libraries"]))
            arguments = data.get("arguments")
            if isinstance(arguments, dict):
                if isinstance(arguments.get("jvm"), list):
                    jvm_args.extend(copy.deepcopy(arguments["jvm"]))
                if isinstance(arguments.get("game"), list):
                    game_args.extend(copy.deepcopy(arguments["game"]))
            if isinstance(data.get("minecraftArguments"), str):
                merged["minecraftArguments"] = data["minecraftArguments"]
        merged["libraries"] = libraries
        if jvm_args or game_args:
            merged["arguments"] = {"jvm": jvm_args, "game": game_args}
        merged["id"] = version_id
        merged["_chain"] = [item[0] for item in chain]
        return merged, chain

    def inspect(self, version_id: str, *, include_profile: bool = True) -> InstalledVersion:
        version_dir = self.versions_dir / version_id
        json_path = version_dir / f"{version_id}.json"
        data = self._load_version_json(version_id)
        broken = False
        error = ""
        try:
            merged, chain = self.resolve(version_id)
        except LauncherError as exc:
            merged = data
            chain = [(version_id, data)]
            broken = True
            error = str(exc)
        loaders, loader_versions = self.detect_loaders(merged, chain)
        base_version = self.infer_base_version(version_id, chain, merged)
        release_time = str(merged.get("releaseTime") or merged.get("time") or "")
        version_type = str(merged.get("type") or "release")
        last = ""
        for record in self.settings.get("history", []):
            if isinstance(record, dict) and record.get("version") == version_id:
                last = str(record.get("time") or "")
                break
        profile = self.settings.version_profile(version_id) if include_profile else {}
        return InstalledVersion(
            id=version_id,
            path=version_dir,
            json_path=json_path,
            data=merged,
            base_version=base_version,
            loaders=loaders,
            loader_versions=loader_versions,
            release_time=release_time,
            version_type=version_type,
            java_major=required_java_major(merged),
            inherits_from=str(data.get("inheritsFrom") or ""),
            main_class=str(merged.get("mainClass") or ""),
            last_played=last,
            display_name=str(profile.get("display_name") or ""),
            broken=broken,
            error=error,
            override=profile,
        )

    def detect_loaders(self, merged: dict[str, Any],
                       chain: list[tuple[str, dict[str, Any]]]) -> tuple[list[str], dict[str, str]]:
        haystack = " ".join(str(merged.get("id") or "").lower() for _ in [0])
        haystack += " " + " ".join(version_id.lower() for version_id, _ in chain)
        for lib in merged.get("libraries", []):
            haystack += " " + str(lib.get("name", "")).lower()
        haystack += " " + str(merged.get("mainClass", "")).lower()
        found: list[str] = []
        versions: dict[str, str] = {}
        for loader, needle, key in LOADER_LIBRARY_HINTS:
            if needle.lower() in haystack or (loader == "forge" and "forge" in haystack
                                              and "neoforge" not in haystack):
                if key not in found:
                    found.append(key)
        # Explicit id checks cover profiles where the library got renamed.
        if "neoforge" in haystack:
            if "neoforge" not in found:
                found.insert(0, "neoforge")
        elif "fabric" in haystack and "quilt" not in haystack and "fabric" not in found:
            found.insert(0, "fabric")
        if "quilt" in haystack and "quilt" not in found:
            found.insert(0, "quilt")
        if "forge" in haystack and "neoforge" not in haystack and "forge" not in found:
            found.insert(0, "forge")
        # Pull versions from Maven coordinates.
        patterns = {
            "fabric": r"net\.fabricmc:fabric-loader:([^:\s\"']+)",
            "quilt": r"org\.quiltmc:quilt-loader:([^:\s\"']+)",
            "forge": r"net\.minecraftforge:(?:forge|fmlloader):([^:\s\"']+)",
            "neoforge": r"net\.neoforged:(?:neoforge|forge):([^:\s\"']+)",
            "optifine": r"optifine:(?:OptiFine|optifine):([^:\s\"']+)",
            "liteloader": r"com\.mumfrey:liteloader:([^:\s\"']+)",
        }
        import re
        for lib in merged.get("libraries", []):
            name = str(lib.get("name", ""))
            for key, pattern in patterns.items():
                match = re.search(pattern, name, re.I)
                if match:
                    version = match.group(1)
                    if key in {"forge", "neoforge"} and "-" in version:
                        # Forge coordinates are generally <mc>-<forge>.
                        parts = version.split("-")
                        if re.fullmatch(r"\d+\.\d+(?:\.\d+)?", parts[0]):
                            version = "-".join(parts[1:])
                    versions.setdefault(key, version)
        for key in found:
            if key not in versions:
                versions[key] = self._parse_loader_version_from_id(key, haystack)
        return found, versions

    @staticmethod
    def _parse_loader_version_from_id(loader: str, haystack: str) -> str:
        import re
        patterns = {
            "fabric": r"fabric[-_ ]?loader[-_ ]?([0-9][0-9A-Za-z.\-+]*)",
            "quilt": r"quilt[-_ ]?loader[-_ ]?([0-9][0-9A-Za-z.\-+]*)",
            "forge": r"forge[-_ ]?([0-9][0-9A-Za-z.\-+]*)",
            "neoforge": r"neoforge[-_ ]?([0-9][0-9A-Za-z.\-+]*)",
        }
        match = re.search(patterns.get(loader, r"([0-9.]+)"), haystack, re.I)
        return match.group(1) if match else ""

    def infer_base_version(self, version_id: str, chain: list[tuple[str, dict[str, Any]]],
                           merged: dict[str, Any]) -> str:
        for _, data in reversed(chain):
            if "inheritsFrom" not in data:
                id_value = str(data.get("id") or "")
                jar = str(data.get("jar") or "")
                if jar:
                    return jar
                if id_value:
                    return id_value
        import re
        match = re.search(r"(?:^|[-_ ])(\d+\.\d+(?:\.\d+)?)(?:$|[-_ +])", version_id)
        if match:
            return match.group(1)
        return version_id

    def scan(self) -> list[InstalledVersion]:
        result: list[InstalledVersion] = []
        if not self.versions_dir.exists():
            return result
        try:
            entries = sorted(self.versions_dir.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return result
        for entry in entries:
            if not entry.is_dir():
                continue
            json_path = entry / f"{entry.name}.json"
            if not json_path.exists():
                continue
            try:
                result.append(self.inspect(entry.name))
            except LauncherError as exc:
                LOG.warning("Skipping version %s: %s", entry.name, exc)
                result.append(InstalledVersion(
                    id=entry.name, path=entry, json_path=json_path, data={},
                    base_version=entry.name, broken=True, error=str(exc),
                ))
        def rank(item: InstalledVersion) -> tuple:
            last = item.last_played or ""
            release = item.release_time or ""
            return (last, release, item.id)
        result.sort(key=rank, reverse=True)
        return result

    def find(self, version_id: str | None = None) -> InstalledVersion | None:
        version_id = version_id or self.settings.selected_version
        if not version_id:
            return None
        try:
            return self.inspect(version_id)
        except LauncherError:
            return None

    def find_vanilla_version_ids(self) -> list[str]:
        ids = []
        for item in self.scan():
            if not item.loaders and item.version_type in {"release", "snapshot", "old_beta", "old_alpha"}:
                ids.append(item.id)
        return ids

    # ------------------------------------------------------------------ paths
    def is_isolated(self, version: InstalledVersion) -> bool:
        override = self.settings.isolation_override(version.id)
        if override == "on":
            return True
        if override == "off":
            return False
        version_mods = version.path / "mods"
        version_saves = version.path / "saves"
        if version_mods.exists() and any(
                p.is_file() and p.suffix.lower() in {".jar", ".disabled"} for p in version_mods.iterdir()):
            return True
        if version_saves.exists() and any(p.is_dir() for p in version_saves.iterdir()):
            return True
        mode = str(self.settings.get("isolation_mode") or "modded")
        if mode == "all":
            return True
        if mode == "non_release":
            return version.version_type != "release"
        if mode == "modded":
            has_mods = False
            if version_mods.exists():
                try:
                    has_mods = any(p.suffix.lower() in {".jar", ".disabled"} for p in version_mods.iterdir())
                except OSError:
                    pass
            return bool(version.loaders) or has_mods
        return False

    def game_dir(self, version: InstalledVersion) -> Path:
        return version.path if self.is_isolated(version) else self.root

    def mods_dir(self, version: InstalledVersion, *, create: bool = False) -> Path:
        path = self.game_dir(version) / "mods"
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def resourcepacks_dir(self, version: InstalledVersion, *, create: bool = False) -> Path:
        path = self.game_dir(version) / "resourcepacks"
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def shaderpacks_dir(self, version: InstalledVersion, *, create: bool = False) -> Path:
        path = self.game_dir(version) / "shaderpacks"
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def saves_dir(self, version: InstalledVersion, *, create: bool = False) -> Path:
        path = self.game_dir(version) / "saves"
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def logs_dir(self, version: InstalledVersion) -> Path:
        path = self.game_dir(version) / "logs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def version_folder(self, version_id: str) -> Path:
        return self.versions_dir / version_id

    def create_instance_folder(self, version_id: str) -> Path:
        path = self.versions_dir / version_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ------------------------------------------------------------------ delete
    def delete_version(self, version_id: str, *, move_to_trash: bool = True) -> Path:
        target = self.version_folder(version_id)
        if not target.exists():
            raise LauncherError(f"版本文件夹不存在：{target}")
        if move_to_trash:
            trash = data_dir() / "trash" / "versions" / f"{now_stamp()}-{version_id}"
            trash.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(trash))
            result = trash
        else:
            shutil.rmtree(target)
            result = target
        self.settings.forget_version(version_id)
        if self.settings.get("selected_version") == version_id:
            self.settings.set("selected_version", "")
        return result

    # ------------------------------------------------------------------ counts
    def content_counts(self, version: InstalledVersion | None) -> dict[str, int]:
        if not version:
            return {"mods": 0, "resourcepacks": 0, "shaderpacks": 0, "saves": 0}
        def count_files(path: Path, suffix: str | None = None) -> int:
            if not path.exists():
                return 0
            try:
                return sum(1 for p in path.iterdir()
                           if p.is_file() and (suffix is None or p.name.lower().endswith(suffix)))
            except OSError:
                return 0
        def count_entries(path: Path, suffixes: tuple[str, ...]) -> int:
            if not path.exists():
                return 0
            try:
                return sum(1 for p in path.iterdir()
                           if p.is_dir() or p.name.lower().endswith(suffixes))
            except OSError:
                return 0
        return {
            "mods": count_files(self.mods_dir(version), ".jar")
                    + count_files(self.mods_dir(version), ".jar.disabled"),
            "resourcepacks": count_entries(self.resourcepacks_dir(version), (".zip", ".jar")),
            "shaderpacks": count_entries(self.shaderpacks_dir(version), (".zip", ".jar")),
            "saves": sum(1 for p in self.saves_dir(version).glob("*") if p.is_dir()) if self.saves_dir(version).exists() else 0,
        }

    def version_size(self, version_id: str) -> int:
        root = self.version_folder(version_id)
        total = 0
        if not root.exists():
            return 0
        for folder, dirs, files in os.walk(root):
            for filename in files:
                try:
                    total += (Path(folder) / filename).stat().st_size
                except OSError:
                    pass
        return total

    def pretty_size(self, version_id: str) -> str:
        return format_size(self.version_size(version_id))

    # ------------------------------------------------------------------ launch files
    def client_jar(self, version: InstalledVersion) -> Path | None:
        candidates: list[Path] = []
        jar_id = str(version.data.get("jar") or "")
        if jar_id:
            candidates.append(self.versions_dir / jar_id / f"{jar_id}.jar")
        candidates.append(version.path / f"{version.id}.jar")
        candidates.append(self.versions_dir / version.base_version / f"{version.base_version}.jar")
        for _, chain_data in reversed([(x, self._safe_version_json(x)) for x in version.data.get("_chain", [])]):
            if not isinstance(chain_data, dict):
                continue
            downloads = chain_data.get("downloads") or {}
            client = downloads.get("client") or {}
            if client.get("url"):
                chain_id = str(chain_data.get("id") or "")
                if chain_id:
                    candidates.append(self.versions_dir / chain_id / f"{chain_id}.jar")
        for candidate in candidates:
            if candidate and candidate.exists() and candidate.stat().st_size > 0:
                return candidate
        for item in self.scan():
            if item.id == version.base_version:
                jar = item.path / f"{item.id}.jar"
                if jar.exists():
                    return jar
        return None

    def _safe_version_json(self, version_id: str) -> dict[str, Any]:
        data = read_json(self.versions_dir / version_id / f"{version_id}.json", {})
        return data if isinstance(data, dict) else {}

    def assets_index(self, version: InstalledVersion) -> dict[str, Any] | None:
        index = version.data.get("assetIndex") or {}
        index_id = index.get("id")
        if not index_id:
            return None
        data = read_json(self.root / "assets" / "indexes" / f"{index_id}.json", None)
        return data if isinstance(data, dict) else None
