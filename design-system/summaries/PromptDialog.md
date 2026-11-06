# PromptDialog mockup

One captioned text field with inline validation, used for four dialogs.

Group: Feedback
Mockup: `design-system/previews/PromptDialog.html`
Spec: `design-system/components/PromptDialog/README.md`

Shows: New map, rename map, rename system and add system, with the autocomplete list and its typed-part underline.

Implemented by:

- `PromptDialog` in `homebound/ui/dialogs.py`
- `AddSystemDialog` in `homebound/ui/dialogs.py`
- `system_completer` in `homebound/ui/dialogs.py`
- `_SystemCompleterDelegate` in `homebound/ui/dialogs.py`
- `_TabCompletion` in `homebound/ui/dialogs.py`
- `_map_name_check` in `homebound/ui/map_window.py`
- `new_map` in `homebound/ui/map_window.py`
- `rename_map` in `homebound/ui/map_window.py`
- `_rename` in `homebound/ui/map_window.py`
- `_add_system` in `homebound/ui/map_window.py`
