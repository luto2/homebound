# TextField

Text inputs, combo boxes, path pickers, checkboxes, radios and segmented choices, all on `bg-3` at `size-control` height.

- Border `line-2`; focus swaps to `accent-hover` plus the 2px `accent` focus ring.
- **Validation** shows on the field and in a help line under it, never by colour alone: `success` border + "✓ …" text, `danger` border on `danger-dim` + "✕ …" text explaining what was expected.
- Paths and J-names use `mono-data`. Placeholder text is `ink-faint`.
- Segmented control (`QButtonGroup` of checkable `QToolButton`s) for 2–4 mutually exclusive values that change often, e.g. mass state in the connection editor; radios for settings that change rarely.
- Labels sit above fields as `ui-label` captions (uppercase, 11px/600).

Qt: `QLineEdit`, `QComboBox`, `QCheckBox`, `QRadioButton`; set `setProperty("state", "valid"|"invalid")` and style `QLineEdit[state="invalid"]`.
