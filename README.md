# Homebound

An offline wormhole mapper for EVE Online that runs only on your machine. It follows your characters through the EVE Local chat logs instead of ESI. Signatures are pasted from the probe scanner. All map data stays in `~/.local/share/homebound/` (Windows: `%APPDATA%\Homebound\`).

Network access: once, on first start, Homebound downloads the EVE static data export and the community wormhole data (about 100 MB) and builds its universe data from them. After that the only network access is optional zKillboard activity for systems in visible chains, which can be turned off in Settings.

## Install

Releases on GitHub carry ready-made packages. They contain no EVE game data: on first start Homebound offers to download it and builds the universe data on your machine (about 100 MB, a minute or less).

- **Linux**: `Homebound-<version>-x86_64.AppImage`. Make it executable and run it; no install needed. Qt needs `libxcb-cursor0` (`xcb-util-cursor` on Arch), which Plasma systems have.
- **Arch / CachyOS**: the `homebound` AUR package, built from the release's `PKGBUILD`. It uses the system's `pyside6` and `python-httpx`.
- **Windows**: `Homebound-<version>-windows-setup.exe` installs per user (no admin rights) into `%LOCALAPPDATA%\Programs\Homebound`, with a Start-menu entry and optional autostart. `Homebound-<version>-windows-portable.zip` is the same folder to unpack anywhere. The builds aren't signed yet, so SmartScreen warns on the first start (*More info → Run anyway*).

`homebound --version` prints the version; Settings and the tray menu show it too.

## Run from source

Python 3.12 or newer and [uv](https://docs.astral.sh/uv/):

```sh
uv sync
uv run python -m homebound.universe.build_static   # one-time: EVE SDE + statics -> data/universe.sqlite
uv run python -m homebound
```

## First start

In EVE, *Settings → Chat → Log chat to file* must be on. On first start, pick the `Chatlogs` folder in Settings. Auto-detect checks the Steam/Proton prefix on Linux and the Documents folder (OneDrive included) on Windows.

For the overlay to follow the focused client on Plasma Wayland, open **Settings → KWin focus script → Install**. This installs a tiny KWin script that reports the active window title over D-Bus. Without it, Homebound falls back to X11 polling. On Windows focus tracking needs no setup, but EVE must run windowed or borderless: the overlay can't sit above exclusive fullscreen.

## Static data
- **EVE SDE** (Static Data Export): systems, classes, security, stargates, effects (via secondary sun type).
- **Wanderer** (MIT) `wormholeSystems.json` / `wormholes.json` and **anoik.is** `static.json`: statics and wormhole types.
- **whtype.info** `wormholes.js`: which classes each type can wander into, so the type dropdown only offers holes that can actually be there.
- Where Wanderer and anoik disagree on a system's statics, the build asks zKillboard's system page and pins the answer in `data/statics_resolved.json`.

## Using it

The full manual is in [docs/user-guide.md](docs/user-guide.md). In short:

- **Jumps** are read from Local logs.
  - A gate jump only moves the character.
  - A jump into or out of w-space adds the system and the connection.
  - A k-space → k-space jump without a gate (bridge, filament) asks with a toast.
- **Naming**: pick a scheme in Settings: numeric path `1 → 12 → 123`, `A1a / A2b`, or letter-led `A → A2 → A23`.
  - Chain letters follow EVE University's scheme: A (and B for a 2nd static) for the statics, then Z, Y, W, U, S, R, Q, O, M, K, I, E, D. C, F, G, H, J, L, N, P, T, V and X are never used.
  - The static is always hole 1. When it matters, Homebound asks "was this the static?" with its best guess preselected.
  - K-space systems show the scheme label plus the real name: `2 (Jita)`.
- **Bookmarks**: each WH sig has a copy button.
  - The hole with number *n* in system *S* is `S-n`, so the static is `S-1`.
  - The hole leading back home is `S-0`.
- **Sigs**: select a system, copy the probe scanner (Ctrl+A, Ctrl+C), then press Ctrl+V in Homebound.
  - Signatures and anomalies are grouped (Wormholes, Data, Gas …, Anomalies · Combat, Anomalies · Ore …). Click a group header to collapse it.
  - Sigs missing from a paste are flagged, and you choose Keep or Delete. Only the kinds in the paste are checked, so pasting just signatures leaves anomalies alone.
  - A system whose last paste is older than the rescan threshold (Settings → Signatures, 24h by default) gets a `SCAN 26h` tab and is hatched. A wormhole system never pasted shows `NO SCAN`. For an empty system, use right-click → *Mark as scanned now*.
  - Double-click a sig to enter what the scanner can't show: WH type (a dropdown of the types possible in that system, statics first), lifetime (stable, < 4h, < 1h, expired) and mass. For a K162 you also set where it leads and its size; any other type gives both. Once the hole is jumped, these carry over to the connection.
- **Undo**: collapsing a connection, removing a system and deleting sigs happen at once and show an Undo toast for 8 s (Ctrl+Z). Only deleting a whole map asks first.
  - Sig table columns can be resized by dragging the header edges.
- **Drawing by hand**: Shift+drag from one system to another (or right-click → *Draw connection from here*) adds a connection. Right-click the background → *Add system …* adds a system, with autocomplete.
  - Anything added by hand gets a dashed orange border and a ▲ UNCONFIRMED tag, and so do the systems at both ends of a hand-drawn connection. A character jumping the connection, or arriving in the system, confirms it. Right-click → *Mark as confirmed* does it by hand.
- **Keys**: Ctrl+V paste sigs · Ctrl+F find · Ctrl+Z undo · on the map F2 rename, Del remove, Ins add system, F fit chain, Enter edit connection, Shift+drag draw a connection.
- **Maps**: the ▦ menu in the toolbar switches between maps, or creates, renames and deletes them. Each map is its own file with its own home and naming scheme. Jumps are tracked on the active map.
- **Overlay** modes: Off · Follow active client · Pinned character over all clients · Pinned always.
  - A character in k-space counts as in a chain while within 5 gate jumps (configurable) of an entrance.
  - Unlock it from the tray to move it.
  - The overlay is an unmanaged X11 window, so it stays above fullscreen EVE clients.
- **Expiry**: connections are dropped after their type's lifetime (48h if unknown), or 4h / 1h / 10 min after being marked < 4h / < 1h / expired.

## Development
```sh
uv run pytest
uv run python -m homebound.logs.replay path/to/Local_*.txt --home J161940   # replay logs through the tracker
```

Code layout notes: signature scan parsing is `homebound/mapping/signature_parser.py`, the signature table `homebound/ui/signature_table.py`, the wormhole type/size/lifetime fields `homebound/ui/wormhole_fields.py` and the character list `homebound/ui/character_dock.py`. Names are spelled out (no abbreviations), every public entry point validates its arguments with `homebound/validation.py`, and `uv run pytest --cov=homebound` reports coverage. Known bugs and loose ends are in `TODO.md`.

Packages (details in [docs/plan-packaging.md](docs/plan-packaging.md)):
```sh
uv run --group packaging pyinstaller packaging/homebound.spec --noconfirm --workpath build/pyinstaller --distpath dist
packaging/linux/build_appimage.sh                                    # Linux: dist/Homebound-<version>-x86_64.AppImage
iscc /DAppVersion=<version> packaging\windows\homebound.iss          # Windows: dist\Homebound-<version>-windows-setup.exe
```
A `v*` tag runs `.github/workflows/release.yml`, which builds all of them on GitHub's runners and attaches them to a draft release. `test.yml` runs the tests on Linux and Windows for every push. The app icon is drawn in code; `packaging/make_icons.py` renders it to the PNG/SVG/ICO files.

Schema changes: the map database (settings keys, tables and columns were renamed to unabbreviated names, e.g. `log_dir` is now `log_directory`) and `data/universe.sqlite` changed. Old map files migrate automatically when they are opened. `universe.sqlite` must be rebuilt once with `uv run python -m homebound.universe.build_static`, which works offline from `data/cache`.

The UI follows `design-system/`: tokens, component specs and Qt notes.

## License

Homebound is free software under the [GNU General Public License v3.0 or later](LICENSE).

The licence covers Homebound's own code. Bundled third-party software, fonts and data keep their own terms; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). In short: Qt/PySide6 under LGPLv3, the EVE icons under the EVE Online terms of Fenris Creations (formerly CCP Games), and the fonts under OFL/MIT. The packages contain no EVE game data: Homebound downloads it and builds `universe.sqlite` on first start.
