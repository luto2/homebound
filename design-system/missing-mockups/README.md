# Missing mockups

> **Status (2026-10-31): §1–8 done.** Every part in §1–8 now has a spec and mockup (SigEditor, SignatureTable, MapMenu, PromptDialog, ConfirmDialog, ContextMenu, StartupError, MapWindow line grid), and the app follows them. Those sections are kept as the original brief.
>
> **Open (2026-11-03): §9 SystemPanel and §10 CharacterDock.** Both are built and only get one line each in the MapWindow README and a rough sketch in `previews/MapWindow.html`; neither has its own spec or mockup.
>
> **Open (2026-11-07): §11 Cloud sync UI.** Not built; brief and design-pass plan for the sync screens.

These parts of Homebound exist in the app but have no mockup in `previews/` or spec in `components/`. Most are stock Qt widgets that only get the global stylesheet. A few were built from the general Dialog rules without a screen to match.

Each entry says what the part does, when it appears, what it contains, how it behaves, and how it's built now. Use this as the brief for the next design pass.

General rules that apply to all of them (from `README.md`, `components/Dialog/README.md`):
- Dark theme only. Colour never carries meaning alone: every state also has a glyph, dash or label.
- Dialogs: 400–440px wide, `bg-2`, `radius-4`, 1px `line-2`, `shadow-dialog`. Header `ui-title`; footer on `bg-1` with a `line-1` rule.
- Destructive buttons are `danger` and sit far left. Primary is far right. Enter = primary, Esc = the safe answer.
- Copy: sentence case, short, no exclamation marks.

---

## 1. Signature editor

**Status:** built (`homebound/ui/dialogs.py` → `SignatureDialog`) from the Dialog rules, without a mockup.

**What it does:** lets the user enter what the probe scanner can't show. The type of a wormhole only shows up on grid ("show info"), and so do its lifetime and mass state. Anything entered here carries over to the map connection once the hole is jumped. If the sig is already linked to a connection, saving updates that connection as well.

**Opens from:** double-click a signature row in the SignatureTable. Anomaly rows don't open it.

**Contents, top to bottom:**
| Part | Control | Notes |
|---|---|---|
| Title | `Signature · ABC-123` | Sig ID in the title. |
| Subtitle | `Home (J161940)` or `Home (J161940) · leads to A2a` | Which system the sig is in; the target if it's linked. |
| Group | combo | Wormhole, Combat site, Data site, Relic site, Gas site, Ore site, Unscanned. The wormhole fields are only shown when Wormhole is picked. |
| Wormhole type | dropdown in the style of Wanderer (`homebound/ui/wormhole_fields.py`) | Rows: "Unknown type", K162 ("exit · opened from the other side"), this system's statics, then every type that can spawn in this system's class. Each row: code (mono) · destination class badge · size (S/M/L/XL, S in `edge-frig`) · lifetime · total mass (right) · `STATIC` caption in `accent`. Last row "Show all types …" adds every other type, dimmed. Type-ahead jumps to codes. The closed combo paints the same row. |
| Type info line | help text | e.g. `→ C5 · Capital (XL) · 24h · 3 300 kt total · 2 000 kt per jump`. |
| Leads to | combo | **Only for K162.** Unknown, C1–C6, HS, LS, NS, TH, C13, PV, DR. Any other type says where it leads. |
| Size | 5-way segmented | **Only for K162.** ? · Frigate · S · Medium · M · Large · L · Capital · XL. Any other type gives its size (by max mass per jump: ≤ 5 kt S, ≤ 62 kt M, ≤ 375 kt L, else XL). Size S marks the connection frigate-only. |
| Lifetime | 4-way segmented | Stable · < 4h · < 1h · Expired. Changing the state restarts its clock; saving the same state again doesn't. The connection is removed 4h / 1h / 10 min after the state was set. |
| Mass | 4-way segmented | Fresh (> 50%) · Reduced · R (< 50%) · Critical · CRIT (< 10%) · ? (unknown). |
| Name | text field | Editable for sites. For wormholes it's read-only (always "Unstable Wormhole" from the scanner), shown in the disabled style. |
| Footer | `✕ Delete sig` (danger, left) · Cancel (ghost) · Save (primary) | |

