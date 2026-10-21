from __future__ import annotations

APP_NAME = "机械冒险港启动器"
APP_NAME_EN = "Steampunk Harbor Launcher"
APP_BUILD_NAME = "SHL"
APP_ID = "steampunk-harbor-launcher"
APP_VERSION = "26-alpha-B.9"
APP_BUILD = "2026.10.21"

# Developer-only test mode. Change this line in source to True when testing:
# launcher data goes directly to the project root and the game goes to
# <project>/test_game instead of AppData. It is intentionally not exposed in
# the UI, settings, command line or environment variables.
TEST_MODE = False
APP_AUTHOR = "机械冒险港工坊"
APP_DESCRIPTION = "Minecraft 版本、玩家、Java 与内容管理。"

USER_AGENT = f"{APP_NAME_EN}/{APP_VERSION}"

OFFICIAL_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
MIRROR_MANIFEST = "https://bmclapi2.bangbang93.com/mc/game/version_manifest.json"
ASSETS_BASE = "https://resources.download.minecraft.net"
LIBRARIES_BASE = "https://libraries.minecraft.net"
PISTON_BASE = "https://piston-meta.mojang.com"

FABRIC_META = "https://meta.fabricmc.net/v2"
FABRIC_META_MIRROR = "https://bmclapi2.bangbang93.com/fabric-meta/v2"
QUILT_META = "https://meta.quiltmc.org/v3"
QUILT_META_MIRROR = "https://bmclapi2.bangbang93.com/quilt-meta/v3"

FORGE_MAVEN = "https://maven.minecraftforge.net"
FORGE_PROMOTIONS = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
NEOFORGE_MAVEN = "https://maven.neoforged.net"
NEOFORGE_VERSIONS = "https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge"

MODRINTH_API = "https://api.modrinth.com/v2"
MODRINTH_SITE = "https://modrinth.com"
ADOPTIUM_API = "https://api.adoptium.net/v3"
ADOPTIUM_ASSETS = "https://api.adoptium.net/v3/assets/latest/21/hotspot?architecture=x64&image_type=jre&os=windows&vendor=eclipse"

XBL_AUTH = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_AUTH = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_LOGIN = "https://api.minecraftservices.com/authentication/login_with_xbox"
MC_ENTITLEMENTS = "https://api.minecraftservices.com/entitlements/mcstore"
MC_PROFILE = "https://api.minecraftservices.com/minecraft/profile"

# Microsoft OAuth application ids are never stored in this repository or
# compiled into the exe. See app/services/oauth_config.py and docs/LOGIN.md for
# the runtime (environment / local oauth.json) configuration sources.

THEME_NAMES = {
    "creeper": "苦力怕绿",
    "ocean": "海洋蓝",
    "redstone": "红石暖",
    "amethyst": "紫水晶",
    "graphite": "石墨灰",
    "ivory": "象牙白",
}

LOADER_NAMES = {
    "vanilla": "原版",
    "fabric": "Fabric",
    "quilt": "Quilt",
    "forge": "Forge",
    "neoforge": "NeoForge",
    "optifine": "OptiFine",
    "liteloader": "LiteLoader",
}

GAME_TYPES = {"release": "正式版", "snapshot": "快照", "old_beta": "旧测试版", "old_alpha": "旧远古版"}
