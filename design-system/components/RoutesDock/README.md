# RoutesDock

Left dock under Characters. The routes from the selected system (or, with nothing selected, the followed character's system) to each destination.

- **Header**: "From <map name> (<system>)" in `ui-small`, `ink-muted`, with " (followed character)" when no system is selected. With no origin: "Select a system or follow a character". A ghost `⚙` button (tooltip "Route options") opens the options menu.
- **Row** (40px, `line-1` rule below; hover `bg-4`, selected `accent-dim`):
  - Line 1: class badge with security, destination name in `ui-body` bold `ink` (elided before the jumps), jumps right-aligned in `mono-small` (`12 j`; `—` in `ink-faint` without a route).
  - Line 2: the route strip, one 8px square per system after the origin with a 2px gap, filled with `sec-10` … `sec-00` (k-space) or the class colour (w-space). A system entered through a wormhole is a round dot with an `ink-muted` ring; an EVE-Scout hole has a `class-thera` ring. Long routes shrink the step down to 3px, then are cut off. Without a route, "no known route" in `ink-faint`.
- **Strip squares are interactive** (`route-hover` branch):
  - Hover a square: a tooltip with that system's name and security or class, and how it is entered ("· wormhole", "· EVE-Scout Q063, Large, 9h left"). The hit area is the whole step plus 2px above and below, so 3px squares can be hit.
  - Click a square with the left button: copies that system's name, with the toast `Copied Perimeter` (ok).
- **Row tooltip** (elsewhere on the row): every system on the way, one per line (`→ Perimeter · 0.9 …`), "You are here" for the origin, or "No known route: no stargate, mapped wormhole or EVE-Scout connection leads there".
- **Footer**: a text field "Add destination (any system)" with a completer over every system name (and map names), and an Add button. Enter adds. Unknown systems and duplicates show an error toast (`✕ Xyz isn't a known system`, `✕ Jita is already a destination`).
- **Double-click** a row copies the whole route as names (`Jita → Perimeter → Thera → …`) with the toast `Copied route, 3 jumps`.
- **Right-click a row:**
  - Copy route `Double-click` (disabled: "There is no known route.")
  - Move up · Move down (disabled at the ends)
  - ― · ✕ Remove destination (danger, last)
- **Options menu** (⚙): `PREFER` (Shortest · Safer · Less secure, exclusive ●) · `USE` (EVE-Scout Thera && Turnur · Holes at end of life · Mass-critical holes, checkable) · `SHIP SIZE` (Frigate · Medium · Large · Capital, exclusive ●) · ― · Reset destinations to the trade hubs. The same items are on the Settings → Routes page.
- Routes recalculate when the origin, the map, the settings or the EVE-Scout data change.
