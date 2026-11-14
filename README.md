# Homebound

- **Audience:** people who find Homebound: players and contributors.
- **Scope:** what Homebound is, the one-line install, a feature overview, and the developer entry points. Everything else is linked: the [user guide](docs/user/README.md), the [doc map](docs/README.md).
- **Kept current:** checked against `8c371b5` (2026-11-14). Update it when the feature list, the install command or the development commands change.

An offline wormhole mapper for EVE Online. Homebound follows your characters through EVE's Local chat logs, builds the chain as you jump, names new systems with the scheme you pick, and shows the chain your active character is in on a small overlay over the game. It needs no ESI login, and all map data stays on your machine.

Runs on Linux and Windows.

## Install

On Linux, paste this into a terminal. It installs what the AppImage needs, downloads the latest release and adds Homebound to your app menu:

```sh
curl -fsSL https://raw.githubusercontent.com/luto2/homebound/master/packaging/linux/install.sh | sh
```

On Windows, download `Homebound-<version>-windows-setup.exe` from the [releases](https://github.com/luto2/homebound/releases). [Install and first start](docs/user/install.md) has the details, the uninstall command, the portable build and the unsigned-build warning.

![Homebound's main window with a small chain out of a C5 home](docs/images/main-window.png)

## What it does

- **Maps jumps from the Local logs.** A gate jump only moves the character. A jump into or out of w-space adds the system and the connection. A k-space jump without a gate (bridge, filament) asks first.
- **Names systems for you.** Numeric path (`1 → 12 → 123`), `A1a / A2b`, or letter-led (`A → A2 → A23`), with EVE University's chain letters. When the name depends on it, Homebound asks "was this the static?" with its best guess preselected.
- **Keeps your signatures.** Paste the probe scanner with Ctrl+V. New sigs are marked, missing ones are flagged so you can keep or delete them, and systems that are due for a rescan are hatched on the map.
- **Tracks hole state.** Mass, lifetime, type and size per connection, drawn on the line. Holes expire on their own.
- **Shows the chain in game.** The overlay follows the focused EVE client, or stays pinned to one character.
- **Plans routes** to the trade hubs and your own destinations, through stargates, your mapped holes and EVE-Scout's public Thera and Turnur connections.
- **Backs itself up.** One zip a day, plus one before anything risky. Removing a system, a connection or sigs shows an Undo toast for 8 seconds.

<p>
  <img src="docs/images/overlay.png" alt="The overlay following a character" width="330">
  <img src="docs/images/signature-editor.png" alt="The signature editor" width="400">
</p>

## Getting started

1. In EVE, turn on *Settings → Chat → Log chat to file*.
2. Start Homebound. Settings opens on **Log folder**: click **Auto-detect** or browse to your `Chatlogs` folder.
3. Set your home system and pick a naming scheme.
4. Undock and jump. To add sigs, select a system, press Ctrl+A and Ctrl+C in the probe scanner, then Ctrl+V in Homebound.

**The [user guide](docs/user/README.md) is the full manual**: naming and bookmarks, the signature table, editing the map by hand, several maps, the overlay modes, settings, keys, backups and troubleshooting. After the one-time data download Homebound works offline; the two optional lookups it makes are listed in [Where your data lives](docs/user/data.md).

## Static data

- **EVE SDE** (Static Data Export): systems, classes, security, stargates, effects (via the secondary sun type).
- **Wanderer** (MIT) `wormholeSystems.json` / `wormholes.json` and **anoik.is** `static.json`: statics and wormhole types.
- **whtype.info** `wormholes.js`: which classes each type can wander into, so the type dropdown only offers holes that can actually be there.
- Where Wanderer and anoik disagree on a system's statics, the build asks zKillboard's system page and pins the answer in `data/statics_resolved.json`.

## Development

Run from source: [Install and first start](docs/user/install.md#from-source). Then:

```sh
uv run pytest                     # --cov=homebound for coverage
uv run mypy                       # type check of the homebound package (settings in pyproject.toml)
python3 tools/check_docs.py       # dead links and missing header blocks in the docs
uv run python -m homebound.logs.replay path/to/Local_*.txt --home J161940   # replay logs through the tracker
```

- Only one Homebound runs per data folder: a second start raises the running window and exits with code 0. To run two on purpose (sync tests), start the second with `python -m homebound --allow-second-instance`, ideally with its own `HOMEBOUND_HOME` so the two don't share `settings.json` and map files. Each process signs its sync ops with its own connection ID.
- `homebound/logs/` reads the chat logs, `homebound/mapping/` turns jumps into the map (tracker, naming, signature parsing), `homebound/mapdb/` stores it, `homebound/ui/` is the PySide6 interface.
- Names are spelled out (no abbreviations), and every public entry point checks its arguments with `homebound/validation.py`.
- The UI follows `design-system/`: tokens, component specs and Qt notes.
- Known bugs and loose ends go in `TODO.md`. Design plans are in [docs/plans/](docs/plans/) (open ones at the top, finished ones in [done/](docs/plans/done/)); [docs/code-map.md](docs/code-map.md) says where each feature lives in the code.

**Git hooks.** A fresh clone must point git at the repo's hooks, or neither the push gate nor the graph refresh runs. Check the hooks path setting (`git config --get` with the key `core.hooksPath`): it should print `.githooks`. If it prints nothing, set it to `.githooks`.

**Type check.** `uv run mypy` checks the `homebound` package (settings in `pyproject.toml`).

**Using the graph.** `graphify-out/` is not tracked (its `graph.html` alone outweighed the Python and turned GitHub's language bar to HTML). The pre-push hook refreshes the local graph in the background and writes its output to `graphify-out/refresh.log`. On a fresh clone, build it once with `~/.local/bin/graphify update .`. Caveats:

- A missing incoming edge is not dead code. Method calls on other objects are mostly not extracted, so check with a text search before deleting.
- Edges with `relation == "uses"` are mostly false (imports get attached to every class in a file). Skip them in `graph.json` before trusting a god node rank or a bridge score.
- Docstring nodes are about half of the graph, so a query can land on one. Ignore results whose label is a sentence. The report's "isolated nodes" line is mostly these and is a false alarm.
- `graphify` may not be on the shell PATH. Call `~/.local/bin/graphify`, or add `$HOME/.local/bin` to PATH. The hook adds the path itself.
- `graphify update` refuses to write a graph with fewer nodes. After large deletions run `~/.local/bin/graphify update . --force` by hand; the refusal shows in `graphify-out/refresh.log`.

Packages ([how they're built](docs/plans/done/plan-packaging.md)):

```sh
uv run --group packaging pyinstaller packaging/homebound.spec --noconfirm --workpath build/pyinstaller --distpath dist
packaging/linux/build_appimage.sh                                    # Linux: dist/Homebound-<version>-x86_64.AppImage
iscc /DAppVersion=<version> packaging\windows\homebound.iss          # Windows: dist\Homebound-<version>-windows-setup.exe
```

A `v*` tag runs `.github/workflows/release.yml`, which builds all of them on GitHub's runners and attaches them to a draft release. `test.yml` runs the tests on Linux and Windows on every push. The app icon is drawn in code; `packaging/make_icons.py` renders it to PNG, SVG and ICO.

## License

Homebound is free software under the [GNU General Public License v3.0 or later](LICENSE).

The license covers Homebound's own code. Bundled third-party software, fonts and data keep their own terms; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). In short: Qt/PySide6 under LGPLv3, the EVE icons under the EVE Online terms of Fenris Creations (formerly CCP Games), and the fonts under OFL/MIT.

Homebound is not affiliated with or endorsed by Fenris Creations. EVE Online and the EVE logo are trademarks of Fenris Creations.
