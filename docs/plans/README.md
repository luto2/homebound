# Plans

Design and fix plans for Homebound. Open plans sit in this folder; finished ones move to
[`done/`](done/) with a status line that says when they were built and where the build differs from
the plan. Bugs and loose ends without a plan yet are in [`TODO.md`](../../TODO.md). Where each feature
lives in the code is in [`docs/code-map.md`](../code-map.md).

When you finish a plan: set its status line to "implemented (date)", list the deviations, move it to
`done/` with `git mv`, fix the relative links in and to it, and move its row in this file.

## Open

### New features and owner tasks (`TODO.md`, human-added tasks)

| Plan | Task | State |
|---|---|---|
| [plan-zkill-details.md](plan-zkill-details.md) | 1. Show what the zKill activity was | not started |
| [plan-menu-icon.md](plan-menu-icon.md) | 2. App menu icon (To do 14) | icons and cache refresh done; cause and desktop test open |
| [plan-watched-systems.md](plan-watched-systems.md) | 5. Watched systems with a connection alert | not started |
| [plan-disconnect-connection.md](plan-disconnect-connection.md) | 5.1. Remove a connection, keep the systems | not started |
| [plan-online-indicator.md](plan-online-indicator.md) | 6. Online indicator from open clients | not started |
| [plan-system-alias.md](plan-system-alias.md) | 7. Persistent system aliases | not started |
| [plan-combat-scan.md](plan-combat-scan.md) | 8. Combat-scanned ships (and the paste bug behind it) | not started |
| [plan-structure-list.md](plan-structure-list.md) | 9. Structure list, fed by combat scans | not started, after 8 |
| [plan-return-hole.md](plan-return-hole.md) | 12. Mark a sig as the return hole, with its type | not started |
| [plan-stable-hole-names.md](plan-stable-hole-names.md) | 14. Names never change (and a reported rename) | not started, before 5.1 |
| [plan-overlay-shortcut.md](plan-overlay-shortcut.md) | Global shortcut to cycle the overlay mode | not started |
| [plan-code-signing.md](plan-code-signing.md) | Windows code signing | not started |

### Audit fixes (`TODO.md` "To do", findings in `docs/findings/findings.md`)

| Plan | To do item | State |
|---|---|---|
| [plan-corrupt-map-lockout.md](plan-corrupt-map-lockout.md) | 1. Corrupt map locks the app out | proposed |
| [plan-backup-uri.md](plan-backup-uri.md) | 2. Backups empty with `#` or `?` in the path | proposed |
| [plan-test-segfault.md](plan-test-segfault.md) | 3. Test suite segfaults | root cause not confirmed |
| [plan-damaged-backup-restore.md](plan-damaged-backup-restore.md) | 5. Damaged backup breaks Restore | proposed |
| [plan-prune-oserror.md](plan-prune-oserror.md) | 6. `prune` raises out of a finished backup | proposed |
| [plan-zkill-worker.md](plan-zkill-worker.md) | 7. zKill worker dies silently | proposed |
| [plan-duplicated-logic.md](plan-duplicated-logic.md) | 9. Duplicated logic that can drift | not started |
| [plan-system-sender.md](plan-system-sender.md) | 12. Any `EVE…` sender counts as the system | not started |
| [plan-small-fixes.md](plan-small-fixes.md) | "Small items" | partly done (`3d65485`) |
| [plan-test-cleanup.md](plan-test-cleanup.md) | Weak tests and test structure | not started |
| [plan-tooling.md](plan-tooling.md) | Type checker, graphify | not started |

### Drafts

| Draft | State |
|---|---|
| [legal/](legal/) (Impressum, Datenschutz, Terms) | WIP; to be checked before the sync service goes public |

## Done

| Plan | Built | Left open |
|---|---|---|
| [done/plan-rescan-status.md](done/plan-rescan-status.md) | 2026-11-01 | the optional overlay marker |
| [done/plan-backups.md](done/plan-backups.md) | 2026-11-01 | — |
| [done/plan-windows-support.md](done/plan-windows-support.md) | 2026-11-01 | a run on a real Windows machine |
| [done/plan-packaging.md](done/plan-packaging.md) | 2026-11-01 | signing ([plan-code-signing.md](plan-code-signing.md)) |
| [done/plan-dedup.md](done/plan-dedup.md) | 2026-11-02 | — |
| [done/plan-test-fixes.md](done/plan-test-fixes.md) | 2026-11-02 | — |
| [done/cloud-sync-plan.md](done/cloud-sync-plan.md) | 2026-11-08 (phases 0–9) | web view (phase 10), open questions in §20 |
| [done/sync-protocol.md](done/sync-protocol.md) | 2026-11-08 | — |
| [done/hosting-hetzner.md](done/hosting-hetzner.md) | 2026-11-08 | legal drafts (§14) |
| [done/plan-tray-overlay-off.md](done/plan-tray-overlay-off.md) | 2026-11-10 | — |
| [done/plan-copy-bookmark-text.md](done/plan-copy-bookmark-text.md) | 2026-11-10 | — |
| [done/plan-sig-group-collapse.md](done/plan-sig-group-collapse.md) | 2026-11-10 | auto-clearing "new" (open decision 1) |
| [done/plan-log-filename.md](done/plan-log-filename.md) | 2026-11-10 | — |
| [done/plan-bad-settings.md](done/plan-bad-settings.md) | 2026-11-10 | — |
| [done/plan-naming-scrim.md](done/plan-naming-scrim.md) | 2026-11-10 | — |
| [done/plan-appimage-ssl.md](done/plan-appimage-ssl.md) | 2026-11-10 | a run of the AppImage on Arch |
| [done/plan-multi-select-move.md](done/plan-multi-select-move.md) | 2026-11-10 | multi-delete |
| [done/plan-paint-tests.md](done/plan-paint-tests.md) | 2026-11-10 | the old `render()` helper (open decision 4) |

Four of the open audit plans overlap in the backup and store code; read their "Overlaps" sections before
starting either: corrupt-map lockout, backup URI, damaged-backup restore and prune errors.