The **connection editor** (double-click an edge) now uses the same Type / Size / Lifetime / Mass fields (no Leads to: both ends are known), with `✕ Collapse` on the left. It has a spec in `components/Dialog` but its mockup predates these fields.

**States to design:**
- Wormhole, type unknown (most common).
- K162: Leads to and Size shown.
- Known type: info line, no Leads to / Size.
- Linked to a connection: the subtitle shows the target, and the values come from the connection.
- Non-wormhole group: only Group and an editable Name are shown, so the dialog is much shorter.
- The type dropdown open, with the static on top and "Show all types …" at the bottom.

**Open questions for design:**
- Should the segmented controls use the edge-state language (`edge-reduced`, `edge-critical`, `edge-eol` colours plus chips)?
- How should "Expired" read on the map? It's currently a `✕ EXP` chip in `danger` on the edge label.

**Related change in the SignatureTable:** for unlinked WH sigs, the Name column now shows what was entered here: `→ C3 · M · ◷ <1h · R` (size only for K162; `F` for frigate holes).

---

## 2. SignatureTable: group headers and anomalies

**Status:** built (`homebound/ui/signature_table.py`). It departs from `previews/SignatureTable.html`.

**What changed:**
- Rows are grouped, with a header row per group. Signatures come first (Wormholes, Combat, Data, Relic, Gas, Ore, Ice, Unscanned), then anomalies (`Anomalies · Combat`, `Anomalies · Ore`, …).
- Header row: `▾`/`▸` caret, the group bullet (the shape and colour the Group column used before), the group title in `ui-small` semibold, and the count right-aligned in `mono-small`/`ink-faint`. `bg-3` fill, `bg-4` on hover. The header spans all columns.
- Clicking a header collapses or expands the group, and this is remembered per map. A group that has new (+) or missing (−) rows always stays open.
- The Group column is hidden because the headers already say it. Its width goes to Name.
- The section title meta now reads `5 sigs · 3 anoms · last paste 2m ago`.

**Behaviour:** a paste only flags missing entries of the kinds it contains. Pasting just signatures leaves anomalies alone, and the other way round.

**Needs:** a mockup of the grouped table with one group collapsed, a missing row inside an open group, and anomaly groups.

---

## 3. Map menu (toolbar)

**Status:** built (`homebound/ui/map_window.py` → `_fill_map_menu`). It's a QMenu styled by the global QSS, with no mockup.

**What it does:** switches between maps and manages them. Each map is a separate SQLite file with its own systems, signatures, home system and naming scheme. Jumps are tracked on the active map only. Character locations carry over when switching.

**Toolbar button:** `▦ Main` sits left of the home button, and the menu opens on click. The window title becomes `Homebound · Main` once more than one map exists.

**Menu contents:**
- One checkable row per map: `Main   ·   J161940` (name · home system, or "no home"). The active map is checked. Picking a row switches maps and shows a toast: `Map “Corp”`.
- Separator.
- `New map …`: see §4.
- `Rename “Main” …`: see §4.
- `Delete map ▸`: a submenu listing every map except the active one. Disabled when there's only one map. See §5.

**Open questions for design:**
- Is a menu enough, or should this be a combo / a left-dock section with per-map stats (systems, last activity)?
- How do you see at a glance which map you're on while looking at the canvas?

---

## 4. Text-input dialogs

**Status:** stock `QInputDialog`, global stylesheet only. These don't follow the Dialog layout (no title/subtitle block, no `bg-1` footer, OK/Cancel in the platform order).

A single "text prompt" variant of Dialog would cover all four:

