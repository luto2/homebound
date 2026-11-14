# Documentation

- **Audience:** the owner, contributors and coding agents.
- **Scope:** a map of every doc in the repo, and the rules for writing and updating them. The rules have no other home; the root [`CLAUDE.md`](../CLAUDE.md) imports this file.
- **Kept current:** checked against `7702f42` (2026-11-14). Update it when a doc folder is added, moved or removed, or when a writing rule changes.

## Map of the docs

| I want to… | Read |
|---|---|
| Know what Homebound is and install it | [`README.md`](../README.md) |
| Learn to use the app | [`docs/user/`](user/README.md): one page per topic, plus a [glossary](user/glossary.md) |
| See what changed in a release | [`CHANGELOG.md`](../CHANGELOG.md) |
| Understand how the parts fit together | [`docs/architecture.md`](architecture.md) |
| Find the file behind a feature | [`docs/code-map.md`](code-map.md) |
| See what is open | [`TODO.md`](../TODO.md) and the open plans in [`docs/plans/`](plans/README.md) |
| See why something was built a certain way | the finished plans in [`docs/plans/done/`](plans/README.md) |
| Look at the sync protocol | [`docs/plans/done/sync-protocol.md`](plans/done/sync-protocol.md) |
| Read the legal texts (German is binding) | [`docs/legal/`](legal/) |
| Test a build by hand | [`docs/testing/`](testing/windows.md) |
| Read an old audit or crash analysis | [`docs/investigations/`](investigations/) (dated, archived) |
| Look up how the UI should look | [`design-system/`](../design-system/README.md) |
| Build or ship a package | [`packaging/README.md`](../packaging/README.md) |

`docs/images/` holds the screenshots and sketches the docs use. `docs/private/` is gitignored: operations notes with secrets-adjacent detail live there and are never linked from tracked docs.

## Writing docs

### Header block

Every doc opens with its title, then this block:

```markdown
- **Audience:** who reads this.
- **Scope:** what it covers, and where the neighbouring topics are.
- **Kept current:** checked against `<hash>` (YYYY-MM-DD). <What change makes this doc need an update.>
```

User pages use the same block; "Audience" there is "players using Homebound". Plans keep their `Status:` line above the block. Archived files (`docs/investigations/`) say "archived, not updated" under "Kept current". `tools/check_docs.py` checks that the block is there.

### Rules

1. **One home per fact.** Each fact is written in one doc; the others link to it. The install steps live in `docs/user/install.md`, and the README has only the one-line install command and a link. The feature list lives in the README; the user guide links to it.
2. **Absolute dates and hashes.** Write "2026-11-10 (`1665163`)", never "yesterday", "recently" or "the last release".
3. **Files and symbols, not line numbers.** Point to `homebound/settings.py` and `Settings.load`. Line numbers go stale with every edit.
4. **Plain sentences, terms explained.** Short, concrete sentences, no filler or marketing tone. EVE terms (static, K162, sig, mass state) and tool terms (SDE, KWin script, offscreen Qt) are explained the first time a doc uses them, or link to the [glossary](user/glossary.md).
5. **Relative links.** Link between docs with relative paths, so links work on GitHub, in an editor and on a later website.
6. **Same commit.** A commit that changes behaviour, a module, the UI, a command or the protocol updates the matching doc (table below) in the same commit and moves its "Kept current" line.
7. **English.** Legal texts are German (binding) with an English translation next to them.

### Which doc goes with which change

| The commit changes… | Update |
|---|---|
| Something a user sees or does | the page in `docs/user/`, and `CHANGELOG.md` under "Unreleased" |
| Where a feature lives (new or moved module, renamed symbol) | `docs/code-map.md` |
| How the parts talk to each other (client, server, sync, SDE, overlay) | `docs/architecture.md` |
| The sync protocol | `docs/plans/done/sync-protocol.md` and the protocol number in `docs/architecture.md` |
| Build, packaging or install | the `packaging/` READMEs, `docs/user/install.md` |
| Server deploy or operations | `homebound_server/CLAUDE.md` and the hosting plan; secrets never, notes in `docs/private/` |
| Finishing a plan | the steps in [`docs/plans/README.md`](plans/README.md) (status line, `git mv` to `done/`, fix links, move the row) and the item in `TODO.md` |

### Checking

```sh
python3 tools/check_docs.py          # report
python3 tools/check_docs.py --strict # exit 1 on any finding (CI and the push gate)
```

It checks that relative links and cited `docs/….md` paths exist and that every doc has the header block. It does not check that a doc is accurate; the "Kept current" line and the same-commit rule cover that.
