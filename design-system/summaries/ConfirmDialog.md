# ConfirmDialog mockup

The one action that asks first: deleting a map. Every other destructive action shows an Undo toast.

Group: Feedback
Mockup: `design-system/previews/ConfirmDialog.html`
Spec: `design-system/components/ConfirmDialog/README.md`

Shows: The delete question with its summary of what is lost, and the Undo toast for smaller actions.

Implemented by:

- `ConfirmDeleteMapDialog` in `homebound/ui/dialogs.py`
- `delete_map` in `homebound/ui/map_window.py`
- `offer_undo` in `homebound/ui/map_window.py`
- `ToastHost` in `homebound/ui/toast.py`
- `snapshot` in `homebound/mapdb/store.py`
- `restore` in `homebound/mapdb/store.py`
