# Plan: hole numbers and system names never change

Status: implemented (2026-11-10, commits `06f76b7`…`8495554`), see "As built". Deviations: `label`, `static` and `next_hole` are local-only until the protocol gets a version gate (`protocol.LOCAL_SYSTEM_FIELDS`), so a2a/letter names and STATIC badges can differ between cloud members; numbers sync. The a2a position letter is never given twice. Open owner questions are in the session report (migration numbers on cloud maps, a remote scheme change asking to rename, renumber action for duplicate numbers, k-space home's first chain is A).

Depends on: nothing. Do it before [`plan-disconnect-connection.md`](plan-disconnect-connection.md)
(task 5.1), which removes connections and would hit path 5 below. Touches `mapping/naming.py`,
`mapping/tracker.py`, `mapping/signature_parser.py`, `mapdb/store.py` (schema and sync fields),
`sync/protocol.py`, `ui/signature_table.py` and `ui/map_window.py`.

## The rule (decided by the owner, 2026-11-10)

1. **A name never changes on its own.** A wormhole signature gets its bookmark number the moment it is
   pasted, and a system gets its name the moment it is attached. Nothing later (another paste, a
   static guess, a removed connection or system, a sibling added or removed) changes either. Only an
   explicit user action may: a rename, a scheme change in Settings, "Make home", marking a sig as the
   return hole ([`plan-return-hole.md`](plan-return-hole.md), it becomes `-0`).
2. **No slot is reserved for the static.** Holes are numbered in the order they are found, from 1. The
   static is whatever number it got when it was pasted; it never moves to -1 later.

## Where names change today (from the code, 2026-11-10)

Each of these breaks rule 1. The reporter uses the default numeric scheme, so path 4 is ruled out for
them; 1, 2, 3 and 5 all fit. In the numeric scheme a name is the path of hole numbers from home, so one
changed number renames that system **and everything behind it** (`123` → `143`, `1231` → `1431`).

1. **Unreserved WH sigs only have a preview number.** `SignatureModel._provisional_indices` numbers the
   unlinked WH sigs on every refresh, in signature ID order, after the static slots. It is not stored.
   A new sig with a lower ID, a sig getting its type (and moving to the static slot), or a sig going
   away renumbers the others, and two clients can show different numbers. The number is only stored
   when the bookmark is copied (`reserve_index`) or the sig is linked.
2. **Attaching a system overwrites the sig's number.** `Tracker._attach` computes a fresh
   `next_hole_index` and passes it to `link_signature`, which writes it onto the sig.
   `used_hole_indices` counts the sig's own number as taken, so a sig whose bookmark was copied as `-3`
   ends up as the lowest other free number (`-2`, `-4`, or `-1` if the guess says static). The naming
   dialog's answer does the same through `Tracker.apply_naming` → `link_signature`.
3. **Static or not moves the number.** `Tracker.apply_naming` frees the index and picks a new one when the
   answer says static and the current index isn't a static slot, or the other way round.
   `guess_static` at attach time does the same choice up front.
4. **Level letters are derived, not stored.** The `a2a` scheme's last letter comes from
   `level_positions`, which orders the systems at a depth by `added_at` every time. Removing one shifts
   the letters of the later ones (`A2c` becomes `A2b`). This is "removing a connection shuffles the
   naming down the chain".
5. **Detached systems lose their number.** `Store.clear_broken_parents` (after a connection is
   removed) and `Store.remove_system` (for the children) set `hole_index` to None. When the system
   is attached again, it gets the next free number. Undo of a removal (`_insert_row`) does the same
   when the parent is gone.
6. **Freed numbers are reused.** `used_hole_indices` only counts what exists now, so after a sig is
   deleted its number goes to the next new hole, while the old bookmark may still be in people's
   in-game folders.

## Change

1. **Number at paste.** In the paste merge, every WH sig without a number gets one, in any system,
   stored on the signature, in signature ID order (so two clients pasting the same scan agree, see 8).
   A sig whose group is still unknown gets its number when it is first set to wormhole (by a paste or
   by hand). `_provisional_indices` and the dry-run branch of `bookmark_for` go away.
2. **Count up per system, never fill gaps.** A `next_hole` counter on the system row, bumped in the
   same batch as the assignment. Numbers of deleted sigs and holes are not reused.
3. **The link takes the sig's number.** `_attach`, `apply_naming` and `link_signature` use the sig's stored
   number for the system. Only a system attached without any sig gets the next number from the
   counter.
4. **Static is a flag, not a slot.** Drop the reserved slots from `next_hole_index`,
   `static_slot_count`/`static_slot_for_class` callers and `preview_names`. Whether a hole is the static
   is stored as a `static` flag on the system (set by `guess_static` or the naming dialog) and shown as
   a badge; it no longer changes the number.
   `chain_letter` then maps hole numbers in found order (1 → A, 2 → B, 3 → Z, …), and the scheme
   descriptions in `SCHEMES` lose "the static is always 1" and "A = static".
5. **Freeze the name.** Store the label on the system when it is attached (a `label` column), and have
   `Namer.label` read it. The `a2a` level letter is computed once at attach time and is part of the
   stored label. Detaching keeps the label and `hole_index`; attaching again under the same parent
   keeps them too.
6. **Explicit renames only.** A scheme change in Settings asks "Rename all systems with the new scheme?"
   and rewrites the labels in one batch (one undo step). "Make home" (`_set_home`) asks the same for
   the re-rooted chain. A per-system "Rename" keeps working through `custom_name`.
7. **Migration.** Existing maps: fill `label` from today's computed name and `next_hole` from the
   highest number in use, so nothing visible changes on upgrade.
8. **Sync.** `label`, `static` and `next_hole` are new synced fields on `system`, and `hole_index` is
   now written at paste time. Two clients pasting the same new sigs at once can assign different
   numbers; the per-field merge keeps one, so a client may briefly show a number that then changes.
   Lower the chance by numbering in the order of signature ID within a paste (both clients then agree
   when they paste the same scan), and show a warning when two sigs in one system share a number.

## Tests

- A paste of three WH sigs stores 1, 2, 3; a second paste with a new sig of lower ID keeps them and
  gives it 4. Fails today (path 1).
- Copy a sig's bookmark as `-3`, attach a system through it: the system is `-3`. Fails today (path 2).
- Answer the naming dialog with "static" for a hole numbered 2: it stays 2 and is flagged static. Fails
  today (path 3).
- `a2a`: three systems at one depth (`A2a`, `A2b`, `A2c`); remove the second: the third is still `A2c`.
  Fails today (path 4).
- Remove a connection and add it back: the system keeps its number and label (path 5).
- Delete sig 2, paste a new WH sig: it gets 4, not 2 (path 6).
- Scheme change: labels change only after confirming; undo brings the old ones back.
- Migration: an old map shows exactly the names it showed before.
- Sync: two replicas that paste the same scan end up with the same numbers.

## Decided (owner, 2026-11-10)

- **Chain letters in found order.** In the letter schemes hole 1 → A, 2 → B, then Z, Y, … as now; A no
  longer means "static". The static is shown by its badge.
- The reported change happened under the default **numeric** scheme (paths 1, 2, 3 or 5).
- **Every pasted WH sig is numbered at once**, in any system, mapped or not: its number and bookmark
  name exist from the second it is pasted (change step 1).

No open decisions left.

## As built

- **Change 8 is only half done.** Hole numbers sync (signature `hole_index` and the system placement
  were synced fields already), so numbers and the numeric names agree between installs, and the
  same scan pasted on two installs gets the same numbers. `label`, `static` and `next_hole` stay
  local (`protocol.LOCAL_SYSTEM_FIELDS`, with a TODO): `op_from_json` refuses a field it doesn't
  know, so the server out there would reject every batch with one, and an older client would fail
  on every `ops` message with one and ask for it again forever. Syncing them needs a protocol
  version gate first (bump `PROTOCOL`, strip the fields for older connections, send them only to a
  server that knows them). Meanwhile each install names systems placed by others itself, when
  they arrive (`AppState._name_new_systems`), and counts numbers it receives into its own counter
  (`Store._raise_hole_counters`).
- Two sigs with the same number (two members pasting different new sigs at once) get a ⚠ on
  their copy button and a tooltip; nothing is renumbered.
  **2026-11-12 (owner):** on a cloud map the sig whose number reached the server first keeps it, and
  the server gives the later one the next free number (`merge.settle_hole_numbers`, sync-protocol.md
  §6 step 8); its member gets a warning. Already synced numbers win over edits made offline. The ⚠
  stays for duplicates from before that server version.
- A WH sig that comes without a number (from an older cloud client) shows the number it would get
  and stores it when its bookmark is copied, or at the next paste in that system.
- A system that comes back without a sig keeps its old number while no hole of the new parent
  has it, even if the parent is a different one. **Changed 2026-11-12 (owner):** only under the
  same parent. A system re-linked under a new parent gets that parent's next number (or its sig's)
  and a new name, so its bookmark fits the system it hangs off; its children keep their names. The
  parent it hung off is kept in the local column `systems.detached_from` (set by `Store._update_row`
  whenever a system loses its parent, sync included; `None` for systems detached before that, which
  keep the old rule).
