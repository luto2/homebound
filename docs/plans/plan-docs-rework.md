# Plan: rework the documentation structure and guidelines

Status: plan only, nothing implemented yet (2026-11-14). Decisions come from the owner interview on
2026-11-14.

- **Audience:** the owner and the coding agents that build this plan.
- **Scope:** where every doc in the repo lives, the rules for writing and updating them, and the
  agent instruction files. Out of scope: `design-system/` (left as it is, see "Not in this plan").
- **Kept current:** written against `cb44ac6` (2026-11-14).

Depends on: nothing. Touches every Markdown file outside `design-system/`, adds `CLAUDE.md` files,
`CHANGELOG.md`, `docs/architecture.md`, a doc check in `tools/` and its CI step, and the doc paths
cited in code comments.

## Why

The owner named four problems:

1. **Hard to find things.** Legal drafts sit in `docs/plans/legal/`, one-off analyses sit loose in
   `docs/`, the full manual is a 463-line `HOW_TO.md` at the root, and `TODO.md` mixes open tasks
   with "Done" and "Fixed" records.
2. **Docs go stale.** Only `code-map.md` states when it was last checked. Code comments cite plan
   paths that nobody checks; `homebound/settings.py` already cites
   `docs/plans/plan-watched-systems.md`, which does not exist.
3. **Agents need guidance.** There is no `CLAUDE.md`. Rules like "update the code map in the same
   commit" or "move a finished plan to `done/`" are spread over `code-map.md`,
   `docs/plans/README.md` and `TODO.md`, so an agent sees them only if it opens those files.
4. **Public release polish.** The README and HOW_TO repeat the install steps and the feature list,
   and the legal pages are German drafts without English versions.

The docs must help agents find their way around the codebase, and the owner must be able to
understand every doc easily.

## Decisions (owner, 2026-11-14)

| Topic | Decision |
|---|---|
| Agent files | A root `CLAUDE.md` (short: doc map, rules, commands), plus a `CLAUDE.md` in `homebound_server/`, `design-system/`, `packaging/` and `tests/` |
| User guide | Split `HOW_TO.md` into `docs/user/`, one page per topic. Later: a website under the luthopian domain built from `docs/user/` (see "Later") |
| `TODO.md` | Stays at the root, open items only. GitHub Issues may come later for public bug reports |
| Loose docs | `docs/legal/`, `docs/investigations/`, `docs/testing/` |
| Changelog | `CHANGELOG.md`, user-facing |
| Architecture | A separate `docs/architecture.md`; `code-map.md` stays the "where is X" lookup |
| Doc header | Every doc opens with Audience / Scope / Kept current |
| Style | One home per fact; absolute dates with commit hashes; files and symbols, never line numbers; plain sentences that explain terms |
| Language | English. Legal texts in German (binding) and English |
| Updating | In the same commit as the code change, always |
| Design system | Not touched by this plan |

## Target tree

```
README.md                  front door: what it is, install one-liner, screenshots, links
CHANGELOG.md               new: one section per release, written for users
TODO.md                    open items only
CLAUDE.md                  new: agent rules, imports docs/README.md
LICENSE, THIRD_PARTY_NOTICES.md   unchanged

docs/
  README.md                new: map of all docs + the writing guidelines (the one home for both)
  architecture.md          new: how the parts fit together, with a diagram
  code-map.md              unchanged role: where each feature lives
  user/                    from HOW_TO.md, see "User guide split"
  legal/                   from plans/legal/, de + en per page
  testing/                 windows-testing.md, later other manual test scripts
  investigations/          dated one-off analyses: audit findings, core dumps, the TODO sweep
  plans/                   open plans, README.md index, done/
  images/                  unchanged
  private/                 unchanged, still gitignored

homebound_server/CLAUDE.md new
packaging/CLAUDE.md        new
tests/CLAUDE.md            new
design-system/CLAUDE.md    new (only the agent file; the folder's content is not changed)
```

`HOW_TO.md` at the root goes away. `docs/SUS-BM-SCHEME.png` is the sketch for a planned
bookmarking scheme that no doc uses yet (owner, 2026-11-14); it moves to `docs/images/` unchanged,
for the plan that builds that scheme.

## The guidelines

They live in `docs/README.md` under "Writing docs". That file is their only home. The root
`CLAUDE.md` imports it with `@docs/README.md`, so agents get the guidelines in every session and the
owner reads the same text.

### Header block

Every doc opens with its title, then this block (the form `code-map.md` already uses):

```markdown
- **Audience:** who reads this.
- **Scope:** what it covers, and where the neighbouring topics are.
- **Kept current:** checked against `<hash>` (YYYY-MM-DD). <What change makes this doc need an update.>
```

User pages use the same block; "Audience" there is "players using Homebound". Plans keep their
`Status:` line above the block. Generated or archived files (`investigations/`) say so in
"Kept current": "archived, not updated".

