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
| [plan-menu-icon.md](plan-menu-icon.md) | 2. App menu icon (To do 14) | icons and cache refresh done; cause and desktop test open |
| [plan-watched-systems.md](plan-watched-systems.md) | 5. Watched systems with a connection alert | not started |
| [plan-combat-scan.md](plan-combat-scan.md) | 8. Combat-scanned ships (and the paste bug behind it) | not started |
| [plan-structure-list.md](plan-structure-list.md) | 9. Structure list, fed by combat scans | not started, after 8 |
| [plan-overlay-shortcut.md](plan-overlay-shortcut.md) | Global shortcut to cycle the overlay mode | not started |
| [plan-code-signing.md](plan-code-signing.md) | Windows code signing | not started |

### Audit fixes (`TODO.md` "To do", findings in `docs/findings/findings.md`)

| Plan | To do item | State |
|---|---|---|
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
| [done/plan-multi-select-move.md](done/plan-multi-select-move.md) | 2026-11-10 | — |
| [done/plan-paint-tests.md](done/plan-paint-tests.md) | 2026-11-10 | the old `render()` helper (open decision 4) |
| [done/plan-zkill-worker.md](done/plan-zkill-worker.md) | 2026-11-10 | — |
| [done/plan-zkill-details.md](done/plan-zkill-details.md) | 2026-11-10 | a recorded zKill answer for the test data |
| [done/plan-backup-uri.md](done/plan-backup-uri.md) | 2026-11-10 | UNC paths on a real Windows share |
| [done/plan-prune-oserror.md](done/plan-prune-oserror.md) | 2026-11-10 | — |
| [done/plan-damaged-backup-restore.md](done/plan-damaged-backup-restore.md) | 2026-11-10 | decision 2 (`list_backups` catch) and 3 (a "damaged" marker in the list) |
| [done/plan-corrupt-map-lockout.md](done/plan-corrupt-map-lockout.md) | 2026-11-10 | the wording of the new warnings; decisions 2, 4, 5 as proposed |
| [done/plan-test-segfault.md](done/plan-test-segfault.md) | 2026-11-10 | — (test-only; the app can't hit it) |
| [done/plan-stable-hole-names.md](done/plan-stable-hole-names.md) | 2026-11-10 | — (fields sync with protocol 2) |
| [done/plan-duplicated-logic.md](done/plan-duplicated-logic.md) | 2026-11-10 | which home ID the callers use (not confirmed) |
| [done/plan-system-alias.md](done/plan-system-alias.md) | 2026-11-10 | — (aliases sync with protocol 2) |
| [done/plan-multi-delete.md](done/plan-multi-delete.md) | 2026-11-11 | — |
| [done/plan-disconnect-connection.md](done/plan-disconnect-connection.md) | 2026-11-11 | cut-off tab on other cloud members |
| [done/plan-return-hole.md](done/plan-return-hole.md) | 2026-11-11 | origin memory survives restart? marker for child-origin return holes (needs a synced flag) |
| [done/plan-online-indicator.md](done/plan-online-indicator.md) | 2026-11-11 | overlay follow skipping closed clients (step 5) |
| [done/plan-single-instance.md](done/plan-single-instance.md) | 2026-11-12 (`45398b0`) | a run on Windows |
| [done/plan-gate-connections.md](done/plan-gate-connections.md) | 2026-11-12 (`3dfeb27`) | line colour/token and corner look by eye |
| [done/plan-system-sender.md](done/plan-system-sender.md) | 2026-11-12 (`b913596`) | real logs for other client languages (later) |
| [done/plan-overlay-focus.md](done/plan-overlay-focus.md) | 2026-11-12 (`48a1e2e`) | look on screen at 11 and 14 px; follow-mode hide on X11/KWin and Windows |
