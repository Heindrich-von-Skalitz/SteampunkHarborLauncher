from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable

import requests

from ..constants import (
    MC_ENTITLEMENTS, MC_LOGIN, MC_PROFILE, USER_AGENT, XBL_AUTH, XSTS_AUTH,
)
from ..core.skins import model_from_variant, select_active_skin
from ..storage import Settings
from ..utils import CancelledError, LauncherError, iso_now
from .oauth_config import (
    ClientConfig, LIVE_PROVIDER, NO_CLIENT_ID_MESSAGE, PROVIDERS,
    infer_provider, normalize_client_id, resolve_client_config,
)

LOG = logging.getLogger("shl.auth")
SCOPE = PROVIDERS[LIVE_PROVIDER].scope


class MicrosoftAuthError(LauncherError):
    pass


def rps_ticket(oauth_access_token: str, client_id: str) -> str:
    """Build the Xbox RPS ticket with the prefix Microsoft expects.

    Title clients use ``t=``; Entra application GUIDs use ``d=``.  Using the
    wrong prefix makes ``user.auth.xboxlive.com`` answer HTTP 401.
    """

    is_title_client = bool(re.fullmatch(r"[0-9a-fA-F]{16}", client_id or ""))
    if oauth_access_token.startswith(("t=", "d=")):
        return oauth_access_token
    return ("t=" if is_title_client else "d=") + oauth_access_token


class DeviceCodePending(Exception):
    """Kept for callers that used the old polling API."""


