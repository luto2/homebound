# NamingDialog mockup

Asks "Was this the static?" when a character enters an unmapped system, with the likely answer selected.

Group: Screens
Mockup: `design-system/previews/NamingDialog.html`
Spec: `design-system/components/NamingDialog/README.md`

Shows: The question over a scrim, both answers with their resulting names, the reason for the guess, and the name field.

Implemented by:

- `NamingDialog` in `homebound/ui/dialogs.py`
- `queue_naming` in `homebound/ui/map_window.py`
- `_next_naming` in `homebound/ui/map_window.py`
- `Scrim` in `homebound/ui/map_window.py`
- `NamingRequest` in `homebound/mapping/tracker.py`
- `preview_names` in `homebound/mapping/tracker.py`
- `apply_naming` in `homebound/mapping/tracker.py`
- `guess_static` in `homebound/mapping/naming.py`
