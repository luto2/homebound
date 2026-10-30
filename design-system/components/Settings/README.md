# Settings

The settings window. Every option is shown with its live result: a validation line, scheme previews, the KWin script status.

- **Log folder**: path field (mono) + Browse + Auto-detect; ✓ / ✕ help line counting the logs and characters found. Validate on every edit (debounced 300ms).
- **Home system**: autocomplete over known J-names and names; shows class and statics once resolved.
- **Naming scheme**: a radio card per scheme, each previewing the same small chain *and* its bookmark format. The selected card gets `accent-dim` + `accent` border.
- **Overlay mode**: four radio cards with one-line descriptions, a background-opacity slider limited to `op-overlay-bg-min` … `op-overlay-bg-max`, and a text-size control (11–14px).
- **zKill**: one toggle and a plain statement of what is queried.
- **KWin focus script**: three states shown — installed (✓ `success` banner with Reinstall/Remove), installed-but-inactive (▲ `warning` with the exact fix), not installed (Install button + consequence).

The mockup stacks every section on one page; in the app each nav item is a page of a `QStackedWidget`, driven by a `QListWidget` nav.
