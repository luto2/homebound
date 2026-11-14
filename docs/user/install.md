# Install and first start

- **Audience:** players installing Homebound.
- **Scope:** every way to install and start Homebound, the one-time map data download and the first settings. Where the data ends up is in [Where your data lives](data.md).
- **Kept current:** checked against `702f2d5` (2026-11-14). Update it when the installers, the packages or the first-start dialogs change.

## Linux: the install script

Paste this into a terminal (bash, zsh or fish). It installs the two libraries the AppImage needs (it asks for your password), downloads the latest release to `~/.local/bin/homebound` and adds Homebound to your app menu. Run it again to update. It knows apt (Debian, Ubuntu, Mint, Pop!_OS), dnf (Fedora), pacman (Arch, CachyOS, Manjaro, EndeavourOS) and zypper (openSUSE).

```sh
curl -fsSL https://raw.githubusercontent.com/luto2/homebound/master/packaging/linux/install.sh | sh
```

The script is [`packaging/linux/install.sh`](../../packaging/linux/install.sh) if you want to read it first. To uninstall:

```sh
curl -fsSL https://raw.githubusercontent.com/luto2/homebound/master/packaging/linux/install.sh | sh -s -- --uninstall
```

Your maps stay in `~/.local/share/homebound/`.

## Windows and the AppImage by hand

Download from the GitHub release page:
- **Linux**: the `.AppImage`. Make it executable (`chmod +x Homebound-*.AppImage`, or the file manager's Properties) and start it. If it doesn't start, install `libxcb-cursor0` (Arch: `xcb-util-cursor`).
- **Windows**: run `Homebound-<version>-windows-setup.exe`. It installs for your user only, without admin rights, and can add a desktop shortcut and start Homebound when you sign in. Windows SmartScreen warns about the unsigned installer: *More info → Run anyway*. The `-portable.zip` holds the same program to unpack anywhere.

## Map data download

Packages contain no EVE game data. On first start, a **Download map data** dialog explains that Homebound builds its map of New Eden once from the EVE static data export and the community wormhole data (about 100 MB). Click **Download**; a bar shows each step, and the main window opens when it's done. If the download fails (no network, say), the dialog shows why and **Download** becomes **Retry**. **Quit** stops the build and closes Homebound. In a package the downloads are deleted afterwards, so only the finished `universe.sqlite` (a few MB) stays in the data folder.

## From source

You need Python 3.12 or newer and [uv](https://docs.astral.sh/uv/):

```sh
uv sync
uv run python -m homebound.universe.build_static   # once: builds data/universe.sqlite
uv run python -m homebound
```

`build_static` downloads the EVE static data export (systems, classes, gates, effects) and the community wormhole data (statics and wormhole types) once and writes `data/universe.sqlite`. After that Homebound works offline. If that file is missing when Homebound starts, the same **Download map data** dialog offers to build it; it also shows the command with a copy button.

### Refreshing the map data

To refresh the universe data of an installed package (after a new wormhole type, say), run `homebound --build-static --refresh` (Windows: `Homebound.exe --build-static --refresh` in the install folder, which runs without a window or output for a minute or two; AppImage: `./Homebound-*.AppImage --build-static --refresh`). It writes a new `universe.sqlite` into the data folder. When a new Homebound version needs a newer format, the dialog appears again at start as **Map data needs an update**.

## First start

**In EVE**, turn on *Settings → Chat → Log chat to file*. Homebound only reads the Local channel logs.

On first start, Settings opens on **Log folder**:
- **Auto-detect** looks in the Steam/Proton prefixes of every Steam library (`…/steamapps/compatdata/8500/pfx/drive_c/users/steamuser/Documents/EVE/logs/Chatlogs`) plus Wine prefixes, `/mnt/EVE-logs` and `~/Documents/EVE/logs/Chatlogs`. On Windows it looks in `Documents\EVE\logs\Chatlogs`, wherever Windows has the Documents folder (OneDrive included), and in `OneDrive\Documents` / `OneDrive\Dokumente`.
- **Browse** picks the folder by hand.
- A line under the field says whether the folder is right, and how many logs and characters it found.

Then set your **Home system** (autocomplete over every system name) and pick a **naming scheme**.

## Fullscreen and Plasma

On Windows, run EVE windowed or borderless: no window can sit above exclusive fullscreen. On KDE Plasma (Wayland), install the KWin focus script once under **Settings → KWin focus script** so the overlay can follow the focused client.
