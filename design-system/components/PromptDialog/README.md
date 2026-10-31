# PromptDialog

One text-prompt variant of Dialog that replaces the four stock `QInputDialog`s: **New map**, **Rename map**, **Rename system** and **Add system**. Same frame as every other dialog (title + one-line subtitle, `bg-1` footer); the only body content is one captioned field.

| Dialog | Subtitle | Field | Primary |
|---|---|---|---|
| New map | Each map has its own systems, sigs and home system. | Map name | Create map (then Settings → Home opens) |
| Rename map | `Main · home J161940` | New name, prefilled and selected | Rename |
| Rename system | `A2 · J163856 · C5` | Custom name, prefilled; placeholder is the scheme name | Rename, or **Use scheme name** while the field is empty |
| Add system | where it goes, and which selected system it links to | System name, mono, autocomplete | Add system |

**Validation happens in the dialog, not in a toast afterwards.** The field uses the TextField states while typing (debounce 150 ms):
- invalid → `is-invalid` border + `danger-dim` fill, help line `✕ …` in `danger`, primary disabled. Cases: name already used, empty map name, unknown system, system already on this map.
- valid (Add system only) → `success` border and a `✓` help line that says exactly what will happen ("added with a WH connection to A2").
- Footer left (`ui-small`, `ink-faint`) says what happens after OK when it's not obvious ("Opens Settings → Home next").

**Add system autocomplete:** `QCompleter` over all system names (J-codes and k-space), case-insensitive, `MatchStartsWith`, max 8 rows. Each row: name in mono with the typed part underlined in `accent`, class badge, and a reason on the right. Systems already on the map are shown disabled with "already on this map". Tab accepts the top row.

Qt: subclass the existing `BaseDialog` (title, subtitle, body, foot) with a `QLineEdit` and a `validator(text) -> (ok, message)` callable; set the line edit's `state` property and re-polish on each change. Enter = primary (only when valid), Esc = Cancel.
