# Naming and bookmarks

- **Audience:** players using Homebound.
- **Scope:** The naming schemes, the "was this the static?" question and bookmark names. The other topics are on the [contents page](README.md).
- **Kept current:** checked against `702f2d5` (2026-11-14). Update it when the behaviour it describes changes.

## Schemes

Pick one in the toolbar or in Settings. It applies to the active map.

| Scheme | Example chain | Notes |
|---|---|---|
| Numeric path | Home → `1` → `12` → `123` | Each hop adds the hole number. |
| Chain · depth · position | Home → `A1a` → `A2a`, `A2b` | Chain letter, depth, and the system's letter at that depth. |
| Letter-led numeric | Home → `A` → `A2` → `A23` | Like numeric, but the first hop is a letter. |

Holes are numbered in the order you find them, from 1: every wormhole sig gets its number the moment you paste it, in any system, and a system takes the number of the sig you jumped through. Numbers count up per system and are never given again, even after a sig is deleted, so an old bookmark in your folder can't end up on another hole.

Chain letters follow EVE University's letters in the order home's holes were found: hole 1 is `A`, hole 2 is `B`, then `Z, Y, W, U, S, R, Q, O, M, K, I, E, D`, then doubled letters. C, F, G, H, J, L, N, P, T, V and X are never used, because they're easy to confuse. (EVE Uni keeps A and B for the statics; Homebound marks the static with a badge instead.)

K-space systems show the scheme name plus the real name: `1 Jita`.

**Names never change on their own.** A system gets its name when it is attached and keeps it when the map around it changes: when a sibling goes, a connection is removed and added back, or a system is removed and brought back. Only you rename:
- **Rename …** (F2) sets a custom name.
- Changing the scheme asks **Rename all systems with the new scheme?**. Keep names leaves them; new systems get the new scheme's names either way. Rename can be undone.
- **Set as home** asks the same for the chain behind the new home.

## "Was this the static?"

Whether a hole is the static is a flag, not a number: the system behind it gets a `STATIC` badge, and its number stays what it was. When a new system could be behind the static, Homebound asks:
- The best guess is preselected with its reason.
  - Example: "Static H296 → C5 is already mapped".
- If the system you jumped from has several unlinked wormhole sigs, it also asks which one you took. Only sigs that can lead to the class you landed in are listed. Picking one gives the system that sig's number, and the name shown changes with it.
- You can edit the name before accepting.
- Keys: **Enter** accepts, **Y** / **N** answer yes or no, **Esc** skips.
- If more jumps arrive while the dialog is open, they queue up ("1 more pending").

Homebound doesn't ask when there's nothing to decide: when the static can't lead to the class you landed in and no sig could have, or when exactly one sig fits (it's linked by itself). Turn on **Don't ask when the guess is certain** to also skip the question when the guess is certain: an Undo toast shows the name it picked.

To fix a wrong answer later, right-click the system → **Change static / hole …**.

## Bookmark names

Every wormhole sig in the table has a copy button such as `⧉ 12-1`:
- The hole with number *n* in system *S* is `S-n`; the number is the sig's, given when it was pasted.
- The hole leading back towards home is `S-0`.
- The button copies the whole line for the in-game bookmark: `<number> <sig> <class>`, for example `12-1 ABC C3` (the sig is its three letters, the class is where the hole leads). Without a known class it copies `12-1 ABC`. The button shows only the number; its tooltip shows the text it copies.
