# StatusBar

The bottom strip of the map window. It shows the state of each background source in one line, so a broken log watcher or a failed backup is visible without opening a dialog.

- Height 24px, `bg-1` with a `line-1` top rule, `ui-small` in `ink-faint`. Sources are separated by a `·` in `ink-faint`.
- **Sources, in order:** logs · zKill · EVE-Scout · KWin · backup. Each is one label: a glyph, then the text.
- **Glyphs** (never colour alone): `✓` `success` ok · `▲` `warning` warning · `✕` `danger` error · no glyph for a neutral note (`EVE-Scout off`, `EVE-Scout …` while loading).
- **Texts** (examples): `✓ Log watcher · 4 clients` · `✓ zKill · updated 1m ago` · `EVE-Scout off` / `EVE-Scout …` (neutral while loading or switched off) · `✓ KWin focus script installed` · `✓ Backed up 09:41` · `▲ Backed up, but not copied: …`.
- **Backup** is hidden until there is something to say: the first start with no event shows nothing. A backup warning (`▲`/`✕`) also raises a toast. Its `·` is hidden together with the label.
- **KWin** is always listed; it shows `…` until the focus script reports, and the message is the same whether the script is installed or not.
- Labels update in place; a source that has not reported yet shows `…` in `ink-faint`.

Qt: a `QStatusBar` with one `QLabel` per source (`STATUS_SOURCES` in `map_window.py`); glyph and text share one rich-text label, the glyph coloured by a `<span>`. Hidden labels use `hide()`, not an empty text. The separator before the backup label is hidden with it.
