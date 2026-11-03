# StartupError

Shown before the main window exists, when `data/universe.sqlite` is missing or was built by an older version. It replaces `QMessageBox.critical`.

**Superseded:** the app now shows [BuildDataDialog](../BuildDataDialog/README.md) instead. It offers **Download** and does the build itself, and keeps the terminal command in the same code box described below as a fallback. The terminal-only layout here is kept as the reference for that code box and the footer.

- 440px, no close cross (Quit is the only way out). Danger glyph `!` in a 22px `danger` circle left of the title; this is the only dialog with a header glyph, because it's the only one that stops the app.
- Title says what is wrong in plain words ("Map data is missing"); the subtitle names the file and why it's needed.
- The fix is one command, so it gets a **code box**: `bg-0`, 1px `line-2`, mono 11px (`mono-small`), a faint `$` prompt and an icon button `⧉` (tooltip "Copy command") that puts the command on the clipboard (`QGuiApplication.clipboard().setText`) and shows `✓` for 2 s.
- Footer: project path in mono on the left, `Open folder` (ghost, `QDesktopServices.openUrl`) and `Quit` (primary).
- Qt: build it with the app stylesheet already applied (`app.setStyleSheet(build_qss())` runs before the check), so it matches the rest of the app.

The "outdated data" case uses the same dialog with the title **Map data needs an update**.