### Rules

1. **One home per fact.** Each fact is written in one doc; the others link to it. The install steps
   live in `docs/user/install.md`; the README has only the one-line install command and a link
   (owner, 2026-11-14). The
   feature list lives in the README; the user guide links to it.
2. **Absolute dates and hashes.** Write "2026-11-10 (`1665163`)", never "yesterday", "recently" or
   "the last release".
3. **Files and symbols, not line numbers.** Point to `homebound/settings.py` and `Settings.load`.
   Line numbers go stale with every edit.
4. **Plain sentences, terms explained.** Short, concrete sentences, no filler or marketing tone. EVE
   terms (static, K162, sig, mass state) and tool terms (SDE, KWin script, offscreen Qt) are
   explained the first time a doc uses them, or link to the glossary in `docs/user/glossary.md`.
5. **Relative links.** Link between docs with relative paths, so links work on GitHub, in an editor
   and on the later website.
6. **Same commit.** A commit that changes behaviour, a module, the UI, a command or the protocol
   updates the matching doc (table below) in the same commit and moves its "Kept current" line.

### Which doc goes with which change

| The commit changes… | Update |
|---|---|
| Something a user sees or does | the page in `docs/user/`, and `CHANGELOG.md` under "Unreleased" |
| Where a feature lives (new or moved module, renamed symbol) | `docs/code-map.md` |
| How the parts talk to each other (client, server, sync, SDE, overlay) | `docs/architecture.md` |
| The sync protocol | `docs/plans/done/sync-protocol.md` and the protocol number in `architecture.md` |
| Build, packaging or install | `packaging/` READMEs, `docs/user/install.md` |
| Server deploy or operations | `homebound_server/CLAUDE.md`, the hosting plan; secrets never, notes in `docs/private/` |
| Finishing a plan | the steps in `docs/plans/README.md` (status line, `git mv` to `done/`, fix links, move the row) and the item in `TODO.md` |

## Agent files

The root `CLAUDE.md` is an index with rules, not a guide (about 50–100 lines):

- What Homebound is in two sentences, and `@docs/README.md` for the doc map and the guidelines.
- Commands: a link to the README's "Development" section (run from source, tests, mypy, the push
  gate, the doc check), not a copy of it.
- Commit rules: no `Co-Authored-By: Claude` trailer; docs in the same commit (the table above).
- Where work is tracked: `TODO.md` and `docs/plans/`; TODO numbers are never reused or renumbered,
  because commits, plans and notes refer to them ("TODO 21").
- Model choice per task, as `TODO.md` describes it today (moved here, `TODO.md` links to it).

Each folder `CLAUDE.md` holds only what matters while working in that folder, and links instead of
repeating:

| File | Holds |
|---|---|
| `homebound_server/CLAUDE.md` | how the server is structured; protocol changes deploy the server first, then release the client; `hb-deploy` installs only the latest published GitHub release; the admin CLI and billing; where operations notes live |
| `packaging/CLAUDE.md` | how the AppImage and the Windows build are made; host tools get the user's `LD_LIBRARY_PATH`, not the bundle's (`25bffb7`); what can only be tested by hand on Plasma, Arch and Windows |
| `tests/CLAUDE.md` | offscreen Qt; the segfault history and its fix (`docs/plans/done/plan-test-segfault.md`); `host_environment()` for focus tool calls; timing-sensitive tests; coverage |
| `design-system/CLAUDE.md` | how the mockups, `summaries/`, tokens and `qt-implementation.md` relate, and which file to change for a UI change. Written from how the folder is used today; the folder itself is not reorganised |

## User guide split

`HOW_TO.md` becomes pages in `docs/user/`, with `docs/user/README.md` as the contents page:

| Page | From `HOW_TO.md` |
|---|---|
| `install.md` | 1, plus the install sections now in the root README |
| `main-window.md` | 2 |
| `tracking.md` | 3 |
| `naming.md` | 4 |
| `signatures.md` | 5 |
| `connections.md` | 6, 7, 8 (connections, editing by hand, undo) |
| `maps.md` | 9 |
| `overlay.md` | 10, 11 (overlay, tray icon) |
| `settings.md` | 12, 13 (settings, keyboard and mouse) |
| `data.md` | 14, plus "Network access" from the README |
| `troubleshooting.md` | 15 |
| `glossary.md` | new: EVE and Homebound terms |

Section 16 ("For developers: tooling") moves to the README's "Development" section, its one
home (owner, 2026-11-14). The root `CLAUDE.md` links to it instead of repeating the commands.

Page anchors change, so search for links to `HOW_TO.md#…` (README, `code-map.md`, plans) and point
them at the new pages. Done plans keep their text, but their links are fixed.

## TODO.md

