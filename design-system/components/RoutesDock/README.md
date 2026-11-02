# RoutesDock

Left dock under Characters. The routes from the selected system (or, with nothing selected, the followed character's system) to each destination.

- **Header**: "From <map name> (<system>)" in `ui-small`, `ink-muted`, with " (followed character)" when no system is selected; a ghost `⚙` button opens the options menu (Prefer · Use · Ship size · Reset destinations).
- **Row** (40px, `line-1` rule below; hover `bg-4`, selected `accent-dim`):
  - Line 1: class badge with security, destination name in `ui-body` bold `ink`, jumps right-aligned in `mono-small` (`12 j`; `—` in `ink-faint` without a route).
  - Line 2: the route strip, one 8px square per system after the origin with a 2px gap, filled with `sec-10` … `sec-00` (k-space) or the class colour (w-space). A system entered through a wormhole is a round dot with an `ink-muted` ring; an EVE-Scout hole has a `class-thera` ring. Long routes shrink the step down to 3px, then are cut off. Without a route, "no known route" in `ink-faint`.
- **Tooltip**: every system with its security or class and how it is entered ("· wormhole", "· EVE-Scout Q063, Large, 9h left").
- **Footer**: a text field "Add destination (any system)" with a completer over every system name, and an Add button.
- Double-click copies the route as names (`Jita → Perimeter → Thera → …`) with an ok toast; right-click: Copy route · Move up · Move down · Remove destination (danger).
