# SignatureTable

The signature and anomaly list for the selected system, **grouped** with one header row per group: sig ID, type, name, last seen, age, plus a copy-bookmark action on each wormhole row.

**Groups and order:** signatures first (Wormholes, Combat, Data, Relic, Gas, Ore, Ice, Unscanned), then anomalies (`Anomalies · Combat`, `Anomalies · Ore`, …). Empty groups aren't shown.

**Header row** (spans every column, 22px): `▾` open / `▸` collapsed caret in `ink-muted` · the group glyph (SiteIcon) · title in `ui-small` semibold (anomaly groups prefix `Anomalies ·` in `ink-muted`, regular weight) · count right-aligned in `mono-small` `ink-faint`, followed by `· 1 −` in `warning` or `· 1 +` in `success` when the group has missing or new rows. `bg-3`, `bg-4` on hover.
- Click toggles the group; the state is remembered per map. **A group with new or missing rows always stays open**, even if it was collapsed, so a change is never hidden.
- Header rows can't be selected and don't open the editor.

**Columns:** flag · Sig (`mono-sig`, 13px bold, the most-read value) · Type (WH code, mono) · Name / leads to (gets the old Group column's width) · Seen (time since the last paste that contained the sig; mono, right) · Age (mono, right) · action. The Group column is hidden; the headers say it.

**Name for wormholes:** a linked sig shows `→ <target>`. An unlinked one shows what was entered in the SigEditor: `→ C3 · M · ◷ <1h · R` (size only for a K162, `F` for frigate holes).

**Row states**
- default; hover `bg-4`; selected `accent-dim` + 2px `accent` inner left edge.
- **Missing from last paste**: `warning-dim` fill, struck through in `warning`, flag "−". A banner above the table counts them and offers **Keep** and **✕ Delete n** (danger). A paste only flags missing entries of the kinds it contains: a sigs-only paste leaves anomalies alone (the banner says so), and the other way round.
- **New in last paste**: flag "+" in `success`, for one paste cycle.
- **Just pasted**: every row in the paste (new, updated or unchanged) flashes a `success` fill that rises to ~35% in 0.12 s and eases out over 1.8 s. Missing rows don't flash.
- **Stale** (system not re-scanned for 24h+): text drops to `ink-faint`.

**Section title meta:** `9 sigs · 4 anoms · last paste 2m ago`.

**Group icons** (see SiteIcon): EVE's bracket icons, 16px, tinted wormhole `accent`, combat `danger`, data `ink-muted`, relic `warning`, gas `success`, ore `warning`, ice `accent`; unscanned uses our dashed ? in `ink-faint`. Left of the title. Anomaly groups use the same glyph as the matching signature group.

**Actions**: "Paste sigs (Ctrl+V)" is the panel's primary button and also works anywhere in the map window. Each WH row has a `sm` "⧉ 123-1" button that copies the bookmark name built from the naming scheme; after copying, the label switches to "✓ Copied" for 1.5 s. Double-click a sig row opens the SigEditor (not for anomalies).

Qt: `QTableView` + `QAbstractTableModel` with header rows as model rows (`GroupHeader`); `setSpan(row, 0, 1, columnCount)` for each header after a reset; row states via `Qt.BackgroundRole`/`Qt.FontRole` or a `QStyledItemDelegate`; the copy button is painted by the delegate and hit-tested in `editorEvent`, not a real widget per row.