- Keep only open items. Done items are already recorded in their plan's status line, the
  `docs/plans/README.md` "Done" table and git; delete them from `TODO.md`.
- Keep each open item's number. New items take the next free number; gaps are fine.
- "Fixed in the TODO sweep (2026-11-01)" and "Fixed during the cleanup" move unchanged to
  `docs/investigations/2026-11-01-todo-sweep.md`, since they record behaviour changes.
- The effort and model scale moves to the root `CLAUDE.md`; `TODO.md` keeps one line linking to it.

## CHANGELOG.md

- One section per release, newest first, plus "Unreleased" at the top. Groups: Added, Changed,
  Fixed. Written for players: what changed for them, not which module.
- Start with 1.0.0 and every later release, written from the GitHub release notes and the done
  plans.
- The changelog is the release notes (owner, 2026-11-14). `release.yml` uses `--generate-notes`
  today; it switches to `--notes-file` with the tag's section of `CHANGELOG.md`, and the release job
  fails if the tag has no section, so a release can't go out without notes.

## Legal

- `docs/plans/legal/{impressum,datenschutz,terms}.md` move to `docs/legal/` as
  `impressum.de.md`, `datenschutz.de.md`, `terms.de.md`, with `*.en.md` translations next to them.
- Each English file opens with "Translation. The German text is binding."
- They are still drafts to check before the sync service goes public (the `docs/plans/README.md`
  "Drafts" row moves along).

## Investigations and testing

- Names start with the date: `docs/investigations/2026-11-06-audit-findings.md` (from
  `findings/findings.md`), `2026-11-06-graphify-findings.md`, `2026-11-06-core-dump-summary.md`,
  `2026-11-01-todo-sweep.md`.
- They are archived: "Kept current: archived, not updated". Code is not fixed by editing them.
- `docs/windows-testing.md` moves to `docs/testing/windows.md`.

## Architecture doc

`docs/architecture.md` explains how the parts fit together, for a reader who doesn't know the
code yet:

- The parts: the desktop client (log watcher → mapping → map DB → UI and overlay), the static data
  (SDE build, Wanderer/anoik statics), the external services (zKillboard, EVE-Scout), the sync
  server and its protocol, the packaging targets.
- One diagram of the data flow (Mermaid, so GitHub renders it and it stays text).
- For each part: what it owns, what it must never do (for example, the client never needs ESI),
  and a link to its `code-map.md` section for the files.

## Doc check

A small `tools/check_docs.py`, run in `test.yml` and by the push gate:

- Every relative Markdown link and every `docs/….md` path cited in a code comment points to a file
  that exists. (Today this finds `docs/plans/plan-watched-systems.md` in `homebound/settings.py`.)
- Every doc outside `design-system/`, `dist/`, `build/` and `docs/private/` has the header block.
- It does not check that a doc is accurate; the "Kept current" line and the same-commit rule cover
  that.

## Steps

Each step is its own commit, so moves stay reviewable and `git log --follow` keeps the history.

1. Add `tools/check_docs.py` in report-only mode; fix the dead link it finds.
2. `git mv` the loose docs into `docs/legal/`, `docs/investigations/`, `docs/testing/`; fix the
   links in and to them.
3. Split `HOW_TO.md` into `docs/user/`; trim the README to the front door; fix links.
4. Clean `TODO.md`; create `docs/investigations/2026-11-01-todo-sweep.md`.
5. Write `docs/README.md` (doc map and guidelines) and the root `CLAUDE.md`.
6. Write the four folder `CLAUDE.md` files.
7. Write `docs/architecture.md`.
8. Write `CHANGELOG.md` back to 1.0.0; switch `release.yml` to `--notes-file`.
9. Add the header block to every doc that lacks one.
10. Write the English legal translations (the owner checks them).
11. Make the doc check blocking in CI and the push gate.

## Not in this plan

- `design-system/`: it has both `components/X/README.md` and `summaries/X.md` for each component,
  which may break "one home per fact". The owner left the folder out of this rework; only its
  `CLAUDE.md` is added.
- GitHub Issues. They would give outside players a place to report bugs (with templates), commits
  that close them (`Fixes #12`), labels and filters. The cost: tasks leave git, and agents read them
  through `gh`. Worth revisiting when the repo goes public, for public bug reports only.

## Later

- A user-guide website under the luthopian domain, built from `docs/user/` (MkDocs Material is a
  good fit: plain Markdown, relative links, search). The relative-link rule and the one page per
  topic layout prepare for it.

## Open questions

None left; the four from the first draft were answered by the owner on 2026-11-14 and are written
into the sections above (install one-liner, tooling section, changelog as release notes, the
bookmarking sketch).

Effort: 3/5 · Sonnet. Mostly moves and writing that this plan pins down; the architecture doc and
the legal translations need the owner's review.
