# SigEditor mockup

Dialog for what the scanner can't show: wormhole type, lifetime, mass and, for a K162, destination and size.

Group: Feedback
Mockup: `design-system/previews/SigEditor.html`
Spec: `design-system/components/SigEditor/README.md`

Shows: Five states plus the type dropdown: unknown type, K162, known type, linked, and not a wormhole.

Implemented by:

- `SignatureDialog` in `homebound/ui/dialogs.py`
- `WormholeFields` in `homebound/ui/wormhole_fields.py`
- `WormholeTypeCombo` in `homebound/ui/wormhole_fields.py`
- `DestinationCombo` in `homebound/ui/wormhole_fields.py`
- `Segmented` in `homebound/ui/wormhole_fields.py`
- `save_signature` in `homebound/mapping/tracker.py`
- `edit_signature` in `homebound/ui/system_panel.py`
