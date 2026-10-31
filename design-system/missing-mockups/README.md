# Missing mockups

> **Status (2026-10-31): done.** Every part below now has a spec and mockup (SigEditor, SignatureTable, MapMenu, PromptDialog, ConfirmDialog, ContextMenu, StartupError, MapWindow line grid), and the app follows them. This file is kept as the original brief.

These parts of Homebound exist in the app but have no mockup in `previews/` or spec in `components/`. Most are stock Qt widgets that only get the global stylesheet. A few were built from the general Dialog rules without a screen to match.

Each entry says what the part does, when it appears, what it contains, how it behaves, and how it's built now. Use this as the brief for the next design pass.

General rules that apply to all of them (from `README.md`, `components/Dialog/README.md`):
- Dark theme only. Colour never carries meaning alone: every state also has a glyph, dash or label.
- Dialogs: 400–440px wide, `bg-2`, `radius-4`, 1px `line-2`, `shadow-dialog`. Header `ui-title`; footer on `bg-1` with a `line-1` rule.
- Destructive buttons are `danger` and sit far left. Primary is far right. Enter = primary, Esc = the safe answer.
- Copy: sentence case, short, no exclamation marks.

---

## 1. Signature editor

**Status:** built (`homebound/ui/dialogs.py` → `SigDialog`) from the Dialog rules, without a mockup.

**What it does:** lets the user enter what the probe scanner can't show. The type of a wormhole only shows up on grid ("show info"), and so do its lifetime and mass state. Anything entered here carries over to the map connection once the hole is jumped. If the sig is already linked to a connection, saving updates that connection as well.

**Opens from:** double-click a signature row in the SignatureTable. Anomaly rows don't open it.

**Contents, top to bottom:**
| Part | Control | Notes |
|---|---|---|
| Title | `Signature · ABC-123` | Sig ID in the title. |
| Subtitle | `Home (J161940)` or `Home (J161940) · leads to A2a` | Which system the sig is in; the target if it's linked. |
| Group | combo | Wormhole, Combat site, Data site, Relic site, Gas site, Ore site, Unscanned. The wormhole fields are only shown when Wormhole is picked. |
| Wormhole type | dropdown in the style of Wanderer (`homebound/ui/wh_fields.py`) | Rows: "Unknown type", K162 ("exit · opened from the other side"), this system's statics, then every type that can spawn in this system's class. Each row: code (mono) · destination class badge · size (S/M/L/XL, S in `edge-frig`) · lifetime · total mass (right) · `STATIC` caption in `accent`. Last row "Show all types …" adds every other type, dimmed. Type-ahead jumps to codes. The closed combo paints the same row. |
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

**Status:** built (`homebound/ui/sig_table.py`). It departs from `previews/SignatureTable.html`.

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
