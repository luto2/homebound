# Plan: match the system sender exactly

Status: not started. TODO.md "To do" item 12, finding E14 in `docs/findings/findings.md`.

Depends on: nothing.

## Problem (verified)

`parse_system_change` (`homebound/logs/parser.py:124`) accepts a line when the sender is in
`SYSTEM_SENDERS` **or** starts with `EVE`. A player named "EVE Fan" who types `anyone in local : Amarr`
in Local writes the line `[ ts ] EVE Fan > anyone in local : Amarr`. The message has no
`Channel changed to Local`, but `"local" in message.lower()` holds and `ANY_CHANGE_PATTERN` takes `Amarr`,
so the parser returns a jump to Amarr (`parser.py:127`). Not re-run for this plan; E14 is marked `ran`
and the code path above matches it.

The findings entry also says most of `SYSTEM_SENDERS` is redundant. Checked: four of the six names start
with `EVE`, so the prefix test already covers them. Only `Système EVE` and `Система EVE`
rely on the set.

## Root cause

`parser.py:124`: `and not sender.startswith("EVE")` was added as a catch-all for client languages whose
system sender name nobody had listed. The second branch at `parser.py:129` (`"local" in message` plus a
trailing `: <name>`) then trusts any text, so the sender check is the only guard. The comment at
`parser.py:21` says other languages are matched by the trailing `Local : <system>`.

## What the system sender is called per language

Only English is confirmed.

| Language | Name in `SYSTEM_SENDERS` | Status |
|---|---|---|
| English | `EVE System` | Confirmed. Forum logs ([Local chat fix?](https://forums.eveonline.com/t/local-chat-fix/80331), [Local chat status updates](https://forums.eveonline.com/t/local-chat-status-updates/75662)), the sample output of [thebravyone/eve-chatlog](https://github.com/thebravyone/eve-chatlog), and every fixture in this repo (`tests/test_logs.py:27`, `tests/test_coverage_gaps.py:32`, `tests/test_ui_components.py:35`, `docs/windows-testing.md:76`). |
| German | `EVE-System` | Not confirmed. Not found online. |
| French | `Système EVE` | Not confirmed. |
| Russian | `Система EVE` | Not confirmed. |
| Japanese | `EVE システム` | Not confirmed. |
| Chinese | `EVE 系统` | Not confirmed. |
| Korean, Spanish | none | Not listed. Not found. |

The repo has no non-English sample log; the six names arrived in the initial commit (`e2fe7a3`) with no
source. A web search found no localized log. [kirilan/eve-local-watch](https://github.com/kirilan/eve-local-watch)
says it only works with the English client because it matches the English `Channel changed to Local`
text, which also means the localized message text is unverified. Ask a player on each client for a
real `Local_*.txt` line before relying on any non-English name.

## Risk of exact matching

Exact matching with a wrong or missing name stops tracking for that player with no error: no jumps, no
character on the map. Today the `EVE` prefix covers `EVE-System`, `EVE システム` and `EVE 系统` even if the
exact spelling differs (a space, a different dash). A prefix-free rule loses that. The false positive
is a player who is named `EVE ...` and types a matching line, which is rare. The false negative is
every player on a client whose name isn't in the set. The second costs more.

## Proposed change

`homebound/logs/parser.py`
1. Line 124: `if sender not in SYSTEM_SENDERS: return None`. Drop the prefix test.
2. Compare normalized text: `unicodedata.normalize("NFKC", sender)` and collapse whitespace, so
   `EVE  システム` or a no-break space still matches. Build the normalized set once at import.
3. Keep the tail match at `parser.py:129`, but see decision 2.
4. Update the comment at `parser.py:21` (the sender is now an exact match).

No other file changes. `SYSTEM_SENDERS` is only used in `parser.py`.

## Tests

In `tests/test_logs.py`, extending `test_parse_system_change` or next to it. Write the first one before
the fix and watch it fail:

1. **(fails now)** `line(ts, "anyone in local : Amarr", sender="EVE Fan")` returns None.
2. **(fails now)** `line(ts, "Channel changed to Local : Amarr", sender="EVE Fan")` returns None.
3. Each name in `SYSTEM_SENDERS` still gives a change for `Channel changed to Local : Jita`.
4. `EVE  System` (two spaces) and a no-break space still match.
5. Existing cases (`Some Pilot` is None, trailing `*` dropped) stay green.

Run `pytest tests/test_logs.py tests/test_coverage_gaps.py`, then the full suite.

## Risks

- A client language with an unlisted name stops tracking silently (see above). Mitigated only by
  decision 1.
- The normalization changes nothing for English, so the common case can't regress.

## Localization: later

Owner decision (2026-11-06): localization is deferred. Collecting a real log per client language and
confirming the names waits until then, tracked on the todo board. One way to collect them: switch the
client language, jump once and read `Local_*.txt`, though nobody has checked that the setting changes that
line. [Phobos](https://github.com/DarkFenX/Phobos) dumps client data if the names turn out to be stored there.

## Open decisions

1. **How to cover languages we couldn't confirm.** (a) Exact set only: simple, but silent loss for
   an unlisted client. (b) Exact set plus a log warning when an `EVE`-prefixed sender produces a
   `Channel changed to Local : <name>` line: nothing is tracked from it, but a bug report has a
   trail. (c) Keep a fallback: accept an unlisted `EVE`-prefixed sender only for the strict
   `CHANGE_PATTERN` text, never for the loose `local ... :` match. This fixes the "EVE Fan" report
   but a player can still type the exact system text. Recommended: (b), then collect real logs.
2. **Keep the loose message match** (`parser.py:129`, any language) once the sender is exact? It exists for
   non-English clients, and with an exact sender it is safe. Recommended: keep.
3. **Who supplies real localized logs?** Before release, one `Local_*.txt` per client language, with
   the name added to `SYSTEM_SENDERS` and a fixture under `tests/`.

## Owner decisions (2026-11-12)

- An `EVE`-prefixed sender that isn't in `SYSTEM_SENDERS` is ignored, and the user gets a warning that
  only the English client is fully supported for now (also written to the log).
- Keep the loose message match.
- Collecting real logs for other languages is for later, once Homebound has enough users for it to pay off.
