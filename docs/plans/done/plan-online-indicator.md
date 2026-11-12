# Plan: a truthful online indicator in the character list

Status: implemented (2026-11-11, commits `6e0006c`, `b25df0b`, `3fcafca`, with the protocol 2 version gate). Deviations: step 5 (overlay "follow" skips closed clients) not done; location sharing now stops when the client window closes; sharing the online state is off by default; the KWin script is 1.1 (users press Install again).

Depends on: nothing. Touches `logs/watcher.py`, the focus backends, `ui/state.py` and
`ui/character_dock.py`.

## What there is now

`logs/watcher.py` marks a character online when its newest Local log was written or started less than
`ONLINE_WINDOW` (24 h) ago. `AppState.character_rows` passes that flag to `CharacterDelegate`, which
draws "● online" or "○ offline · seen 3h ago". A character who logged off an hour ago still shows as
online, so the dot means "played today", not "logged in".

## Signals we can use

| Signal | Says | Cost |
|---|---|---|
| A window titled `EVE - <name>` exists | the client is open with that character | needs a window list, not just the active window |
| The Local log was written in the last few minutes | the character is in space and someone spoke, or jumped | free, already read; quiet systems write nothing for hours |
| A newer log file exists for the same character | a new session started | free |

The window list is the only reliable "logged in" signal. The focus backends already parse captions with
`ui/focus.py` `eve_character`; they only look at the active window today.

## Behaviour

Three states instead of two:

- **● online**: a client window for the character is open (accent `success`).
- **◐ recent**: no window seen (or no window list on this platform), but the log was written in the last
  30 minutes (`warning`, muted). This is also the fallback when window listing isn't available.
- **○ offline · seen 3h ago**: neither.

Sort order stays: online first, then recent, then offline, each by name.

## Steps

1. **Window list per backend.** Add `open_eve_characters() -> set[str] | None` (None: can't tell).
   - Windows (`platform/focus_windows.py`): `EnumWindows` + `GetWindowTextW`, same `ctypes` style as
     `foreground_window`.
   - Linux X11/XWayland (`platform/focus_linux.py`): `xprop -root _NET_CLIENT_LIST`, then the captions of
     those IDs (one `xprop` call per window is slow; use `xprop -id … _NET_WM_NAME` only for windows of
     class `exefile.exe`/`steam_app_8500`, or `wmctrl -l` when it exists).
   - KWin script: add a second D-Bus call, `Windows(captions)`, sent from `workspace.windowAdded`,
     `windowRemoved` and `captionChanged`, with the captions of all windows. EVE under Proton usually runs
     on XWayland, so the xprop path also works on Plasma if the script is missing.
   Poll every 10 s from `FocusTracker`, not on every focus change.
2. **Watcher.** `LogScanner` keeps `last_written` per character (it already has the mtime) and exposes it;
   drop the boolean `online` in favour of the time, so the UI decides.
3. **State.** `AppState.character_rows` returns a `presence` of `"online" | "recent" | "offline"` instead
   of the bool. `FocusTracker` emits `openCharactersChanged(set)`; `AppState` stores it and emits
   `charactersChanged`.
4. **Dock.** `CharacterDelegate.online_text` draws the three states; tooltip says where the state comes
   from ("client window open", "log written 12 min ago").
5. **Overlay "follow" mode** can use the same set to skip characters whose client is closed. Out of scope
   here; note it in `TODO.md` if it turns out useful.

## Tests

- `eve_character` already has tests; add caption lists with two clients, a non-EVE window, and the
  launcher window (`EVE Launcher` must not count).
- Backends: fake `EnumWindows` / `subprocess.run` output and check the parsed set; a failing `xprop`
  returns None, not an empty set (empty means "no clients open").
- `character_rows`: online beats a stale log; no window list plus a fresh log gives `recent`; no window list
  plus an old log gives `offline`.

## Risks

- Captions: a client on the character select screen is titled `EVE` without a name. It counts as no
  character, which is right.
- Polling `xprop` costs a few processes every 10 s. Measure; if it shows, only poll while the main
  window or the dock is visible.

## Open decisions

1. Keep a "recent" state, or show only online/offline once window lists work everywhere? Recommended:
   keep it, because Wayland without the KWin script and without XWayland has no window list.
2. 30 minutes for "recent"?

## As built

- Decisions as recommended: the "recent" state stays (1); 30 minutes (2, `logs/watcher.py` `RECENT_WINDOW`).
- **Window lists**: `ui/focus.py` `eve_characters` turns captions into names; `FocusTracker.poll_windows`
  asks the backend's `open_eve_characters()` every 10 s (`WINDOW_POLL_MS`) and emits
  `openCharactersChanged(set | None)`. Windows: `focus_windows.window_titles` (`EnumWindows`, visible
  windows only). Linux: the KWin script's new `Windows(captions)` call (sent on `windowAdded`,
  `windowRemoved` and each window's `captionChanged`; script version 1.1, so an installed 1.0 script needs
  the Install button again) wins once it has sent a list; else `focus_linux.x11_window_titles`
  (`wmctrl -l` in one process, else `xprop -root _NET_CLIENT_LIST` plus one `xprop` per window). Any
  failure is None ("can't tell"), never an empty set.
- **Watcher**: `CharacterState.last_written` (the newer of file time and session start) and `recent`
  replace `online`; the log status line counts "active" characters.
- **State**: `AppState.set_open_characters`, `presence(character_id)` and `presence_detail` (tooltip
  text); `character_rows` carries the presence string and sorts online, recent, offline. A character in
  the window list is online whatever its log says.
- **Dock**: `● online` (`success`), `◐ recent` (`warning`), `○ offline 3h` (`ink-faint`, time since the log
  was written); the tooltip says where the state comes from.
- **Cloud presence (protocol 2)**: a shared character is sent while it is playing: an open client, or a
  fresh log where windows can't be listed. A client seen to close stops sharing at once (it used to keep
  going for 24 h after the last log write). The `presence` message got an optional `state`
  (`"online"`/`"recent"`), gated like the ops (`sync-protocol.md` §4 Presence). **Default**: the
  state is sent unless the user turns off Cloud → "Also share whether the game is open"
  (`Settings.sync_presence_state`; off at first, on by default since the owner's answer of 2026-11-12). The window list itself, and characters that aren't shared,
  never leave the computer. Other members see a shared "recent" character as "Name (recent)".
- Not done: the overlay "follow" mode skipping closed clients (step 5, out of scope).
