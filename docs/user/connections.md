# Connections, editing by hand and undo

- **Audience:** players using Homebound.
- **Scope:** Connection state, drawing and removing systems and connections by hand, and the Undo toast. The other topics are on the [contents page](README.md).
- **Kept current:** checked against `702f2d5` (2026-11-14). Update it when the behaviour it describes changes.

## Connections

Right-click a connection for the quick menu:
- **Edit connection …** (or select it and press Enter) opens the connection editor: Type, Size (K162 only), Lifetime and Mass, the same fields as the signature editor.
- **Mass**: Fresh, Reduced, Critical.
- **Lifetime**: Stable, < 4h, < 1h, Expired.
- **✕ Collapse** removes the connection, the sigs on both sides, and any system left with no connections (except home and pinned systems). Undo is in the toast.

**Expiry.** Connections are removed automatically:
- after their wormhole type's lifetime, or 48h if the type is unknown
- 4h / 1h / 10 min after they were marked < 4h / < 1h / expired

## Editing the map by hand

- **Add a system**: right-click the map background → *Add system …* (or press Ins).
  - The system name autocompletes over every system. Systems already on the map are listed but greyed out.
  - Tab takes the highlighted entry.
  - The line under the field says what will happen, for example "✓ J163891 · C3 · added with a WH connection to A2".
  - If a system is selected, the new one is connected to it.
- **Draw a connection**: hold Shift and drag from one system to another, or right-click a system → *Draw connection from here*, then click the other system. Esc cancels.
- **Rename** (F2): sets a custom name. Leave it empty to go back to the scheme name.
- **Set as home**: makes the system the root of the naming scheme.
- **Remove from map** (Del): removes the system and its connections. With several systems selected, Del (or the menu's "Remove N systems") removes them all in one step. Home is kept. Undo is in the toast.
- **Request zKill now** refreshes its kill activity.

**Unconfirmed.** Systems and connections you add by hand get a dashed orange border and a `▲ UNCONFIRMED` tag, as do both ends of a hand-drawn connection. A character jumping the connection, or arriving in the system, confirms it. Right-click → **✓ Mark as confirmed** does it by hand.

Disabled menu items stay visible. Hover them to see why they're disabled.

## Undo

Only deleting a whole map asks for confirmation first. Everything else that removes something acts at once and shows an **Undo toast** for 8 seconds:
- Collapse a connection
- Remove a system
- Delete sigs: selected, missing, or from the editor

Click **Undo** or press **Ctrl+Z** while the toast is up. Hovering the toast pauses the countdown. There is one level of undo: a new removal replaces the previous toast. Things that happened in between, such as a jump, are kept.
