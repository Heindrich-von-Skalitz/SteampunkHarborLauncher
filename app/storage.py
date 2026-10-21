from __future__ import annotations

import base64
import copy
import ctypes
import hashlib
import json
import logging
import os
import re
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

from .paths import (
    current_minecraft_dir, data_dir, default_minecraft_dir, infer_game_dir_mode,
    normalize_custom_minecraft_dir, official_minecraft_dir, settings_file,
    version_profiles_file,
)
from .utils import iso_now, now_stamp, psutil_memory_mb, read_json, write_json

LOG = logging.getLogger("shl")

SENSITIVE_ACCOUNT_KEYS = ("access_token", "refresh_token", "oauth_access_token")


def _protect_text(value: str, *, encrypt: bool) -> str:
    """Protect account tokens with Windows DPAPI; no-op elsewhere."""
    if not value or not isinstance(value, str):
        return value
    if not sys.platform.startswith("win"):
        return value
    if not encrypt and not value.startswith("dpapi:"):
        return value
    try:
        class DataBlob(ctypes.Structure):
            _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        if encrypt:
            raw = value.encode("utf-8")
            source = ctypes.create_string_buffer(raw)
            source_blob = DataBlob(len(raw), ctypes.cast(source, ctypes.POINTER(ctypes.c_byte)))
            output = DataBlob()
            crypt32.CryptProtectData.argtypes = [
                ctypes.POINTER(DataBlob), ctypes.c_wchar_p, ctypes.POINTER(DataBlob),
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(DataBlob)]
            crypt32.CryptProtectData.restype = ctypes.c_bool
            if not crypt32.CryptProtectData(
                    ctypes.byref(source_blob), "SteampunkHarborLauncher", None, None, None, 0,
                    ctypes.byref(output)):
                return value
            try:
                cipher = ctypes.string_at(output.pbData, output.cbData)
            finally:
                kernel32.LocalFree.argtypes = [ctypes.c_void_p]
                kernel32.LocalFree.restype = ctypes.c_void_p
                kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))
            return "dpapi:" + base64.b64encode(cipher).decode("ascii")
        cipher = base64.b64decode(value[len("dpapi:"):])
        raw_buffer = ctypes.create_string_buffer(cipher)
        source_blob = DataBlob(len(cipher), ctypes.cast(raw_buffer, ctypes.POINTER(ctypes.c_byte)))
        output = DataBlob()
        crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(DataBlob), ctypes.POINTER(ctypes.c_wchar_p), ctypes.POINTER(DataBlob),
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(DataBlob)]
        crypt32.CryptUnprotectData.restype = ctypes.c_bool
        if not crypt32.CryptUnprotectData(
                ctypes.byref(source_blob), None, None, None, None, 0, ctypes.byref(output)):
            return ""
        try:
            return ctypes.string_at(output.pbData, output.cbData).decode("utf-8")
        finally:
            kernel32.LocalFree.argtypes = [ctypes.c_void_p]
            kernel32.LocalFree.restype = ctypes.c_void_p
            kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))
    except Exception:
        return value if encrypt else ("" if str(value).startswith("dpapi:") else value)


