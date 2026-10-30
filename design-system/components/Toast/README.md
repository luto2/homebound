# Toast

A non-modal notice in the map window's bottom-right corner. It never steals focus from the game.

- 360px, `bg-3`, `radius-3`, 1px `line-2`, `shadow-toast`. A round glyph states the kind: `?` question (`warning`), `✓` done (`success`), `✕` error (`danger`).
- **Question toasts** ("Unknown jump — add as WH connection?") have one primary and one secondary action and a countdown bar; when it runs out the default applies (configurable: add or ignore).
- Info toasts last 2.5 s with no buttons. At most 3 stacked, newest at the bottom.
- Qt: a child `QFrame` of the main window positioned in `resizeEvent`; for when the window is hidden, mirror it with `QSystemTrayIcon.showMessage` (KDE renders it as a Plasma notification).
