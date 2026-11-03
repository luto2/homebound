# BuildDataDialog

The first-run dialog for map data. Shown before the main window when `universe.sqlite` is missing (**Download map data**) or was built by an older version (**Map data needs an update**). It replaces the plain error box in [StartupError](../StartupError/README.md): instead of a command to run, it does the build itself and keeps the command as a fallback.

- 440px, no close cross. `Quit` (ghost, left of the primary) and `Esc` quit; the window's close button does the same.
- **Header:** `ui-title` as above; subtitle in `ink-muted` says what the data is (systems, classes, statics, gates), that it's about 100 MB, and that Homebound works offline afterwards.
- **Progress:** a 6px bar under the footer's text, `accent` fill on `bg-3`. Indeterminate (moving) while the size is unknown, determinate when a download reports its size. Hidden until the build starts.
- **Status line** (`help`, two lines max): the current step, e.g. `Downloading the static export · 42 MB of 100 MB`. After an error it becomes `✕ The build failed: <message>` in `danger`, and the primary button turns into **Retry**.
- **Caption and code box:** `Or run this in a terminal`, then the same `$ uv run python -m homebound.universe.build_static` box as in StartupError with the `⧉` copy button (✓ for 2 s).
- **Footer:** `Quit` (ghost) · `Download` (primary, default). `Download` is disabled while the build runs. Enter = Download.

**Behaviour:** the build runs in a background thread. Quit during a build stops it at the next step or download chunk and waits up to 10 s. A package deletes the downloads afterwards; a checkout keeps them for offline rebuilds

**Qt:** `BaseDialog` with `setWindowFlags(Dialog | CustomizeWindowHint | WindowTitleHint)`; the progress bar is a `QProgressBar` at fixed height 6; the status line uses the `help` / `help-err` roles.
