from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .constants import USER_AGENT
from .paths import executable_dir, logs_dir


class LauncherError(Exception):
    """A user-presentable launcher error."""


class NetworkError(LauncherError):
    """Networking failure with a friendly message."""


class CancelledError(LauncherError):
    """The user cancelled a long-running job."""


class DownloadError(LauncherError):
    """A file could not be downloaded or failed verification."""


LOG = logging.getLogger("shl")

# Version/loader identifiers are single path components.  The ASCII set is
# deliberately strict; custom modpack names use safe_component() below.
_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

# Only HTTPS endpoints used by the launcher and by the supported modpack
# ecosystems are allowed.  Redirect targets are checked again in
# download_file().
ALLOWED_DOWNLOAD_HOSTS = frozenset({
    "piston-meta.mojang.com",
    "piston-data.mojang.com",
    "launcher.mojang.com",
    "launchermeta.mojang.com",
    "resources.download.minecraft.net",
    "libraries.minecraft.net",
    "launchercontent.mojang.com",
    "bmclapi2.bangbang93.com",
    "api.adoptium.net",
    "github.com",
    "api.github.com",
    "codeload.github.com",
    "raw.githubusercontent.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "api.modrinth.com",
    "cdn.modrinth.com",
    "cdn-raw.modrinth.com",
    "api.curseforge.com",
    "www.curseforge.com",
    "edge.forgecdn.net",
    "mediafilez.forgecdn.net",
    "media.forgecdn.net",
    "meta.fabricmc.net",
    "maven.fabricmc.net",
    "meta.quiltmc.org",
    "maven.quiltmc.org",
    "files.minecraftforge.net",
    "maven.minecraftforge.net",
    "maven.neoforged.net",
})


def validate_component(value: str, label: str = "标识") -> str:
    """Validate a Minecraft version/loader identifier used as a path part."""
    text = str(value or "").strip()
    if (not text or text in {".", ".."} or not _COMPONENT_RE.fullmatch(text)
            or text.casefold() in _WINDOWS_RESERVED
            or text.endswith((".", " "))):
        raise LauncherError(f"非法的{label}：{value!r}")
    return text


def safe_component(value: str, label: str = "名称") -> str:
    """Validate one path component, allowing Unicode/spaces for instances."""
    text = str(value or "").strip()
    if (not text or text in {".", ".."} or "\x00" in text
            or any(ch in text for ch in ("/", "\\", ":"))
            or text.endswith((".", " "))
            or text.casefold() in _WINDOWS_RESERVED):
        raise LauncherError(f"非法的{label}：{value!r}")
    return text


def is_allowed_download_host(host: str) -> bool:
    host = str(host or "").lower().rstrip(".")
    if host in ALLOWED_DOWNLOAD_HOSTS:
        return True
    # BMCLAPI rotates front-end hosts. Allow only bmclapiN.bangbang93.com and
    # the observed bmclapiN.sakura.sld.tw CDN, never an arbitrary subdomain.
    if re.fullmatch(r"bmclapi\d*\.bangbang93\.com", host):
        return True
    if re.fullmatch(r"bmclapi\d+\.sakura\.sld\.tw", host):
        return True
    return False


def validate_download_url(url: str) -> str:
    """Allow only HTTPS downloads from known launcher/ecosystem hosts."""
    parsed = urlparse(str(url or ""))
    if parsed.scheme != "https" or not parsed.hostname:
        raise DownloadError(f"只允许 HTTPS 下载：{url}")
    if parsed.username or parsed.password:
        raise DownloadError("下载地址不能包含用户名或密码")
    try:
        port = parsed.port
    except ValueError as exc:
        raise DownloadError(f"下载地址端口非法：{url}") from exc
    if port not in {None, 443}:
        raise DownloadError(f"下载地址端口不允许：{url}")
    host = parsed.hostname.lower().rstrip(".")
    if not is_allowed_download_host(host):
        raise DownloadError(f"下载地址不在白名单：{host}")
    return url


