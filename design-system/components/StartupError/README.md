# StartupError

A small standalone Dialog shown before the main window exists, when `data/universe.sqlite` is missing. It replaces `QMessageBox.critical`.

- 440px, no close cross (Quit is the only way out). Danger glyph `!` in a 22px `danger` circle left of the title; this is the only dialog with a header glyph, because it's the only one that stops the app.
- Title says what is wrong in plain words ("Map data is missing"); the subtitle names the file and why it's needed.
- The fix is one command, so it gets a **code box**: `bg-0`, 1px `line-2`, mono 11px (`mono-small`), a faint `$` prompt and an icon button `⧉` (tooltip "Copy command") that puts the command on the clipboard (`QGuiApplication.clipboard().setText`) and shows `✓` for 2 s.
- Footer: project path in mono on the left, `Open folder` (ghost, `QDesktopServices.openUrl`) and `Quit` (primary).
- Qt: build it with the app stylesheet already applied (`app.setStyleSheet(build_qss())` runs before the check), so it matches the rest of the app.

Later this can become a first-run screen with a progress bar if building the static data moves into the app; the layout stays the same, the code box becomes a `Build now` button and a `QProgressBar`.