def _account_for_disk(account: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(account)
    for key in SENSITIVE_ACCOUNT_KEYS:
        value = result.get(key)
        if isinstance(value, str) and value and not value.startswith("dpapi:"):
            result[key] = _protect_text(value, encrypt=True)
    return result


def _account_from_disk(account: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(account)
    for key in SENSITIVE_ACCOUNT_KEYS:
        value = result.get(key)
        if isinstance(value, str) and value.startswith("dpapi:"):
            result[key] = _protect_text(value, encrypt=False)
    return result


DEFAULT_PROFILE: dict[str, Any] = {
    "java_path": "",
    "memory_mode": "inherit",
    "memory_mb": 0,
    "isolation": "auto",
    "jvm_args": "",
    "game_args": "",
    "window_mode": "inherit",
    "width": 0,
    "height": 0,
    "server": "",
    "note": "",
    "display_name": "",
}


def automatic_memory_mb() -> int:
    total, available = psutil_memory_mb()
    # Keep enough room for the OS and other applications. High-RAM systems do
    # not need to assign everything to the JVM.
    target = int(total * 0.32)
    target = max(2048, min(8192, target))
    if available:
        target = min(target, max(2048, int(available * 0.75)))
    target = (target // 512) * 512
    return max(2048, min(target, max(2048, int(total * 0.82))))


def offline_uuid(name: str) -> str:
    digest = bytearray(hashlib.md5(("OfflinePlayer:" + name).encode("utf-8")).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30
    digest[8] = (digest[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(digest)).hex


def valid_player_name(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_]{3,16}", name or ""))


def make_offline_account(name: str) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex,
        "type": "offline",
        "name": name,
        "uuid": offline_uuid(name),
        "access_token": offline_uuid(name),
        "created_at": iso_now(),
        "expires_at": 0,
        "skin_url": "",
        "skin_file": "",
        "skin_model": "",
    }


class Settings:
    def __init__(self, path: Path | None = None, profiles_path: Path | None = None) -> None:
        self.path = Path(path) if path else settings_file()
        self.profiles_path = Path(profiles_path) if profiles_path else version_profiles_file()
        self._lock = threading.RLock()
        self.data = self._load()
        self.profiles = self._load_profiles()
        self._ensure_defaults()

    # ------------------------------------------------------------------ load
    def _load(self) -> dict[str, Any]:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        raw = read_json(self.path, None)
        if not isinstance(raw, dict):
            if self.path.exists():
                try:
                    self.path.replace(self.path.with_name(f"settings.corrupt-{now_stamp()}.json"))
                except OSError:
                    pass
            return {}
        accounts = raw.get("accounts")
        if isinstance(accounts, list):
            raw["accounts"] = [_account_from_disk(a) if isinstance(a, dict) else a for a in accounts]
        return raw

    def _load_profiles(self) -> dict[str, Any]:
        raw = read_json(self.profiles_path, {})
        if not isinstance(raw, dict):
            raw = {}
        profiles = raw.get("profiles") if isinstance(raw.get("profiles"), dict) else raw
        return profiles if isinstance(profiles, dict) else {}

    def _ensure_defaults(self) -> None:
        total, _ = psutil_memory_mb()
        # v2 moves the game directory from "next to the launcher" to the
        # standard %APPDATA%\.minecraft layout.
        legacy_layout = "storage_layout" not in self.data
        defaults: dict[str, Any] = {
            "config_version": 1,
            "storage_layout": 2,
            "theme": "creeper",
            "hero_art": "harbor",
            "minecraft_dir": str(default_minecraft_dir()),
            "minecraft_dir_mode": "official",
            "java_mode": "auto",
            "java_path": "",
            "memory_mode": "auto",
            "memory_mb": automatic_memory_mb(),
            "gc_mode": "auto",
            "jvm_args": "",
            "game_args": "",
            "window_mode": "default",
            "window_width": 1280,
            "window_height": 720,
            "launcher_window_width": 1368,
            "launcher_window_height": 882,
            "isolation_mode": "modded",
            "download_source": "auto",
            "parallel_downloads": 8,
            "selected_version": "",
            "last_version": "",
            "account_id": "",
            "accounts": [],
            "launcher_visibility": "keep",
            "open_console": "on_error",
            "show_snapshots": False,
            "reduce_motion": False,
            "launch_count": 0,
            "history": [],
            "last_page": "home",
            "window_geometry": "",
            "onboarded": False,
            "microsoft_client_id": "",
            "oauth_provider": "live",
            "curseforge_api_key": "",
            "server_quick_join": "",
            "system_ram_mb": total,
        }
        changed = False
        for key, value in defaults.items():
            if key not in self.data:
                self.data[key] = value
                changed = True
        if legacy_layout and str(self.data.get("minecraft_dir_mode") or "current") == "current":
            # Older releases used the executable folder itself. The portable
            # layout now gets its own .minecraft subfolder; files are not moved
            # or deleted during the upgrade.
            self.data["minecraft_dir"] = str(current_minecraft_dir())
            changed = True
        # An invalid / vanished game directory is repaired.
        game_dir = Path(str(self.data.get("minecraft_dir") or "")).expanduser()
        if not str(game_dir) or not game_dir.parent.exists():
            self.data["minecraft_dir"] = str(default_minecraft_dir())
            changed = True
        mode = str(self.data.get("minecraft_dir_mode") or "")
        if mode not in {"current", "official", "custom"}:
            mode = infer_game_dir_mode(self.minecraft_dir)
        # Every non-official location must be a .minecraft folder.
        if mode == "official":
            # default_minecraft_dir() honors SHL_MINECRAFT_DIR and TEST_MODE;
            # official_minecraft_dir() alone would ignore those overrides.
            target = default_minecraft_dir()
        elif mode == "current":
            target = current_minecraft_dir()
        else:
            target = normalize_custom_minecraft_dir(self.minecraft_dir)
        if self.minecraft_dir != target:
            self.data["minecraft_dir"] = str(target)
            changed = True
        if self.data.get("minecraft_dir_mode") != mode:
            self.data["minecraft_dir_mode"] = mode
            changed = True
        account_ids = {a.get("id") for a in self.data.get("accounts", []) if isinstance(a, dict)}
        if self.data.get("account_id") not in account_ids:
            self.data["account_id"] = next(iter(account_ids), "")
            changed = True
        if not isinstance(self.data.get("history"), list):
            self.data["history"] = []
            changed = True
        if changed:
            self.save()

    def save(self) -> None:
        with self._lock:
            try:
                disk_data = copy.deepcopy(self.data)
                accounts = disk_data.get("accounts")
                if isinstance(accounts, list):
                    disk_data["accounts"] = [
                        _account_for_disk(a) if isinstance(a, dict) else a for a in accounts]
                write_json(self.path, disk_data, atomic=False)
            except OSError as exc:
                # Read-only/locked profiles are common with security software.
                # Keep running with in-memory settings instead of refusing to
                # open the launcher.
                LOG.error("Unable to save settings: %s", exc)

    def save_profiles(self) -> None:
        with self._lock:
            try:
                write_json(self.profiles_path, {"profiles": self.profiles}, atomic=False)
            except OSError as exc:
                LOG.error("Unable to save version profiles: %s", exc)

    # ------------------------------------------------------------------ generic
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return copy.deepcopy(self.data.get(key, default))

    def set(self, key: str, value: Any, *, save: bool = True) -> None:
        with self._lock:
            if self.data.get(key) == value and key in self.data:
                return
            self.data[key] = copy.deepcopy(value)
            if save:
                self.save()

    def update(self, values: dict[str, Any], *, save: bool = True) -> None:
        with self._lock:
            self.data.update(values)
            if save:
                self.save()

    # ------------------------------------------------------------------ game dir
    @property
    def minecraft_dir(self) -> Path:
        return Path(str(self.data.get("minecraft_dir") or default_minecraft_dir())).expanduser()

    @minecraft_dir.setter
    def minecraft_dir(self, value: Path | str) -> None:
        self.set("minecraft_dir", str(Path(value)))

    def set_game_dir_mode(self, mode: str) -> Path:
        if mode == "current":
            path = current_minecraft_dir()
        elif mode == "official":
            path = official_minecraft_dir()
        else:
            path = normalize_custom_minecraft_dir(self.minecraft_dir)
        self.data["minecraft_dir_mode"] = mode if mode in {"current", "official", "custom"} else "custom"
        self.minecraft_dir = path
        return path

    def ensure_minecraft_dirs(self) -> None:
        for name in ("versions", "libraries", "assets", "assets/indexes", "assets/objects",
                     "mods", "resourcepacks", "shaderpacks", "saves", "logs"):
            try:
                (self.minecraft_dir / name).mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                # Read-only game folders are still valid for launching. The UI
                # surfaces a proper error if a specific action needs writing.
                LOG.debug("Could not create %s: %s", self.minecraft_dir / name, exc)

    # ------------------------------------------------------------------ accounts
    def accounts(self) -> list[dict[str, Any]]:
        accounts = self.data.get("accounts") or []
        return [a for a in accounts if isinstance(a, dict)]

    def active_account(self) -> dict[str, Any] | None:
        active_id = self.data.get("account_id")
        for account in self.accounts():
            if account.get("id") == active_id:
                return account
        accounts = self.accounts()
        return accounts[0] if accounts else None

    def set_active_account(self, account_id: str) -> None:
        self.set("account_id", account_id)

    def add_account(self, account: dict[str, Any], *, make_active: bool = True) -> None:
        with self._lock:
            accounts = [a for a in self.accounts() if a.get("id") != account.get("id")]
            # Prefer updating the same Microsoft UUID instead of creating dupes.
            if account.get("type") == "microsoft":
                accounts = [a for a in accounts
                            if not (a.get("type") == "microsoft"
                                    and (a.get("uuid") == account.get("uuid")
                                         or a.get("name", "").lower() == account.get("name", "").lower()))]
            accounts.insert(0, account)
            self.data["accounts"] = accounts
            if make_active or not self.data.get("account_id"):
                self.data["account_id"] = account.get("id")
            self.save()

    def update_account(self, account: dict[str, Any]) -> None:
        with self._lock:
            accounts = self.accounts()
            for index, existing in enumerate(accounts):
                if existing.get("id") == account.get("id"):
                    accounts[index] = account
                    break
            else:
                accounts.insert(0, account)
            self.data["accounts"] = accounts
            self.save()

    def remove_account(self, account_id: str) -> None:
        with self._lock:
            self.data["accounts"] = [a for a in self.accounts() if a.get("id") != account_id]
            if self.data.get("account_id") == account_id:
                remaining = self.data["accounts"]
                self.data["account_id"] = remaining[0].get("id") if remaining else ""
            self.save()

    # ------------------------------------------------------------------ versions
    def version_profile(self, version_id: str) -> dict[str, Any]:
        raw = self.profiles.get(version_id, {})
        profile = copy.deepcopy(DEFAULT_PROFILE)
        if isinstance(raw, dict):
            profile.update({k: v for k, v in raw.items() if k in DEFAULT_PROFILE})
        return profile

    def save_version_profile(self, version_id: str, values: dict[str, Any]) -> None:
        with self._lock:
            profile = self.version_profile(version_id)
            profile.update({k: v for k, v in values.items() if k in DEFAULT_PROFILE})
            self.profiles[version_id] = profile
            self.save_profiles()

    def forget_version(self, version_id: str) -> None:
        if version_id in self.profiles:
            self.profiles.pop(version_id, None)
            self.save_profiles()

    # ------------------------------------------------------------------ launch
    @property
    def selected_version(self) -> str:
        return str(self.data.get("selected_version") or self.data.get("last_version") or "")

    @selected_version.setter
    def selected_version(self, value: str) -> None:
        self.set("selected_version", value)

    def record_launch(self, version_id: str, *, pid: int = 0, success: bool = True,
                      note: str = "") -> None:
        with self._lock:
            self.data["launch_count"] = int(self.data.get("launch_count", 0)) + 1
            self.data["last_version"] = version_id
            self.data["selected_version"] = version_id
            history = list(self.data.get("history") or [])
            history.insert(0, {
                "version": version_id,
                "time": iso_now(),
                "pid": pid,
                "success": success,
                "note": note,
            })
            self.data["history"] = history[:80]
            self.save()

    def memory_mb_for(self, version_id: str) -> int:
        profile = self.version_profile(version_id)
        if profile.get("memory_mode") == "custom" and int(profile.get("memory_mb") or 0) > 0:
            return int(profile["memory_mb"])
        return int(self.data.get("memory_mb") or automatic_memory_mb())

    def isolation_override(self, version_id: str) -> str:
        value = self.version_profile(version_id).get("isolation", "auto")
        return value if value in {"auto", "on", "off"} else "auto"

    @property
    def total_ram_mb(self) -> int:
        return int(self.data.get("system_ram_mb") or psutil_memory_mb()[0])

    # ------------------------------------------------------------------ misc
    def add_history_note(self, version_id: str, note: str) -> None:
        record = {
            "version": version_id,
            "time": iso_now(),
            "pid": 0,
            "success": True,
            "note": note,
        }
        history = list(self.data.get("history") or [])
        history.insert(0, record)
        self.set("history", history[:80])

    def export_sanitized(self) -> dict[str, Any]:
        data = copy.deepcopy(self.data)
        data.pop("accounts", None)
        data.pop("microsoft_client_id", None)
        data.pop("curseforge_api_key", None)
        return data