def setup_logging(debug: bool = False) -> None:
    logger = logging.getLogger("shl")
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    if logger.handlers:
        return

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S")
    candidates: list[Path] = []
    try:
        candidates.append(logs_dir() / "launcher.log")
    except OSError:
        pass
    candidates.append(Path(tempfile.gettempdir()) / "SHL-logs" / "launcher.log")
    try:
        candidates.append(executable_dir() / "SHL-logs" / "launcher.log")
    except OSError:
        pass
    try:
        candidates.append(Path.cwd() / "SHL-logs" / "launcher.log")
    except OSError:
        pass

    handler: logging.Handler | None = None
    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(path, encoding="utf-8")
            break
        except OSError:
            continue
    if handler is None:
        # A read-only or locked data directory must not prevent startup.
        handler = logging.NullHandler()
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    if debug and sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        logger.addHandler(console)
    logger.propagate = False


def app_log(level: int, message: str, *args: Any) -> None:
    logging.getLogger("shl").log(level, message, *args)


def make_session(total_retries: int = 3) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=total_retries,
        connect=total_retries,
        read=total_retries,
        backoff_factor=0.55,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=24, pool_maxsize=32)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"})
    return session


_SESSION: requests.Session | None = None


def get_session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = make_session()
    return _SESSION


def friendly_network_error(exc: BaseException, context: str = "") -> NetworkError:
    prefix = f"{context}失败：" if context else "网络请求失败："
    detail = str(exc) or exc.__class__.__name__
    if isinstance(exc, requests.Timeout):
        detail = "连接超时，请检查网络或代理设置。"
    elif isinstance(exc, requests.ConnectionError):
        detail = "无法连接到服务器，请检查网络或切换下载源。"
    else:
        detail = detail[:280]
    return NetworkError(prefix + detail)


def request_json(url: str, *, method: str = "GET", timeout: float = 25,
                 context: str = "", **kwargs: Any) -> Any:
    url = validate_download_url(url)
    try:
        response = get_session().request(method, url, timeout=timeout, **kwargs)
        validate_download_url(response.url)
        if response.status_code >= 400:
            body = response.text[:240].replace("\n", " ")
            raise NetworkError(f"{context or '请求'}失败（HTTP {response.status_code}）{body}")
        return response.json()
    except NetworkError:
        raise
    except (requests.RequestException, ValueError) as exc:
        raise friendly_network_error(exc, context) from exc


def request_text(url: str, *, timeout: float = 25, context: str = "", **kwargs: Any) -> str:
    url = validate_download_url(url)
    try:
        response = get_session().get(url, timeout=timeout, **kwargs)
        validate_download_url(response.url)
        response.raise_for_status()
        return response.text
    except requests.RequestException as exc:
        raise friendly_network_error(exc, context) from exc