class MicrosoftAuth:
    """Xbox Live / Minecraft authentication.

    Sign-in uses the Microsoft account device-code flow: the user opens the
    browser once, the code is filled in automatically, and the launcher polls
    for the token.  No MSAL, WAM broker, Azure tenant or redirect URI is
    required.  The client id is resolved from local configuration only; see
    :mod:`app.services.oauth_config`.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    # ------------------------------------------------------------------ config
    def configuration(self, client_id: str = "") -> ClientConfig:
        config = resolve_client_config(self.settings, explicit_client_id=client_id)
        if config.configured:
            try:
                normalize_client_id(config.client_id)
            except ValueError as exc:
                raise MicrosoftAuthError(str(exc)) from exc
        return config

    def has_credentials(self) -> bool:
        return self.configuration().configured

    # ------------------------------------------------------------------ HTTP
    def _post(self, url: str, *, data: dict[str, Any] | None = None,
              json: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
              timeout: float = 30, context: str = "") -> dict[str, Any]:
        try:
            response = self._session.post(url, data=data, json=json, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            raise MicrosoftAuthError(f"{context or '微软登录'}网络请求失败：{exc}") from exc
        if response.status_code >= 400:
            detail = ""
            try:
                body = response.json()
                if isinstance(body, dict):
                    xerr = body.get("XErr")
                    detail = (body.get("error_description") or body.get("Message")
                              or body.get("error") or (f"XErr={xerr}" if xerr else ""))
            except ValueError:
                detail = response.text[:200]
            raise MicrosoftAuthError(f"{context or '微软登录'}失败（HTTP {response.status_code}）{detail[:260]}")
        try:
            return response.json()
        except ValueError as exc:
            raise MicrosoftAuthError(f"{context or '微软登录'}返回了无法解析的数据。") from exc

    def _get(self, url: str, token: str, *, context: str = "") -> dict[str, Any]:
        try:
            response = self._session.get(
                url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        except requests.RequestException as exc:
            raise MicrosoftAuthError(f"{context or '请求'}失败：{exc}") from exc
        if response.status_code == 404:
            raise MicrosoftAuthError("Minecraft 档案不存在，请先在 minecraft.net 创建玩家档案。")
        if response.status_code >= 400:
            raise MicrosoftAuthError(f"{context or '请求'}失败（HTTP {response.status_code}）")
        try:
            return response.json()
        except ValueError as exc:
            raise MicrosoftAuthError("服务器返回了无法解析的数据。") from exc

    # ------------------------------------------------------------------ device flow
    def begin_device_flow(self, client_id: str = "") -> dict[str, Any]:
        config = self.configuration(client_id)
        if not config.configured:
            raise MicrosoftAuthError(NO_CLIENT_ID_MESSAGE)
        payload: dict[str, Any] = {
            "client_id": config.client_id,
            "scope": config.provider.scope,
        }
        if config.provider.is_live:
            # The legacy live.com device endpoint requires response_type; the
            # Entra consumers endpoint does not.
            payload["response_type"] = "device_code"
        data = self._post(
            config.provider.device_code_url,
            data=payload,
            context="获取微软登录设备码",
        )
        if not data.get("device_code") or not data.get("user_code"):
            raise MicrosoftAuthError("微软没有返回有效的设备码。")
        flow = dict(data)
        flow["provider"] = config.provider.key
        flow["client_id"] = config.client_id
        verification = str(flow.get("verification_uri") or flow.get("verification_url")
                           or config.provider.verification_url or "")
        if verification:
            flow["verification_uri"] = verification
            # Microsoft's consumer page accepts the code in the query string,
            # so the browser opens on the "enter code" step directly.
            from urllib.parse import quote

            flow["verification_uri_complete"] = (
                verification + ("&" if "?" in verification else "?") + "otc="
                + quote(str(flow["user_code"]), safe="")
            )
        return flow

    def poll_device_code(self, flow: dict[str, Any], client_id: str = "",
                         cancel: Callable[[], bool] | None = None) -> dict[str, Any]:
        cancel = cancel or (lambda: False)
        config = self.configuration(client_id or str(flow.get("client_id") or ""))
        provider_key = str(flow.get("provider") or "")
        if provider_key in PROVIDERS:
            # Preserve the provider selected when the device code was issued.
            config = ClientConfig(config.client_id, PROVIDERS[provider_key], config.source)
        if not config.configured:
            raise MicrosoftAuthError(NO_CLIENT_ID_MESSAGE)
        deadline = time.monotonic() + max(60, int(flow.get("expires_in") or 900))
        interval = max(2, int(flow.get("interval") or 5))
        while time.monotonic() < deadline:
            # Sleep in short slices so closing the dialog cancels promptly.
            slept = 0.0
            while slept < interval:
                if cancel():
                    raise CancelledError("已取消微软登录")
                time.sleep(min(0.5, interval - slept))
                slept += 0.5
            if cancel():
                raise CancelledError("已取消微软登录")
            try:
                response = self._session.post(
                    config.provider.token_url,
                    data={
                        "client_id": config.client_id,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "device_code": flow["device_code"],
                    },
                    timeout=30,
                )
            except requests.RequestException as exc:
                raise MicrosoftAuthError(f"等待微软登录时网络中断：{exc}") from exc
            if response.status_code < 400:
                try:
                    data = response.json()
                except ValueError as exc:
                    raise MicrosoftAuthError("微软返回了无效的令牌数据。") from exc
                if data.get("access_token"):
                    return data
            try:
                error = response.json()
            except ValueError:
                error = {"error": "unknown", "error_description": response.text[:200]}
            code = str(error.get("error") or "")
            if code == "authorization_pending":
                continue
            if code == "slow_down":
                interval += 5
                continue
            if code == "authorization_declined":
                raise MicrosoftAuthError("你在浏览器中取消了微软登录。")
            if code in {"expired_token", "bad_verification_code"}:
                raise MicrosoftAuthError("登录代码已过期，请重新开始微软登录。")
            description = error.get("error_description") or code or "未知错误"
            raise MicrosoftAuthError(f"微软登录失败：{description[:260]}")
        raise MicrosoftAuthError("等待微软登录超时，请重新尝试。")

    # ------------------------------------------------------------------ Xbox chain
    def _xbox_chain(self, oauth_access_token: str, client_id: str,
                    *, require_game: bool = True) -> dict[str, Any]:
        # Microsoft title clients (16 hex chars, e.g. the Minecraft title id)
        # use the "t=" RPS prefix.  Entra/Azure application GUIDs use "d=".
        # Using the wrong prefix is what produced the HTTP 401 at Xbox Live.
        rps = rps_ticket(oauth_access_token, client_id)
        try:
            xbl = self._post(
                XBL_AUTH,
                json={
                    "Properties": {
                        "AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com",
                        "RpsTicket": rps,
                    },
                    "RelyingParty": "http://auth.xboxlive.com", "TokenType": "JWT",
                },
                headers={"x-xbl-contract-version": "1"},
                context="Xbox Live 身份验证",
            )
        except MicrosoftAuthError as exc:
            if "invalid_grant" in str(exc) or "credentials" in str(exc).lower():
                raise MicrosoftAuthError("微软账号授权已失效，请重新登录。") from exc
            raise
        xbl_token = xbl.get("Token")
        if not xbl_token:
            raise MicrosoftAuthError("无法获取 Xbox Live 令牌。")
        try:
            xsts = self._post(
                XSTS_AUTH,
                json={
                    "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl_token]},
                    "RelyingParty": "rp://api.minecraftservices.com/", "TokenType": "JWT",
                },
                headers={"x-xbl-contract-version": "1"},
                context="Xbox Live 权限验证",
            )
        except MicrosoftAuthError as exc:
            text = str(exc)
            if "2148916227" in text:
                raise MicrosoftAuthError("该微软账号已被封禁。") from exc
            if "2148916233" in text:
                raise MicrosoftAuthError("该微软账号尚未注册 Xbox 账户，请先前往 xbox.com 注册。") from exc
            if "2148916235" in text:
                raise MicrosoftAuthError("该账号所在地区无法使用 Xbox Live，请检查网络或地区。") from exc
            if "2148916238" in text:
                raise MicrosoftAuthError("该账号年龄不足，需要监护人或修改出生日期后才能登录。") from exc
            raise
        xsts_token = xsts.get("Token")
        claims = xsts.get("DisplayClaims") or {}
        xui = claims.get("xui") or []
        uhs = xui[0].get("uhs") if xui else None
        xuid = xui[0].get("xid") if xui else ""
        if not xsts_token or not uhs:
            raise MicrosoftAuthError("无法获取 Xbox Live 安全令牌。")
        try:
            mc = self._post(
                MC_LOGIN,
                json={"identityToken": f"XBL3.0 x={uhs};{xsts_token}"},
                context="获取 Minecraft 访问令牌",
            )
        except MicrosoftAuthError as exc:
            text = str(exc)
            if "ACCOUNT_SUSPENDED" in text:
                raise MicrosoftAuthError("该账号已被微软封禁。") from exc
            if "403" in text:
                raise MicrosoftAuthError("当前 IP 的登录请求被拒绝，请关闭代理/加速器后重试。") from exc
            raise
        access_token = mc.get("access_token")
        if not access_token:
            raise MicrosoftAuthError("未能获取 Minecraft 访问令牌。")
        expires_in = int(mc.get("expires_in") or 86400)
        entitlements: dict[str, Any] = {}
        if require_game:
            try:
                entitlements = self._get(MC_ENTITLEMENTS, access_token, context="检查游戏所有权")
            except MicrosoftAuthError:
                entitlements = {}
            items = entitlements.get("items") if isinstance(entitlements, dict) else []
            if not items:
                raise MicrosoftAuthError(
                    "该微软账号没有查询到已购买的 Minecraft。\n"
                    "请确认使用的是已购买 Minecraft: Java & Bedrock Edition 或 Xbox Game Pass 权益的账号。"
                )
        profile = self._get(MC_PROFILE, access_token, context="读取 Minecraft 档案")
        name = str(profile.get("name") or "")
        uuid = str(profile.get("id") or "")
        if not name or not uuid:
            raise MicrosoftAuthError("Minecraft 档案信息不完整，可能尚未创建玩家档案。")
        active_skin = select_active_skin(profile.get("skins"))
        skin_url = str(active_skin.get("url") or "")
        skin_model = model_from_variant(active_skin.get("variant"))
        return {
            "access_token": access_token,
            "expires_at": int(time.time()) + expires_in,
            "name": name,
            "uuid": uuid,
            "xuid": xuid,
            "skin_url": skin_url,
            "skin_model": skin_model,
            "entitlements": len(entitlements.get("items") or []),
        }

    # ------------------------------------------------------------------ account
    def account_from_oauth(self, oauth: dict[str, Any], client_id: str,
                           *, require_game: bool = True,
                           provider: str = LIVE_PROVIDER) -> dict[str, Any]:
        oauth_access = str(oauth.get("access_token") or "")
        refresh = str(oauth.get("refresh_token") or "")
        if not oauth_access:
            raise MicrosoftAuthError("微软令牌缺少 access_token。")
        result = self._xbox_chain(oauth_access, client_id, require_game=require_game)
        uuid = result["uuid"]
        return {
            "id": "ms-" + uuid,
            "type": "microsoft",
            "name": result["name"],
            "uuid": uuid,
            "access_token": result["access_token"],
            "refresh_token": refresh,
            "oauth_access_token": oauth_access,
            "expires_at": result["expires_at"],
            "xuid": result["xuid"],
            "skin_url": result["skin_url"],
            "skin_model": result.get("skin_model") or "classic",
            "client_id": client_id,
            "oauth_provider": provider,
            "created_at": iso_now(),
            "last_refreshed": iso_now(),
        }

    def login_with_device_code(self, client_id: str = "",
                               progress: Callable[[dict[str, Any]], None] | None = None,
                               cancel: Callable[[], bool] | None = None) -> dict[str, Any]:
        config = self.configuration(client_id)
        if not config.configured:
            raise MicrosoftAuthError(NO_CLIENT_ID_MESSAGE)
        flow = self.begin_device_flow(config.client_id)
        if progress:
            progress({
                "phase": "device",
                "flow": flow,
                "config": {
                    "source": config.source,
                    "provider": config.provider.key,
                    "masked_client_id": config.masked_client_id(),
                },
            })
        oauth = self.poll_device_code(flow, config.client_id, cancel)
        if progress:
            progress({"phase": "account", "name": "正在验证 Minecraft 档案…"})
        account = self.account_from_oauth(
            oauth, config.client_id, provider=config.provider.key)
        if progress:
            progress({"phase": "done", "name": account["name"]})
        return account

    def refresh_account(self, account: dict[str, Any]) -> dict[str, Any]:
        client_id = str(account.get("client_id") or "")
        config = self.configuration(client_id)
        if not config.configured:
            raise MicrosoftAuthError(NO_CLIENT_ID_MESSAGE)
        provider_key = str(account.get("oauth_provider") or "")
        if not provider_key:
            # Accounts created by 26-alpha.4.x did not store the provider.
            provider_key = infer_provider(config.client_id)
        provider = PROVIDERS.get(provider_key, config.provider)
        refresh_token = str(account.get("refresh_token") or "")
        if not refresh_token:
            raise MicrosoftAuthError("此微软账号缺少刷新令牌，请重新登录。")
        try:
            response = self._session.post(
                provider.token_url,
                data={
                    "client_id": config.client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "scope": provider.scope,
                },
                timeout=30,
            )
        except requests.RequestException as exc:
            raise MicrosoftAuthError(f"刷新微软登录失败：{exc}") from exc
        if response.status_code >= 400:
            raise MicrosoftAuthError("微软登录已过期，请重新登录。")
        try:
            oauth = response.json()
        except ValueError as exc:
            raise MicrosoftAuthError("微软登录已过期，请重新登录。") from exc
        if not oauth.get("access_token"):
            raise MicrosoftAuthError("微软登录已过期，请重新登录。")
        if not oauth.get("refresh_token"):
            oauth["refresh_token"] = refresh_token
        updated = self.account_from_oauth(
            oauth, config.client_id, require_game=False, provider=provider.key)
        updated["created_at"] = account.get("created_at") or iso_now()
        return updated

    @staticmethod
    def expires_soon(account: dict[str, Any], margin: int = 1800) -> bool:
        try:
            expires = int(account.get("expires_at") or 0)
            return expires - margin <= int(time.time())
        except (TypeError, ValueError):
            return True