- A scheme change from another cloud member asks the rename question too.
- Marking a return hole ([`plan-return-hole.md`](plan-return-hole.md)) is the one explicit rename of a sig: its bookmark reads `-0` (towards the parent) while linked, but its stored number is kept, so nothing is lost on undo.
- **2026-11-11: change 8 is done.** Protocol 2 has the version gate (`docs/plans/done/sync-protocol.md` §3),
  and `label`, `static` and `next_hole` sync with servers and clients that speak it. `next_hole` merges as
  the highest value. A system from an older client still arrives without a label and is named by each
  install itself; that name stays local (not pushed), and a remote `null` label never clears one. Labels
  that existed before the upgrade stay local until someone renames the system: nothing is backfilled, so
  no install's names change at the upgrade.
- **2026-11-12 (owner): one backfill per cloud map file.** Names and numbers only one computer has
  (labels from before protocol 2, names given to systems an older client placed, a static flag never
  synced, a higher hole counter, and the bookmark numbers the upgrade of an old map file fills in for
  unlinked sigs) go to the server once, at the first join with a protocol 2 server
  (`LocalReplica.backfill`, `SyncEngine.backfill`; key `names_backfill` in the file's `key_value`). The
  legacy freeze marks it due again, so a map file upgraded later sends its numbers too. Each op is
  stamped just above the stamp of the value it fills, so it only fills what is empty on the server: a
  value set there meanwhile wins, and two installs backfilling one field agree. One install's local
  names can change once, to what the other install sent first.
