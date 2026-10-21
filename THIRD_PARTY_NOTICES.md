# Third-Party Notices

This file records the third-party libraries used by the Steampunk Harbor
Launcher (机械冒险港启动器) and the origin of the Minecraft-related image assets
included with it.

## Minecraft assets and icons

- The application/brand icon, the six theme marks in `icons/`, and the built-in
  `icons/steve.png` / `icons/alex.png` default skins are Minecraft-related
  artwork supplied by the project owner for this launcher.
- Minecraft character artwork and game assets are copyright Mojang Studios /
  Microsoft. They are **not** relicensed under this project's GPL licence and
  are used only for the launcher's interface display.
- No Minecraft client, game assets, or third-party mod files are distributed in
  this repository. They are downloaded at the user's request from Mojang's
  public services.
- Player heads are rendered locally from Java Edition skin textures. Genuine
  player skins are downloaded at runtime from Minecraft Services and are not
  redistributed with the launcher.

## Libraries used by the launcher

### PyQt6 and Qt

- PyQt6: Riverbank Computing Limited, GPL-3.0 (or commercial licence).
  https://www.riverbankcomputing.com/software/pyqt/
- Qt 6: The Qt Company, LGPL-3.0 / GPL-3.0 / commercial.
  https://www.qt.io/

Because PyQt6 is used under its GPL option, the launcher's original code is
released under GPL-3.0. See `LICENSE`.

### minecraft-launcher-lib

- Project: minecraft-launcher-lib 8.0
- Author: Copyright (c) 2019-2025 JakobDev and contributors
- Upstream: https://codeberg.org/JakobDev/minecraft-launcher-lib
- Licence: BSD 2-Clause
- Included at: `vendor/minecraft_launcher_lib/`
- Licence text: `licenses/minecraft-launcher-lib-LICENSE.txt`

Used only for the Forge official install processor. NeoForge is installed by
this project directly against NeoForge's public Maven metadata and official
`--install-client` installer.

### Python packages

- `requests` — Apache License 2.0 — https://requests.readthedocs.io/
- `psutil` — BSD-3-Clause — https://github.com/giampaolo/psutil
- `Pillow` — HPND licence (asset-generation development tool only) —
  https://python-pillow.org/
- Python standard library — Python Software Foundation License.

### Nuitka

The Windows build is a single Nuitka 4.3.1 `--onefile` executable, produced with
the Apache-2.0 licensed Nuitka compiler. Nuitka is a build-time tool and is not
a runtime dependency of the published source.

## Data services

- Mojang Piston Meta / Minecraft services — Mojang Studios.
- Modrinth API — https://docs.modrinth.com/
- BMCLAPI — https://bmclapi2.bangbang93.com (community mirror).
- Fabric Meta — https://meta.fabricmc.net
- Quilt Meta — https://meta.quiltmc.org
- Eclipse Adoptium API — https://api.adoptium.net
- Microsoft identity platform / Xbox Live / Minecraft Services — Microsoft and
  Mojang Studios.

## Microsoft account sign-in

Microsoft sign-in uses the public OAuth 2.0 device-code flow over HTTPS with
the `requests` library. No MSAL, WAM broker, PyJWT, cryptography or other
Microsoft SDK is bundled. The OAuth client identifier is supplied by the person
building/running the launcher (environment variable or local `oauth.json`) and
is not part of this repository. Microsoft account passwords are entered only on
Microsoft's own web page; the launcher never sees them.
