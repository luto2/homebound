# SignatureTable

The signature list for the selected system: sig ID, group, type, name, scan %, age, plus a copy-bookmark action on each wormhole row.

**Columns**: flag · Sig (`mono-sig`, 13px bold — the most-read value) · Group (shape bullet + word) · Type (WH code, mono) · Name / leads to · Scan % (bar + number, right-aligned mono) · Age (mono, right) · action.

**Row states**
- default; hover `bg-4`; selected `accent-dim` + 2px `accent` inner left edge.
- **Missing from last paste**: `warning-dim` fill, struck through in `warning`, flag "−". A banner above the table counts them and offers **Keep** and **✕ Delete missing** (danger, with the count).
- **New in last paste**: flag "+" in `success`, for one paste cycle.
- **Stale** (system not re-scanned for 24h+): text drops to `ink-faint`.

**Group bullets**: wormhole ● `accent`, combat ◆ `danger`, data ■ `ink-muted`, relic ■ `warning`, gas ○ `success`, ore □ `warning`, unknown dashed □. The word is always shown.

**Actions**: "Paste sigs (Ctrl+V)" is the panel's primary button and also works anywhere in the map window. Each WH row has a `sm` "⧉ 123-1" button that copies the bookmark name built from the naming scheme; after copying, the label switches to "✓ Copied" for 1.5 s.

Qt: `QTableView` + `QAbstractTableModel`; row states via `Qt.BackgroundRole`/`Qt.FontRole` or a `QStyledItemDelegate`; the copy button is painted by the delegate and hit-tested in `editorEvent`, not a real widget per row.