| Dialog | Opens from | Prompt | Field | On OK |
|---|---|---|---|---|
| New map | Map menu → New map … | "Name of the new map (each map has its own home system)" | text | Creates the map, switches to it, opens Settings → Home system. Error toast if the name exists. |
| Rename map | Map menu → Rename … | "New name" (prefilled) | text | Renames; error toast if the name exists. |
| Rename system | System node context menu → Rename … | "Custom name for J123456 (empty = scheme name)" (prefilled) | text | Sets or clears the custom name. Clearing it brings back the scheme name. |
| Add system | Map background context menu → Add system … | "System name (J-name or k-space)" | text with autocomplete over all system names | Adds the system where the user right-clicked. If another system is selected, also adds a WH connection to it. Error toast for unknown names. |

**Suggested design:** title + one-line help, a single field (mono for system names), and inline validation in the TextField valid/invalid states instead of an error toast after closing. Footer: Cancel (ghost) · primary action named for what it does ("Create map", "Rename", "Add system").

---

## 5. Confirm destructive action

**Status:** stock `QMessageBox.question`, used once so far.

| Dialog | Opens from | Text | Buttons |
|---|---|---|---|
| Delete map | Map menu → Delete map ▸ name | "Delete the map “Corp” and everything on it? This can't be undone." | Yes · Cancel (Cancel default) |

On Yes, the map's file is deleted (`.db`, `-wal`, `-shm`) and a toast shows `Map “Corp” deleted`. The active map can't be deleted.

**Suggested design:** a Dialog variant with a danger confirm button named for the action (`✕ Delete map`), Cancel as the default, and maybe a summary of what will be lost (n systems, n sigs).

Other destructive actions currently happen without a confirmation and rely on the result being visible: Collapse connection, Remove from map, Delete selected sigs, Delete missing sigs. Design should decide whether those need confirmation or an Undo toast like auto-naming has.

---

## 6. Context menus

**Status:** QMenus styled by the global QSS (`qt-implementation.md` covers menu styling, but no menu other than the tray is drawn). SystemNode's README only mentions that a context menu exists.

| Where | Items |
|---|---|
| System node (right-click) | Rename … · Set as home · Change static / hole … (only if it has a parent; reopens the NamingDialog) · ― · Request zKill now · ― · ✕ Remove from map (disabled for home) |
| Connection edge (right-click) | Edit connection … · ― · Mass fresh / Mass reduced · R / Mass critical · CRIT (checkable; unknown is only in the editor) · ― · Lifetime stable / < 4h / < 1h / expired (checkable, exclusive) · ― · ✕ Collapse |
| Map background (right-click) | Add system … · Fit chain |
| Character list row (right-click) | Pin overlay to this character · Show on map |
| Map menu (toolbar) | see §3 |

**Needs:** one mockup showing the menu style (item height 26px, `accent` selection, ink-inverse text, separators, check marks, a danger item, a disabled item with a reason tooltip) is enough for all of them.

---

## 7. Startup error

**Status:** stock `QMessageBox.critical`, shown before the main window exists.

Appears when `data/universe.sqlite` is missing. Text: "…universe.sqlite missing. Run: uv run python -m homebound.universe.build_static". Then the app exits.

**Suggested design:** a small standalone Dialog with the command in a mono field and a copy button, since that's the only thing the user needs to do. This could be a first-run screen later, if building the static data moves into the app.

---

## 8. Map canvas grid

**Status:** built. It departs from the design on purpose (user request).

The design asks for a dot grid at `sp-24` pitch (MapWindow README, `qt-implementation.md`, Cover). The app now draws a **line grid**:
- Cell = node height / 3 (62 / 3 ≈ 20.7px), so a node is exactly 3 cells tall.
- Minor lines in `grid-dot`. Every 3rd line (one node height) is brighter, in `line-1`. Both are 1px cosmetic.
- Below 45% zoom only the major lines are drawn. Below 20% the grid is plain `bg-0`.
- Nodes snap to the cell. New children are placed 12 cells right and 5 cells down per sibling.

**Needs:** the design docs (MapWindow README, `qt-implementation.md` "Dot grid", Cover preview, `grid-dot` token usage text) should be updated to describe the line grid, or the line grid restyled.

---

## 9. System panel (right dock)

