# CharacterMarker

A 14px shape that shows where each character is; slot N always pairs `char-N` with the same shape.

| Slot | Shape | Colour |
|---|---|---|
| 1 | circle | `char-1` |
| 2 | square | `char-2` |
| 3 | triangle | `char-3` |
| 4 | diamond | `char-4` |
| 5 | hexagon | `char-5` |
| 6 | star | `char-6` |

- 1px `ink-inverse` stroke so markers stay separate when they stack on a node's top edge.
- More than 3 characters on one node: show 2 markers and a "+N" chip.
- The character dock and the overlay header repeat the marker beside the name, so the shape-to-name link is always visible.