def _temp_path(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    return target.with_name(target.name + f".part-{os.getpid()}")


def download_file(
    urls: str | Sequence[str],
    target: Path,
    *,
    sha1: str | None = None,
    sha256: str | None = None,
    sha512: str | None = None,
    size: int | None = None,
    timeout: float = 35,
    progress: Callable[[int, int], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    context: str = "",
    allow_missing_hash: bool = False,
) -> Path:
    """Download atomically, trying each candidate URL, and verify checksums."""
    if isinstance(urls, str):
        candidates = [urls]
    else:
        candidates = [u for u in urls if u]
    if (not allow_missing_hash and not any((sha1, sha256, sha512))
            and Path(target).suffix.lower() in {".jar", ".exe", ".zip", ".dll", ".msi", ".pyd"}):
        raise DownloadError(f"{context or target.name} 缺少校验哈希，已拒绝下载")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 0:
        if not sha1 and not sha256 and not sha512:
            return target
        if sha1 and not sha256 and not sha512 and sha1_file(target) == sha1.lower():
            return target
        if sha256 and not sha512 and sha256_file(target) == sha256.lower():
            return target
        if sha512 and sha512_file(target) == sha512.lower():
            return target
    tmp = _temp_path(target)
    errors: list[str] = []
    for url in candidates:
        if cancel and cancel():
            raise CancelledError("操作已取消")
        try:
            safe_url = validate_download_url(url)
            with get_session().get(safe_url, stream=True, timeout=timeout) as response:
                validate_download_url(response.url)
                if response.status_code >= 400:
                    errors.append(f"HTTP {response.status_code}")
                    continue
                total = int(response.headers.get("Content-Length") or size or 0)
                done = 0
                with open(tmp, "wb") as handle:
                    for chunk in response.iter_content(1024 * 128):
                        if cancel and cancel():
                            raise CancelledError("操作已取消")
                        if not chunk:
                            continue
                        handle.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total)
            if size and done < size * 0.98:
                errors.append(f"文件不完整（{done}/{size}）")
                tmp.unlink(missing_ok=True)
                continue
            if sha1:
                actual = sha1_file(tmp)
                if actual.lower() != sha1.lower():
                    errors.append(f"SHA-1 不匹配（{actual[:10]}）")
                    tmp.unlink(missing_ok=True)
                    continue
            if sha256:
                actual = sha256_file(tmp)
                if actual.lower() != sha256.lower():
                    errors.append(f"SHA-256 不匹配（{actual[:10]}）")
                    tmp.unlink(missing_ok=True)
                    continue
            if sha512:
                actual = sha512_file(tmp)
                if actual.lower() != sha512.lower():
                    errors.append(f"SHA-512 不匹配（{actual[:10]}）")
                    tmp.unlink(missing_ok=True)
                    continue
            os.replace(tmp, target)
            return target
        except CancelledError:
            tmp.unlink(missing_ok=True)
            raise
        except DownloadError as exc:
            errors.append(str(exc)[:120])
            tmp.unlink(missing_ok=True)
            continue
        except (requests.RequestException, OSError) as exc:
            errors.append(str(exc)[:120])
            tmp.unlink(missing_ok=True)
    tmp.unlink(missing_ok=True)
    message = "；".join(dict.fromkeys(errors[-3:])) or "未知网络错误"
    raise DownloadError(f"{context or target.name} 下载失败：{message}")


def sha1_file(path: Path, chunk_size: int = 1024 * 256) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_file(path: Path, chunk_size: int = 1024 * 256) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def sha512_file(path: Path, chunk_size: int = 1024 * 256) -> str:
    digest = hashlib.sha512()
    with open(path, "rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def write_json(path: Path, value: Any, *, atomic: bool = False) -> None:
    """Write JSON directly.

    Atomic temp-file replacement is available with ``atomic=True``, but is not
    the default: on Windows profiles protected by security software,
    ``tempfile.mkstemp`` can block while the launcher is starting.  Direct
    writes fail fast and are retried by the caller where it matters.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    if atomic:
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    else:
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)


def copy_tree_merge(src: Path, dst: Path) -> int:
    """Copy all files from src into dst without deleting existing files."""
    if not src.exists():
        return 0
    count = 0
    for root, dirs, files in os.walk(src):
        root_path = Path(root)
        rel = root_path.relative_to(src)
        out_dir = dst / rel
        out_dir.mkdir(parents=True, exist_ok=True)
        for filename in files:
            source = root_path / filename
            target = out_dir / filename
            try:
                shutil.copy2(source, target)
                count += 1
            except OSError:
                continue
    return count


def validate_zip_limits(archive: Any) -> None:
    infos = archive.infolist()
    if len(infos) > 20000:
        raise LauncherError("压缩包文件数量过多，已拒绝解压")
    if sum(int(info.file_size or 0) for info in infos) > 2 * 1024 * 1024 * 1024:
        raise LauncherError("压缩包解压后超过 2 GiB，已拒绝解压")


def copy_zip_member(archive: Any, info: Any, target: Path,
                    *, budget: list[int], limit: int = 2 * 1024 * 1024 * 1024) -> None:
    """Copy one ZIP member while enforcing the real uncompressed byte budget."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(info) as source, open(target, "wb") as destination:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            budget[0] += len(chunk)
            if budget[0] > limit:
                raise LauncherError("压缩包实际解压后超过 2 GiB，已拒绝解压")
            destination.write(chunk)


def safe_extract_zip(archive: Any, target: Path) -> None:
    target = target.resolve()
    validate_zip_limits(archive)
    budget = [0]
    for info in archive.infolist():
        member = (target / info.filename).resolve()
        if target != member and target not in member.parents:
            raise LauncherError(f"压缩包包含不安全路径：{info.filename}")
        if info.is_dir():
            member.mkdir(parents=True, exist_ok=True)
            continue
        copy_zip_member(archive, info, member, budget=budget)


def safe_child(base: Path, member: str) -> Path:
    candidate = (base / member.replace("\\", "/").lstrip("/")).resolve()
    base_resolved = base.resolve()
    if candidate != base_resolved and base_resolved not in candidate.parents:
        raise LauncherError(f"文件路径越界：{member}")
    return candidate


def open_path(path: Path | str) -> None:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"路径不存在：{path}")
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def select_file_native() -> str:
    """Kept out of core; UI uses QFileDialog. This is only a fallback."""
    return ""


def format_size(size: float | int | None) -> str:
    if not size:
        return "0 B"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}" if value < 10 else f"{value:.0f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def format_number(value: int | float | None) -> str:
    if value is None:
        return "0"
    value = float(value)
    if value >= 100_000_000:
        return f"{value / 100_000_000:.1f} 亿"
    if value >= 10_000:
        return f"{value / 10_000:.1f} 万"
    return f"{int(value):,}"


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def relative_time(value: str | datetime | int | float | None) -> str:
    if isinstance(value, (int, float)):
        if value <= 0:
            return "未知"
        try:
            dt = datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return "未知"
    else:
        dt = parse_iso(value) if isinstance(value, str) else value
    if not dt:
        return "未知"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    seconds = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()
    if seconds < 0:
        return "刚刚"
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{int(seconds // 60)} 分钟前"
    if seconds < 86400:
        return f"{int(seconds // 3600)} 小时前"
    if seconds < 86400 * 30:
        return f"{int(seconds // 86400)} 天前"
    if seconds < 86400 * 365:
        return f"{int(seconds // 86400 // 30)} 个月前"
    return f"{int(seconds // 86400 // 365)} 年前"


def human_duration(seconds: float | int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} 秒"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} 分 {sec} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分"


def split_cli_arguments(text: str) -> list[str]:
    """Split a Windows-ish argument string while preserving quoted values."""
    result: list[str] = []
    current: list[str] = []
    quoted = False
    quote_char = '"'
    escaped = False
    for char in text.strip():
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\" and quoted:
            current.append(char)
        elif char in "\"'":
            if quoted and char == quote_char:
                quoted = False
            elif not quoted:
                quoted = True
                quote_char = char
            else:
                current.append(char)
        elif char.isspace() and not quoted:
            if current:
                result.append("".join(current))
                current = []
        else:
            current.append(char)
    if current:
        result.append("".join(current))
    return result


def is_windows() -> bool:
    return sys.platform.startswith("win")


def os_name_for_rules() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "osx"
    return "linux"


def os_arch_for_rules() -> str:
    machine = os.environ.get("PROCESSOR_ARCHITECTURE", "").upper()
    if machine in {"AMD64", "X86_64", "ARM64"}:
        return "x86_64" if machine != "ARM64" else "arm64"
    return "x86"


def _rule_os_matches(rule_os: dict[str, Any] | None) -> bool:
    if not rule_os:
        return True
    name = rule_os.get("name")
    if name and name != os_name_for_rules():
        return False
    arch = rule_os.get("arch")
    if arch:
        current = os_arch_for_rules()
        if arch == "x86" and current not in {"x86", "x86_64"}:
            return False
        if arch == "x86_64" and current != "x86_64":
            return False
        if arch == "arm64" and current != "arm64":
            return False
    version = rule_os.get("version")
    if version:
        try:
            if not re.search(version, os.uname().release if hasattr(os, "uname") else os.environ.get("OS", "")):
                return False
        except re.error:
            return False
    return True


def rules_allow(rules: Iterable[dict[str, Any]] | None,
                features: dict[str, bool] | None = None) -> bool:
    if rules is None:
        return True
    rules = list(rules)
    if not rules:
        return True
    features = features or {}
    allowed = False
    for rule in rules:
        action = rule.get("action")
        os_rule = rule.get("os")
        feature_rule = rule.get("features")
        matches = _rule_os_matches(os_rule)
        if matches and feature_rule:
            matches = all(bool(features.get(key, False)) == bool(value)
                          for key, value in feature_rule.items())
        if matches:
            allowed = action == "allow"
    return allowed


def parse_java_version(output: str) -> tuple[str, int]:
    """Return (display version, major version) from `java -version` output."""
    text = output or ""
    match = re.search(r'(?:version|openjdk|runtime)\s+"?([0-9][0-9._\-a-zA-Z+]*)"?', text, re.I)
    raw = match.group(1) if match else ""
    if not raw:
        match = re.search(r"openjdk\s+([0-9][^\s]*)", text, re.I)
        raw = match.group(1) if match else ""
    if not raw:
        return ("未知", 0)
    parts = raw.replace("_", ".").split(".")
    try:
        first = int(parts[0])
    except (ValueError, IndexError):
        return (raw, 0)
    major = first if first >= 9 else (int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else first)
    return raw, major


def required_java_major(version_meta: dict[str, Any]) -> int:
    java_version = version_meta.get("javaVersion") or {}
    major = java_version.get("majorVersion")
    if isinstance(major, int) and major > 0:
        return major
    base_id = str(version_meta.get("id") or "")
    match = re.search(r"^1\.(\d+)", base_id)
    if match:
        number = int(match.group(1))
        if number >= 21:
            return 21
        if number >= 18:
            return 17
        if number >= 17:
            return 16
        return 8
    return 21


def java_requirement_label(major: int) -> str:
    return f"Java {major}+" if major else "任意 Java"


def normalize_version_id(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z._\-+ ]+", "_", value).strip()[:80] or "custom"


def short_path(path: Path | str) -> str:
    """Return an 8.3 path on Windows when the regular path contains non-ASCII."""
    text = str(Path(path).resolve())
    if not is_windows():
        return text
    if all(ord(c) < 128 for c in text):
        return text
    try:
        import ctypes
        from ctypes import wintypes

        get_short = ctypes.windll.kernel32.GetShortPathNameW
        get_short.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        get_short.restype = wintypes.DWORD
        needed = get_short(text, None, 0)
        if needed:
            buffer = ctypes.create_unicode_buffer(needed + 1)
            if get_short(text, buffer, needed + 1):
                return buffer.value
    except Exception:
        pass
    return text


def psutil_memory_mb() -> tuple[int, int]:
    try:
        import psutil
        memory = psutil.virtual_memory()
        return int(memory.total // (1024 * 1024)), int(memory.available // (1024 * 1024))
    except Exception:
        total = 8192
        try:
            if is_windows():
                import ctypes
                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                    ]
                status = MEMORYSTATUSEX()
                status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
                total = int(status.ullTotalPhys // (1024 * 1024))
                return total, int(status.ullAvailPhys // (1024 * 1024))
        except Exception:
            pass
        return total, total // 2


def run_hidden(command: Sequence[str], *, timeout: float = 8,
               cwd: str | Path | None = None) -> subprocess.CompletedProcess[str]:
    flags = 0
    if is_windows():
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return subprocess.run(
        list(command), cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        timeout=timeout, creationflags=flags, check=False,
    )


def truncate(text: str, length: int = 120) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= length else text[: length - 1].rstrip() + "…"


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def friendly_exception(exc: BaseException) -> str:
    if isinstance(exc, LauncherError):
        return str(exc)
    message = str(exc).strip()
    return message or exc.__class__.__name__


def is_probably_url(value: str) -> bool:
    return urlparse(value).scheme.lower() in {"http", "https"}


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 1000):
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{path.stem}-{int(time.time())}{path.suffix}")
