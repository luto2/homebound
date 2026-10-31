# SigEditor

The dialog for what the probe scanner can't show: wormhole type, lifetime and mass (and, for a K162, where it leads and how big it is). Opens on double-click of a signature row; anomaly rows don't open it. App: `SignatureDialog` in `homebound/ui/dialogs.py`, wormhole fields in `homebound/ui/wormhole_fields.py` (shared with the connection editor).

**Layout, top to bottom:** title `Signature · ABC-123` (sig ID in mono) · subtitle with the system and, if linked, `· leads to A2a` · Group · wormhole section (only for Group = Wormhole, set apart by a `line-1` rule) · Name · footer `✕ Delete sig` (danger, far left) · Cancel (ghost) · Save (primary, default).

**Wormhole section:**
| Field | Shown | Control |
|---|---|---|
| Wormhole type | always | Custom-painted combo, 30px. One row = code (`mono-sig`) · destination class badge · size (`S` in `edge-frig` bold, others `ink`) · lifetime · total mass right-aligned (`mono-small`, `ink-faint`) · `STATIC` caption in `accent`. The closed combo paints the same row. |
| Info line | for a known type or K162 | Help text: `→ C5 · Capital (XL) · 24h · 3 300 kt total · 2 000 kt per jump`. |
| Leads to | K162 only | Combo: Unknown, C1–C6, HS, LS, NS, TH, C13, PV, DR. |
| Size | K162 only | 5 segments, code on top (mono bold) and name below: ? Unknown · S Frigate · M Medium · L Large · XL Capital. S (in `edge-frig`) marks the connection frigate only. |
| Lifetime | always | 4 segments: Stable · ◷ <4h · ◷ <1h · ✕ EXP, each with its edge pen above; tooltips spell them out (End of life, less than 1h left, Expired). |
| Mass | always | 4 segments: Fresh · Reduced R · Critical CRIT · Unknown ?, each with its edge pen above. |
| Name | always | Wormholes: read-only "Unstable Wormhole" in the disabled style, `from the scanner` at the right. Sites: editable. |

**States in the mockup:** 1 type unknown (focus in the type combo) · 2 K162 with Leads to and Size · 3 known type (info line, no Leads to/Size) · 4 linked (an info banner `⇄` says saving also updates `A2 ↔ C`; values come from the connection) · 5 not a wormhole (Group and Name only, about a third of the height; call `adjustSize()` when Group changes) · 6 the dropdown open.

**Type dropdown:** Unknown type · K162 (`exit · opened from the other side`) · separator · this system's statics · separator · every type that can spawn in this class, A–Z · separator · `Show all types …` in `accent`, which adds every other type dimmed in `ink-faint`. The selected row is `accent-dim` with a 2px `accent` left edge (the delegate draws it). Type-ahead jumps to codes. Popup at least 440px wide, 14 visible rows.

**Answers to the open questions:**
- **Yes, Lifetime and Mass use the edge language.** Each segment has the map's own edge pen painted as a 36×8 icon above the label, and the label carries the edge chip (`R`, `CRIT`, `?`, `◷ <4h`, `◷ <1h`, `✕ EXP`). So the dialog teaches what the map shows. The selected segment is a 2px `accent` border on `accent-dim`, not a solid accent fill, so the chip colours stay readable. Size has no edge pen (only S changes the map), so it's plain labels with `S` in `edge-frig`.
- **Expired on the map:** keep the `✕ EXP` chip in `danger`, drop the lifetime casing and draw the mass line at 40% opacity, so the hole reads as fading out. < 1h gets a denser casing (a dot every 5px instead of 9px). See ConnectionEdge.

**Rules:**
- Changing the lifetime state restarts its clock; saving the same state again doesn't. The help line under Lifetime says when the state was set and when the connection will be removed (4h / 1h / 10 min after).
- Tooltips on segments give the thresholds (> 50%, < 50%, < 10%; ≤ 5/62/375 kt per jump).
- Enter = Save, Esc = Cancel. Delete sig doesn't ask (the row disappearing is the feedback); see ConfirmDialog for the Undo toast.
- In the SignatureTable, an unlinked WH sig's Name column shows what was entered: `→ C3 · M · ◷ <1h · R` (size only for K162, `F` for frigate holes).
