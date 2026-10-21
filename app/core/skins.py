from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

from ..paths import data_dir

MODEL_CLASSIC = "classic"
MODEL_SLIM = "slim"
MODELS = (MODEL_CLASSIC, MODEL_SLIM)

MODEL_LABELS = {
    MODEL_CLASSIC: "Steve 经典模型",
    MODEL_SLIM: "Alex 纤细模型",
}


def model_for_seed(seed: str) -> str:
    """Deterministically map a player id/name to Steve (1) or Alex (0).

    The low bit of the first SHA-256 byte is used instead of Python's salted
    hash(), so the result is stable across processes and machines.
    """
    value = str(seed or "Player").encode("utf-8")
    bit = hashlib.sha256(value).digest()[0] & 1
    return MODEL_CLASSIC if bit == 1 else MODEL_SLIM


def account_seed(account: dict[str, Any] | None) -> str:
    if not account:
        return "Player"
    return str(account.get("uuid") or account.get("id") or account.get("name") or "Player")


def auto_model(account: dict[str, Any] | None) -> str:
    return model_for_seed(account_seed(account))


def normalize_model(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in MODELS else ""


def model_from_variant(value: object) -> str:
    """Mojang's profile API uses CLASSIC / SLIM variants."""
    text = str(value or "").strip().lower()
    return MODEL_SLIM if text == "slim" else MODEL_CLASSIC


def select_active_skin(skins: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Pick the ACTIVE skin object from a Minecraft profile payload."""
    rows = [s for s in (skins or []) if isinstance(s, dict)]
    return next((s for s in rows if str(s.get("state") or "").upper() == "ACTIVE"),
                rows[0] if rows else {})


def is_custom_skin(account: dict[str, Any] | None) -> bool:
    return bool(account and account.get("type") == "offline" and account.get("skin_file"))


def effective_model(account: dict[str, Any] | None,
                    override: str | None = None) -> str:
    chosen = normalize_model(override)
    if chosen:
        return chosen
    if not account:
        return MODEL_CLASSIC
    stored = normalize_model(account.get("skin_model"))
    if account.get("type") == "microsoft":
        # The profile API's active skin variant is authoritative. Legacy
        # accounts without a stored variant fall back to the classic model.
        return stored or MODEL_CLASSIC
    if is_custom_skin(account) and stored:
        return stored
    return auto_model(account)


def default_skin_path(model: str) -> Path:
    from ..paths import icons_dir
    chosen = normalize_model(model) or MODEL_CLASSIC
    return icons_dir() / ("steve.png" if chosen == MODEL_CLASSIC else "alex.png")


def _resolve_relative(value: str) -> Path | None:
    text = str(value or "").strip().replace("\\", "/")
    if not text or text.startswith("/") or ".." in text.split("/"):
        return None
    path = data_dir() / text
    try:
        path.resolve().relative_to(data_dir().resolve())
    except (OSError, ValueError):
        return None
    return path


def custom_skin_path(account: dict[str, Any] | None) -> Path | None:
    if not account:
        return None
    path = _resolve_relative(str(account.get("skin_file") or ""))
    return path if path and path.exists() else None


def effective_skin_path(account: dict[str, Any] | None) -> Path:
    custom = custom_skin_path(account)
    if custom:
        return custom
    return default_skin_path(effective_model(account))


def copy_custom_skin(account_id: str, source: str | Path) -> str:
    """Copy a validated skin into the launcher data folder.

    Returns the data-relative path stored in the account record.
    """
    src = Path(source)
    folder = data_dir() / "skins"
    folder.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch for ch in str(account_id) if ch.isalnum() or ch in "-_") or "player"
    target = folder / f"{safe}.png"
    try:
        same_file = src.resolve() == target.resolve()
    except OSError:
        same_file = False
    if not same_file:
        shutil.copyfile(src, target)
    return target.relative_to(data_dir()).as_posix()


def skin_label(account: dict[str, Any] | None) -> str:
    if not account:
        return "默认皮肤"
    if account.get("type") == "microsoft" and account.get("skin_url"):
        return "微软正版皮肤"
    if is_custom_skin(account):
        return "自定义皮肤"
    return "默认皮肤"


__all__ = [
    "MODEL_CLASSIC", "MODEL_SLIM", "MODELS", "MODEL_LABELS",
    "model_for_seed", "account_seed", "auto_model", "normalize_model",
    "model_from_variant", "select_active_skin", "effective_model", "effective_skin_path",
    "default_skin_path", "custom_skin_path", "copy_custom_skin",
    "is_custom_skin", "skin_label",
]
