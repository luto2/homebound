# Plan: app menu icon after the README install

Status: implemented (2026-11-12, `e6b86d3`). Deviations: the cause stayed a stale Plasma cache; the install refreshes it and ships 48 px and SVG icons; confirmed working by the owner on 2026-11-12.

- **Audience:** the owner and coding agents who need to know why something was built the way it is.
- **Scope:** the record of "Plan: app menu icon after the README install", finished. The status line says where the build differs from the plan; where the code lives now is in [`code-map.md`](../../code-map.md).
- **Kept current:** archived with the plan, not updated after it was implemented (see the status line). The rest of the text is the plan as it was written.

Depends on: nothing. Touches `README.md` (install commands), `packaging/linux/homebound.desktop` and
possibly `packaging/linux/AppRun` / `build_appimage.sh`.

## Problem (reported)

On CachyOS (Plasma 6) Homebound shows up in the app menu without an icon after the README install. The
install writes a valid 256×256 PNG to `~/.local/share/icons/hicolor/256x256/apps/homebound.png`, and the
desktop entry says `Icon=homebound`.

## Evidence so far (2026-11-09)

The icon (menu and taskbar) appeared on its own after `pacman -S retext` on 2026-11-09 12:46. That install
ran the `gtk-update-icon-cache` and `update-desktop-database` pacman hooks (ReText ships a desktop entry
and hicolor icons); the earlier AppImage-related install on 2026-11-08 had not run the icon-cache hook.
ReText and its dependencies have nothing to do with Homebound, so the likely cause is a stale Plasma
cache (the menu's sycoca and KIconLoader's `~/.cache/icon-cache.kcache`) that another package's install
happened to refresh. The pacman hook itself only rebuilds caches under `/usr/share/icons`, not
`~/.local/share/icons`, so the fix is to make Plasma re-read, not to build a GTK cache. Not yet proven:
reproduce on a fresh user by installing, checking the icon is missing, then running only step 1, and if
that doesn't do it, deleting `~/.cache/icon-cache.kcache` and logging out and back in.

## Investigation first (cause likely a stale cache, see above)

Work through these on the owner's machine, cheapest first, and stop at the first one that fixes it.
Write down which one did, because it decides the fix.

1. **Stale menu or icon cache.** Run `kbuildsycoca6 --noincremental`, then reopen the menu. If the icon
   is still missing, remove `~/.cache/icon-cache.kcache` and restart plasmashell
   (`systemctl --user restart plasma-plasmashell`). If either fixes it, the install just needs to refresh
   the cache.
2. **Icon theme cache / missing index.** `~/.local/share/icons/hicolor/` has no `index.theme` of its own.
   Most desktops fall back to `/usr/share/icons/hicolor/index.theme`; check it exists. Run
   `gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor` (harmless on Plasma).
3. **Only one size installed.** Some menus look for 48×48 or `scalable` first and don't scale down
   from 256. Install `homebound.svg` to `scalable/apps/` and a 48×48 PNG to `48x48/apps/`.
4. **Name clash.** Check `find / -name 'homebound.*' -path '*icons*' 2>/dev/null` for another
   `homebound` icon (for example from an AUR build) that shadows ours.
5. **Absolute path.** As a last check, put `Icon=$HOME/.local/share/icons/hicolor/256x256/apps/homebound.png`
   into the installed desktop entry. If only this works, the theme lookup is the problem.

## Change (once the cause is known)

- Default, whatever the cause: install the SVG to `hicolor/scalable/apps/` and the PNG at 256 and 48.
  `packaging/make_icons.py` already renders the SVG; add a 48 px PNG to its outputs and to
  `packaging/icons/`.
- If step 1 or 2 fixed it: end the README install command with
  `command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 >/dev/null 2>&1; command -v update-desktop-database >/dev/null && update-desktop-database ~/.local/share/applications`.
- Update the uninstall line in the README to remove the new files too.
- The AppImage itself: check `build_appimage.sh` puts `homebound.png` and `.DirIcon` at the AppDir root,
  so AppImage launchers (AppImageLauncher, Gear Lever) also get an icon.

## Tests

No automated test: it depends on the desktop. Check on Plasma 6 (the owner's machine) and, if possible,
GNOME, from a clean `~/.local/share` (uninstall line first, then the install line).

## Open decisions

1. Should the README install become a small script (`packaging/linux/install.sh`) instead of a long
   one-liner? Recommended once it grows past the cache refresh: easier to read and to keep in step with
   the uninstall.

## Owner decisions (2026-11-12)

- The README install is now one line that runs `packaging/linux/install.sh` (done 2026-11-12); the same
  script takes `--uninstall`. This also settles the fish syntax question: a pipe into `sh` works the same
  in every shell.
