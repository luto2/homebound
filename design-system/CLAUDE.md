# Design system: agent rules

The look of Homebound. This folder is not reorganised (see [`docs/plans/plan-docs-rework.md`](../docs/plans/plan-docs-rework.md)); change its content only for a UI change.

## How the parts relate

- `README.md`: the principles, voice, colour and typography rules. Read it first.
- `tokens.json` and `tokens.css`: the values (colours, fonts, spacing). `tokens.json` is the source the app loads; `tokens.css` serves the HTML previews.
- `qt-implementation.md`: how to turn the tokens into QSS and `QPainter` objects with the same names.
- `components/<Name>/README.md` and `preview.html`: one component's spec and a mockup. `summaries/<Name>.md` is the short version of the same spec; `previews/<Name>.html` is a standalone mockup page; `components/bundle.css` and `bundle.js` are the shared preview assets.
- `assets/`: icons and images the mockups and the app use. `missing-mockups/README.md` lists components that have no mockup yet.
- `design-system.json`: the manifest of the design tool that made this folder; it lists the asset groups (Effects, Sites).

## Which file to change for a UI change

1. The value (a colour, a size): `tokens.json`, then `tokens.css` to match.
2. How a component looks or behaves: its `components/<Name>/README.md`, then its `preview.html` and `summaries/<Name>.md`.
3. A new component: a new folder under `components/`, and a `summaries/` page. New icons go in `assets/` (and `design-system.json` if the asset group is new).
4. The code that implements it: `homebound/theme/` and `homebound/ui/`; the docs of those are in [`docs/code-map.md`](../docs/code-map.md).

The look is original; do not borrow the game's UI or logos. The game's own item icons are fine to use.
