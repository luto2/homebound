# OverlayStates mockup

The overlay as a whole, over different game backdrops.

Group: Screens
Mockup: `design-system/previews/OverlayStates.html`
Spec: `design-system/components/OverlayStates/README.md`

Shows: Four states: normal, k-space distance, unlocked and empty.

Implemented by:

- `Overlay` in `homebound/ui/overlay.py`
- `should_show` in `homebound/ui/overlay.py`
- `_paint_frame` in `homebound/ui/overlay.py`
- `_paint_header` in `homebound/ui/overlay.py`
- `_paint_empty` in `homebound/ui/overlay.py`
- `resolve_chain` in `homebound/mapping/chain.py`
- `FocusTracker` in `homebound/ui/focus.py`