**Status:** built (`homebound/ui/system_panel.py` → `SystemPanel`), titled "System" in its dock. It has one line in the MapWindow README ("Right dock") and is sketched in `previews/MapWindow.html`; there's no spec of its own. The signature part uses SignatureTable, which has a spec.

**What it does:** shows and edits everything about the selected system: its custom name, owner, notes, zKill activity and signatures. Signatures are pasted from the probe scanner here.

**Shown when:** a system is selected on the map (click a node, find, or Show on map in the character dock). With nothing selected it shows one help line: "Select a system on the map".

**Contents, top to bottom** (`sp-12` padding, `sp-8` between rows, at least `size-panel-right` wide):
| Part | Control | Notes |
|---|---|---|
| Name row | text field (`ui-title`) · J-name (mono, right) | Custom name. Empty shows the scheme name as placeholder (`A2`); clearing it brings the scheme name back. Saved on Enter or leaving the field. |
| Location line | help text, wraps | `A2 · Region · Constellation · 3 j to Jita`, or `· trade hub` when the system is one. |
| Badge strip | painted, 22px | Class badge (k-space with security) · effect glyph + name in its `effect-*` colour · each static as StaticBadge + type code (`mono-small`, `ink-faint`). |
| Owner row | `OWNER` caption · ticker field (mono, 80px, max 6, saved uppercase) · colour swatch combo (10 `owner-*` swatches) · note field ("Note (structure, timers …)") | Feeds the OwnerTag on the node. Saved on Enter, leaving a field or picking a colour. |
| zKill row | `ZKILL` caption · mono line, wraps · `Refresh` (ghost) | `2/14 kills · 0 pods 1h · last kill 25m ago`, `no kills 24h`, `loading …` or `off`; `· stale 14m` appended when old. Refresh forces a fetch. Selecting a system requests one too. |
| Notes | plain text, 56px | Placeholder "Notes". Saved 0.6 s after the last keystroke. |
| Divider | `line-1` rule | |
| Signatures title row | `Signatures` (title) · summary (help, elided) · `Paste sigs (Ctrl+V)` (primary) | Summary: `5 sigs · 3 anoms · last paste 2m ago` or `never pasted`; when a rescan is due it adds `· ▲ rescan due` and turns `warning`. |
| Missing banner | `warning` banner, only when rows are missing | `▲ 2 sigs missing from the last paste · anomalies untouched` · `Keep` (ghost) · `✕ Delete 2` (danger). |
| Table | SignatureTable, takes the rest of the height | Double-click a signature row opens the SigEditor. Delete removes the selected rows. |
| Bottom row | `Delete selected` (ghost, right) | |

**Behaviour:**
- Fields that have focus aren't overwritten when the map updates, so typing isn't lost to a log or zKill refresh.
- Paste reads the clipboard. No system selected: error toast "Select a system before pasting sigs". No scanner rows: error toast with an example line. Otherwise a toast `5 sigs + 3 anoms · 2 new · 1 updated · 1 missing`, and the pasted rows flash.
- Deleting sigs (selected, missing, or from the SigEditor) also removes the connections they belong to (the systems stay) and shows an Undo toast: `Deleted 3 missing sigs` / `HJK-447, PLW-662, UTE-071 in A2`.
- Copying a bookmark name from the table shows `Copied bookmark name …`.

**Needs:** a mockup of the panel at 440px with a wormhole system (effect, two statics, owner set), the missing banner showing, and the empty state. The MapWindow sketch has an "owner combo"; the app uses a ticker field, swatch and note instead.

---

## 10. Character dock (left dock)

**Status:** built (`homebound/ui/character_dock.py` → `CharacterDock`), titled "Characters" in its dock, above Routes. It has one line in the MapWindow README and a sketch in `previews/MapWindow.html`; CharacterMarker covers only the marker shapes.

**What it does:** lists every character the log watcher has seen, where each one is, and which one the overlay follows.

