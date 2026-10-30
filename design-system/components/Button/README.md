# Button

Push buttons and tool buttons: `primary` for the one action a view is for, default for everything else, `danger` for destructive actions, `ghost` for dismissals.

- Height `size-control` (26px), radius `radius-2`, 1px `line-2` border on `bg-3`; hover `bg-4`; pressed `bg-2`.
- **Primary**: `accent` fill, `ink-inverse` text, weight 600. At most one per dialog or panel ("Paste sigs", "Yes, static", "Save").
- **Danger**: `danger` border and text on `danger-dim`, always with a ✕ glyph and a count ("✕ Delete 3 missing").
- **sm** (20px) for in-row actions such as the per-sig "⧉ 123-1" copy button; **icon** (26×26) for toolbar glyphs — always give a tooltip.
- Show the shortcut in an `lm-kbd` chip when one exists. Labels are verb-first, sentence case.
- Focus: 2px `accent` ring outside the control (`border-focus`). Disabled: `ink-disabled` on `bg-2`, `line-1` border.

Qt: `QPushButton`, `QToolButton`; variants via `setProperty("variant", "primary")` and QSS `QPushButton[variant="primary"]`. Re-polish after changing the property.
