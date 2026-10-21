"""Microsoft OAuth application configuration.

The source repository contains no Microsoft application id.  A distribution
build embeds one (supplied through the build environment) and, on first
launch, writes a normal local ``oauth.json`` for the player.  The file is
generated locally and is never shipped next to the exe.

The OAuth *device code*, access token and refresh token are always generated at
sign-in time; they are never pre-generated or baked into a build.

Resolution order (first match wins):

1. ``SHL_MICROSOFT_CLIENT_ID`` / ``SHL_MS_CLIENT_ID`` environment variable
2. the per-user ``oauth.json`` next to ``settings.json`` (or exe sidecar)
3. the application id embedded in a distribution build
4. the legacy ``microsoft_client_id`` setting

The default provider is the consumer Microsoft account device-code flow on
``login.live.com``.  It needs no Entra/Azure tenant, no broker redirect URI and
no MSAL runtime.  A build/operator may opt into the Entra consumer endpoint by
setting ``provider`` to ``consumers`` in the same configuration sources.

Microsoft requires a registered application id for OAuth.  It identifies the
launcher application; it is not a secret or a login credential.  Microsoft does
not provide anonymous dynamic client registration, so the id is supplied by the
launcher's distribution build and is only written to this machine's config.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..paths import data_dir, executable_dir

LOG = logging.getLogger("shl.auth")

CLIENT_ID_ENV_VARS = ("SHL_MICROSOFT_CLIENT_ID", "SHL_MS_CLIENT_ID")
PROVIDER_ENV_VAR = "SHL_MS_OAUTH_PROVIDER"
OAUTH_FILE_NAME = "oauth.json"

# Microsoft's public consumer device-code endpoints.  These are endpoints, not
# credentials; the client id always comes from configuration.
LIVE_PROVIDER = "live"
CONSUMERS_PROVIDER = "consumers"


@dataclass(frozen=True)
class OAuthProvider:
    key: str
    label: str
    device_code_url: str
    token_url: str
    scope: str
    verification_url: str = ""

    @property
    def is_live(self) -> bool:
        return self.key == LIVE_PROVIDER


PROVIDERS: dict[str, OAuthProvider] = {
    LIVE_PROVIDER: OAuthProvider(
        key=LIVE_PROVIDER,
        label="微软账户",
        device_code_url="https://login.live.com/oauth20_connect.srf",
        token_url="https://login.live.com/oauth20_token.srf",
        scope="service::user.auth.xboxlive.com::MBI_SSL",
        verification_url="https://www.microsoft.com/link",
    ),
    CONSUMERS_PROVIDER: OAuthProvider(
        key=CONSUMERS_PROVIDER,
        label="Microsoft Entra 消费者账户",
        device_code_url=(
            "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
        ),
        token_url="https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
        scope="XboxLive.signin offline_access",
        verification_url="https://microsoft.com/devicelogin",
    ),
}


@dataclass(frozen=True)
class ClientConfig:
    client_id: str
    provider: OAuthProvider
    source: str

    @property
    def configured(self) -> bool:
        return bool(self.client_id)

    def masked_client_id(self) -> str:
        value = self.client_id
        if len(value) <= 10:
            return value
        return f"{value[:6]}…{value[-4:]}"


NO_CLIENT_ID_MESSAGE = (
    "未找到微软登录应用配置。\n\n"
    "完整分发版会把应用标识编入 exe，并在首次启动时自动生成本机 oauth.json；"
    "设备代码仍然在点击登录时现场生成。\n\n"
    "如果你是自行构建：请设置环境变量 SHL_MICROSOFT_CLIENT_ID 后重新构建。\n"
    "玩家不需要、也不应该手动填写 Client ID。"
)


def oauth_file_path() -> Path:
    """Return the local ``oauth.json`` path.

    A sidecar file next to the exe is supported so a portable build can be
    configured without writing into ``%APPDATA%``.  It is read at sign-in time,
    never compiled into the exe.
    """

    candidates = (data_dir() / OAUTH_FILE_NAME, executable_dir() / OAUTH_FILE_NAME)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _env(name: str) -> str:
    value = os.environ.get(name)
    return value.strip() if isinstance(value, str) else ""


def _clean(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def normalize_provider(value: Any, *, default: str = LIVE_PROVIDER) -> str:
    key = _clean(value).lower()
    if key in {"", "default", "microsoft", "msa", "live"}:
        return LIVE_PROVIDER if key != default else default
    if key in {"azure", "entra", "consumers", "microsoftonline"}:
        return CONSUMERS_PROVIDER
    return default


def infer_provider(client_id: str) -> str:
    """Infer the endpoint for legacy settings that predate the provider field.

    Microsoft's consumer/Xbox client ids are 16 hex characters; Entra app
    registrations are GUIDs.  This keeps refresh working for 4.1 accounts
    without storing a hardcoded id anywhere.
    """

    value = _clean(client_id)
    if re.fullmatch(r"[0-9a-fA-F]{16}", value):
        return LIVE_PROVIDER
    return CONSUMERS_PROVIDER


def normalize_client_id(value: Any) -> str:
    """Return a trimmed client id or raise ``ValueError`` with a user hint."""

    client_id = _clean(value)
    if not client_id:
        raise ValueError("Client ID 不能为空。")
    if len(client_id) < 8 or len(client_id) > 128:
        raise ValueError("Client ID 长度不正确，请检查是否复制完整。")
    if re.search(r"\s", client_id):
        raise ValueError("Client ID 中不能包含空格或换行。")
    if not re.fullmatch(r"[A-Za-z0-9._~-]+", client_id):
        raise ValueError("Client ID 含有不支持的字符。")
    return client_id


def read_oauth_file(path: Path | None = None) -> dict[str, str]:
    target = path or oauth_file_path()
    try:
        if not target.exists():
            return {}
        raw = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        LOG.warning("Unable to read %s: %s", target, exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    values: dict[str, str] = {}
    for key in ("client_id", "provider"):
        value = _clean(raw.get(key))
        if value:
            values[key] = value
    return values


def write_oauth_file(client_id: str, provider: str = LIVE_PROVIDER,
                     path: Path | None = None) -> Path:
    """Persist the local client id; never called by packaging scripts."""

    target = path or oauth_file_path()
    payload = {
        "client_id": normalize_client_id(client_id),
        "provider": normalize_provider(provider),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    # Direct write: an atomic temp file can hang or fail on locked profile
    # directories protected by security software.
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return target


def build_config() -> dict[str, str]:
    """Read the application id injected into a distribution build.

    The value is supplied at build time and is never committed. It exists so a
    distributed exe can generate the local oauth.json on first launch without
    asking the player to type anything.
    """

    try:
        from .. import _build_config as generated  # type: ignore[attr-defined]
    except Exception:
        return {}
    return {
        key: _clean(getattr(generated, key, ""))
        for key in ("MICROSOFT_CLIENT_ID", "OAUTH_PROVIDER")
        if _clean(getattr(generated, key, ""))
    }


def resolve_client_config(settings: Any | None = None,
                          explicit_client_id: str = "",
                          explicit_provider: str = "") -> ClientConfig:
    """Resolve the client id from trusted local sources, never from source code."""

    provider_key = _env(PROVIDER_ENV_VAR)
    explicit = _clean(explicit_client_id)
    if explicit:
        provider_key = provider_key or explicit_provider or infer_provider(explicit)
        return ClientConfig(explicit, PROVIDERS[normalize_provider(provider_key)],
                            "手动输入")

    for name in CLIENT_ID_ENV_VARS:
        value = _env(name)
        if value:
            return ClientConfig(value, PROVIDERS[normalize_provider(provider_key)],
                                f"环境变量 {name}")

    local = read_oauth_file()
    if local.get("client_id"):
        provider_key = provider_key or local.get("provider", "") or infer_provider(local["client_id"])
        return ClientConfig(local["client_id"],
                            PROVIDERS[normalize_provider(provider_key)],
                            "本机 oauth.json")

    generated = build_config()
    if generated.get("MICROSOFT_CLIENT_ID"):
        provider_key = provider_key or generated.get("OAUTH_PROVIDER", "")
        return ClientConfig(generated["MICROSOFT_CLIENT_ID"],
                            PROVIDERS[normalize_provider(provider_key)],
                            "分发版内置应用")

    if settings is not None:
        legacy = _clean(settings.get("microsoft_client_id") if hasattr(settings, "get") else "")
        if legacy:
            stored_provider = _clean(settings.get("oauth_provider") if hasattr(settings, "get") else "")
            provider_key = provider_key or stored_provider or infer_provider(legacy)
            return ClientConfig(legacy,
                                PROVIDERS[normalize_provider(provider_key)],
                                "本机设置")

    return ClientConfig("", PROVIDERS[normalize_provider(provider_key)], "未配置")


def ensure_local_oauth(settings: Any | None = None) -> ClientConfig:
    """Create the local ``oauth.json`` automatically for a distribution.

    The first launch resolves the application id from the compiled build
    configuration (or environment), then writes a normal local config file.
    The file is never shipped with the exe.
    """

    config = resolve_client_config(settings)
    if not config.configured:
        return config
    target = data_dir() / OAUTH_FILE_NAME
    if not target.exists():
        try:
            write_oauth_file(config.client_id, config.provider.key, target)
        except (OSError, ValueError) as exc:
            LOG.warning("Unable to create local oauth.json: %s", exc)
    return config


def save_client_config(settings: Any, client_id: str,
                       provider: str = LIVE_PROVIDER) -> ClientConfig:
    """Save the id in the local settings and the portable oauth.json."""

    normalized = normalize_client_id(client_id)
    provider_key = normalize_provider(provider)
    if settings is not None:
        settings.set("microsoft_client_id", normalized, save=False)
        settings.set("oauth_provider", provider_key, save=False)
        settings.save()
    write_oauth_file(normalized, provider_key)
    return ClientConfig(normalized, PROVIDERS[provider_key], "本机设置")


__all__ = [
    "ClientConfig", "OAuthProvider", "PROVIDERS", "LIVE_PROVIDER",
    "CONSUMERS_PROVIDER", "CLIENT_ID_ENV_VARS", "NO_CLIENT_ID_MESSAGE",
    "oauth_file_path", "read_oauth_file", "write_oauth_file",
    "normalize_client_id", "normalize_provider", "infer_provider",
    "resolve_client_config", "save_client_config", "ensure_local_oauth",
    "build_config",
]
