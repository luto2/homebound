# Settings mockup

The settings window: a nav list and one page per topic, each showing its live result.

Group: Screens
Mockup: `design-system/previews/Settings.html`
Spec: `design-system/components/Settings/README.md`

Shows: One 820 by 560 window per page, in nav order, with the other outcomes listed where a page has several.

Implemented by:

- `SettingsDialog` in `homebound/ui/settings_dialog.py`
- `Card` in `homebound/ui/settings_dialog.py`
- `Settings` in `homebound/settings.py`
- `apply_settings` in `homebound/ui/state.py`
