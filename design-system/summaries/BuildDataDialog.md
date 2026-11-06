# BuildDataDialog mockup

First-run dialog that downloads the map data and builds it, before the main window exists.

Group: Feedback
Mockup: `design-system/previews/BuildDataDialog.html`
Spec: `design-system/components/BuildDataDialog/README.md`

Shows: The dialog before, during and after a failed build: Download, a progress bar, the status line, Retry, and the terminal command in a code box as a fallback.

Implemented by:

- `BuildDataDialog` in `homebound/ui/dialogs.py`
- `BuildWorker` in `homebound/ui/dialogs.py`
- `main` in `homebound/__main__.py`
- `build` in `homebound/universe/build_static.py`
