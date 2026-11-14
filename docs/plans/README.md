# Plans

- **Audience:** the owner and coding agents.
- **Scope:** the index of open and finished plans, and how to finish one. The plans themselves are the other files in this folder.
- **Kept current:** checked against `8c371b5` (2026-11-14). Update it when a plan is added, finished or moved.

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
| [plan-code-signing.md](plan-code-signing.md) | Windows code signing | not started |
| [plan-logging.md](plan-logging.md) | Diagnostic log file for the client | not started |
| [plan-tray-caption.md](plan-tray-caption.md) | Empty "OVERLAY MODE" caption in the Plasma tray menu | not started |
| [plan-docs-rework.md](plan-docs-rework.md) | Documentation structure and guidelines | not started |

### Audit fixes (`TODO.md` "To do", findings in `docs/investigations/2026-11-06-audit-findings.md`)

| Plan | To do item | State |
|---|---|---|
| [plan-small-fixes.md](plan-small-fixes.md) | "Small items" | partly done (`3d65485`) |
| [plan-test-cleanup.md](plan-test-cleanup.md) | Weak tests and test structure | not started |

### Drafts

| Draft | State |
|---|---|
| [docs/legal/](../legal/) (Impressum, Datenschutz, Terms) | WIP; to be checked before the sync service goes public |

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
| [done/plan-watched-systems.md](done/plan-watched-systems.md) | 2026-11-12 (`d1fed36`) | Watch section look, hit toast and desktop notification; removal of a cut-off piece while a member is inside without sharing |
| [done/plan-combat-scan.md](done/plan-combat-scan.md) | 2026-11-12 (`0402ae7`) | a real combat-probe paste (English and German) |
| [done/plan-structure-list.md](done/plan-structure-list.md) | 2026-11-12 (`0402ae7`) | timer toast in the running app |
| [done/plan-overlay-shortcut.md](done/plan-overlay-shortcut.md) | 2026-11-12 (`d28cf66`) | a real key press on KDE/Wayland and Windows |
| [done/plan-menu-icon.md](done/plan-menu-icon.md) | 2026-11-12 (`e6b86d3`) | — |
| [done/plan-tooling.md](done/plan-tooling.md) | 2026-11-12 (`597e10e`) | hooks path check by hand; mypy to zero, then blocking; upstream graphify issues |