**Row** (44px, `size-panel-left` wide), painted by a delegate:
- Line 1: CharacterMarker in the character's slot colour and shape · name in `ui-body` bold (`ink` online, `ink-muted` offline; elided) · online state right-aligned in `ui-small` 11px: `● online` in `success`, or `○ offline 2h` in `ink-faint` (age since last seen, left out when unknown).
- Line 2, indented under the name: class badge · the map name (`A2`, custom name, or the real name if not on the map) in `ink`, then the J-name in `mono-small` `ink-muted` when it differs (not for k-space). Unknown location: "Location unknown" in `ink-faint`.
- The row the overlay follows: `accent-dim` fill and a 2px `accent` bar on the left edge. Hover: `bg-4`.
- Tooltip: `Name (character id)`.

**Behaviour:**
- Double-click or Space selects the character's system on the map, if it's on this map.
- Right-click: `Pin overlay to this character` · `Show on map` (`Space`; disabled with "Not in a system on this map." when it isn't).
- The list rebuilds when characters change and repaints when the followed character or the map changes.

**Needs:** a mockup with three rows (followed + online, online in k-space, offline with age), plus the unknown-location row and the context menu.

---

## 11. Cloud sync UI (not built yet)

**Status:** design only. Nothing is built, and cloud sync itself is still WIP (`docs/plans/cloud-sync-plan.md`). This section is the brief, so the screens exist before the client sync work (plan §11, phase 3) starts. The web view is deferred and isn't part of this.

**Plan for the design pass, in this order** (each step one spec in `components/` plus a preview in `previews/`):

1. **Sign in** (plan §6): a small dialog with "Sign in with EVE Online" (opens the browser), a waiting state ("Finish signing in in your browser …", Cancel), and the fallback field "Paste the code from the browser". Error states: browser closed or timed out, code invalid. Also a **re-login due** variant: "Sign in" (primary) and a single "Do it later" (ghost, +36 h), which is gone once it has been used.
2. **Account dialog** (plan §6): the account code (mono, copy button) · linked characters (portrait, name, corp ticker, `✕ Unlink`, disabled on the last one) · `+ Add character` · a devices list (device name, last used, `Sign out`; the current device is marked) · footer with `✕ Delete account` (danger, left), which opens a ConfirmDialog stating what is lost (maps it owns transfer, balance forfeited).
3. **Sync status pill** (toolbar, next to the map menu): the states offline · syncing · live · warning (lapse soon) · read-only · conflict (brief, after a `reject`). Each one has a glyph and a label, not colour alone. Clicking it opens a popover with details and "Retry now".
4. **Make cloud map** (Settings → map section, plan §15): the payer character picker (character, balance or "trial available"), the monthly price for the current seats, and "Make cloud map". Shows a progress and done state.
5. **Share / members dialog** (owner and managers, plan §5): the member list (name, role combo, joined date, `✕ Remove`) · `Create invite` (role, expiry, uses) → a link or code with copy · a seats line `4 of 3 included · +25M ISK / 30 days`.
6. **Billing panel** (owner, plan §7): paid until · balance · the price breakdown by tier, showing how the maps are ranked · how to pay (receiving character name with copy, account or corp code with copy, "send as player donation") · the trial state · map count `3 / 6`.
7. **Lapse warning** (plan §7): a banner on launch and on the map for the warning and read-only states, with the date and "How to pay".
8. **Not-saved toast / list** (plan §5 and `sync-protocol.md` §7): edits rejected after a role change or lapse. "3 changes weren't saved: you can no longer edit this map", with a "Show" link.
9. **Conflict toast** (`sync-protocol.md` §6): "A3 was already mapped by Kira; your version was replaced".
10. **Presence toggles** (character dock, plan §3): a per-character "Share location on this map" toggle, and remote members' characters in the dock (marked as remote, with the account name, and an AFK state after 1 h).
11. **Map menu:** a cloud marker on cloud maps, plus each map's sync state (§3 of this README).
12. **Corp map extras** (plan §16): a CEO badge and hand-picked managers in the members list, a seat limit field, the corp balance in the billing panel, and an optional "Let corp members join as viewer" switch.

**General rules:** the same as above. Money amounts are mono, with `M`/`B` suffixes (`125M ISK`). Destructive actions (unlink, remove member, delete account) are `danger` and sit far left.
