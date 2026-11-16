# Third-party notices

Homebound's own code is licensed under GPL-3.0-or-later (see `LICENSE`). The packages (AppImage,
Windows installer and zip, AUR package) also contain the software and data below, each under its own
terms.

## Software

| Component | Licence | Notes |
|---|---|---|
| Qt 6 / PySide6 (Qt for Python) | LGPL-3.0 | The Qt and PySide6 libraries ship as separate files in the package folder (PyInstaller one-folder mode) and can be replaced with other builds of the same version. Source: <https://code.qt.io>, <https://download.qt.io/official_releases/QtForPython/>. The AUR package uses the system's PySide6 instead. |
| Python | PSF License 2.0 | The interpreter bundled by PyInstaller. <https://docs.python.org/3/license.html> |
| PyInstaller bootloader | GPL-2.0-or-later with the bootloader exception | Only the small launcher executable. <https://pyinstaller.org/en/stable/license.html> |
| httpx, httpcore, h11, anyio, idna, certifi, sniffio | BSD-3-Clause / MIT / MPL-2.0 (certifi) | Used for zKillboard lookups. |

## Fonts

| Font | Licence | Licence file |
|---|---|---|
| Noto Sans (Regular, Medium, Bold, Black) | SIL Open Font License 1.1 | `homebound/theme/fonts/OFL-NotoSans.txt` |
| Hack (Regular, Bold) | MIT, plus the Bitstream Vera licence for the parts derived from it | `homebound/theme/fonts/LICENSE-Hack.txt` |

## Data

The packages contain no copy of the EVE Static Data Export. On first start Homebound downloads it and
builds `universe.sqlite` on the user's machine (`homebound --build-static` does the same from a terminal).
The wormhole statics and types come from the Homebound sync server, which merges the community sources
below daily, or from those sources directly; the packages ship a snapshot of that merge for when neither
answers.

| Data | Source | Terms |
|---|---|---|
| Systems, security, classes, stargates, wormhole effects | EVE Online Static Data Export (Fenris Creations), downloaded at first start | EVE Online terms for third-party developers. |
| Statics and wormhole types | Wanderer (`wanderer-industries/wanderer`), downloaded at first start | MIT for the repository's own files; the values come from EVE Online game data. |
| Statics (second source) | anoik.is `static.json`, downloaded at first start | Derived from EVE Online game data; no separate licence. |
| Where each wormhole type can wander | whtype.info `wormholes.js`, downloaded at first start | Derived from EVE Online game data; no separate licence. |
| Disputed statics (`statics_resolved.json`, shipped) | zKillboard system pages | Which of two sources is right for a handful of systems, looked up once and pinned. |
| Merged statics and wormhole types (`wormhole_data.json`, shipped; also served by the Homebound sync server) | Wanderer, anoik.is and whtype.info above, merged by Homebound | The terms of those sources; the values come from EVE Online game data. |
| Site bracket icons (`homebound/theme/sites/*.png`, shipped) | EVE Online Image Export Collection (Uprising V21.03) | EVE Online terms; see `homebound/theme/sites/NOTICE.md`. |

All EVE Online related materials are property of Fenris Creations (formerly CCP Games).

EVE Online, the EVE logo, EVE and all associated logos and designs are the intellectual property of Fenris Creations. All artwork, screenshots, characters, vehicles, storylines, world facts or other recognizable features of the intellectual property relating to these trademarks are likewise the intellectual property of Fenris Creations. EVE Online and the EVE logo are the registered trademarks of Fenris Creations. All rights are reserved worldwide. All other trademarks are the property of their respective owners.

Homebound is a free, non-commercial tool and is not affiliated with or endorsed by Fenris Creations.
