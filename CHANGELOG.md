# Changelog

- **Audience:** players using Homebound.
- **Scope:** what changed for players in each release, newest first. Written from the GitHub release notes and the finished plans in [`docs/plans/done/`](docs/plans/README.md); the code-level story is in git. A release's section is its GitHub release notes: `release.yml` fails for a tag with no section here.
- **Kept current:** checked against `dbf6d73` (2026-11-14). Every commit that changes what a player sees adds a line under "Unreleased".

Each section is headed `## <version> (<date>)` with the groups Added, Changed and Fixed. When a release is cut, rename "Unreleased" to the version and date, and add a fresh "Unreleased" above it.

## Unreleased

### Fixed

- Linux (AppImage): installing the KWin focus script from Settings failed because the helper tools loaded the AppImage's older libraries.

## 1.0.2 (2026-11-12)

### Fixed

- Linux: Homebound 1.0.1 didn't start (`No module named 'PySide6.QtNetwork'`). This release includes all fixes of 1.0.1, which couldn't be used on Linux.

## 1.0.1 (2026-11-12)

Homebound rebuilds its game data once after updating (about 100 MB).

### Added

- **Watched systems.** Add a system to the Watch section to be told when your chain gets within a set number of jumps of it, without being able to map from it. A connection you lost can be cut off without deleting its systems; the cut-off mark syncs to the other members of a cloud map.
- **Structure list.** Structures found with the combat probes are collected in a per-system list automatically.
- **Kill details.** The zKill activity of a system shows what was killed, not just how many kills there were.
- **Move several systems together.** Select several and drag them as one.
- **Gate connections.** Two systems that are also joined by a stargate are drawn with right-angle lines instead of the wormhole curve.
- **Return wormhole.** Mark a signature or wormhole as the way back right away; Homebound sets its type from the other side.
- **Copy bookmark text** also copies the signature ID and the class when one is known.
- **Remembered names.** A custom name you give a system is remembered and comes back when the system is added again.
- **Overlay shortcut.** Ctrl+Shift+O switches to the next overlay mode, also while EVE has focus (on Linux through KDE's global shortcuts), and the tray menu shows it.

### Changed

- The overlay shows only the systems one or two jumps around the character instead of growing with the chain, and its follow mode skips characters whose game client is closed.
- A "new" mark on a signature clears by itself after about 10 minutes.
- Combat-scan results are stored as ships, structures and so on instead of signatures under wrong groups.
- A damaged backup is marked in the list, and a damaged map is left out of a backup while every other map is still saved.

### Fixed

- Linux: the game data download failed with `CERTIFICATE_VERIFY_FAILED`.
- Linux: no icon in the app menu after install.
- Opening Homebound twice broke both windows; a second start now raises the first.
- Delete removed only one of several selected systems.
- System names and hole numbers could change after they were given.
- Some signature groups wouldn't collapse.
- The tray menu had no overlay Off option.
- The online dot in the character list wasn't reliable.
- A corrupt map, a bad `settings.json`, a badly named log file or a path containing `#` or `?` could stop the app or leave an empty backup; each is handled now.

## 1.0.0 (2026-11-08)

### Added

- **Cloud sync.** Turn any map into a cloud map from the **Cloud** menu and share it live with your corp, alliance or friends. Members see each other's jumps, signatures, notes and hole details as they happen.
- **Works offline.** The map is still a local file. Without a connection you keep mapping, and changes are merged when you're back; for the same field the newer edit wins.
- **Sign in with EVE.** Homebound asks for no ESI scopes and can't read your wallet, mail or assets. Alts can be linked to one account.
- **Roles and invites.** Owner, managers, editors and viewers have their own rights. Invite codes have a lifetime and a number of uses. Members can show where their characters are on the shared map.
- **Corp maps** are paid for by the corporation and owned by its CEO. Members who leave the corp are removed automatically.
- **Who may join.** Limit a map to certain alliances, corporations or characters; invite codes then only work for them.
- **Pricing** is paid in ISK per 30 days, with 3 seats per map: 100M for your first map, 50M for the second, 25M for the third, free from the fourth, plus 25M, 15M or 5M for each extra seat. The first map can start a 3-day free trial. The local mapper stays free and needs no login.

### Changed

- Update over 0.1 by installing over it; maps, settings and backups are kept.

## 0.1.0 (2026-11-04)

First public beta.

### Added

- Automatic jump mapping from the Local chat logs, and system naming (numeric, `A1a`, letter-led).
- Signature pasting with rescan tracking, and mass, lifetime and size per hole.
- An in-game overlay.
- Routes to the trade hubs and your own destinations, including EVE-Scout's Thera and Turnur connections.
- Daily backups with Undo, and several maps.
- Packages: an AppImage for Linux, and an installer and portable zip for Windows (unsigned, so SmartScreen warns on first start).
