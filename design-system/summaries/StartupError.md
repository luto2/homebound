# StartupError mockup

Error dialog for missing map data. Replaced in the app by BuildDataDialog.

Group: Feedback
Mockup: `design-system/previews/StartupError.html`
Spec: `design-system/components/StartupError/README.md`

Shows: The terminal-only dialog with its code box.

Implemented by:

- `_code_box` in `homebound/ui/dialogs.py`
- `UniverseDataError` in `homebound/universe/db.py`
- `open_universe` in `homebound/universe/db.py`

Note: Superseded: the app shows BuildDataDialog. This mockup stays as the reference for the code box.
