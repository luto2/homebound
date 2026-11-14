# Plan: Windows code signing

Status: plan only, nothing implemented yet (2026-11-01).

- **Audience:** the owner and the coding agents that build this plan.
- **Scope:** the plan "Plan: Windows code signing", not implemented yet. The state of every plan is in [`README.md`](README.md).
- **Kept current:** written against `d70e407` (2026-11-12). Update it when the plan changes or the code it describes moves.

Depends on: a public repo with an OSI licence (done: GPL-3.0-or-later) and the CI release build from [packaging](done/plan-packaging.md).

Build order across the plans: [rescan status](done/plan-rescan-status.md) → [backups](done/plan-backups.md) → [Windows support](done/plan-windows-support.md) → [packaging](done/plan-packaging.md) → [code signing](plan-code-signing.md).

**Unsigned, from the user's side**: SmartScreen shows "Windows protected your PC". The user clicks
*More info* → *Run anyway*. That needs **no admin rights**; the per-user installer doesn't either.
Edge may also flag the download as "not commonly downloaded" (→ *Keep*). Only PCs where an admin
enforces SmartScreen by policy block it entirely.

**Signing doesn't remove the warning at once**: SmartScreen trusts by reputation, which builds up
as people download and run files signed with the same certificate. Since 2024, EV certificates no
longer skip that. What signing gives right away is a named publisher instead of "Unknown".

Options, cheapest first (prices and eligibility to check when it's time):
1. **SignPath Foundation**: free signing for open-source projects. It needs an OSI licence, a
   public repo, and builds from CI. It plugs into GitHub Actions. Best fit.
2. **Certum Open Source Code Signing**: low-cost certificate for open-source developers, available
   to individuals in the EU. It needs an ID check and a hardware key or their cloud signing.
3. **Microsoft Trusted Signing** (Azure): monthly subscription, cloud signing from GitHub Actions.
   Check whether it's open to individuals in Germany.
4. A regular OV certificate from a commercial CA: a few hundred € a year, hardware key required.

Plan: ship the first releases unsigned, apply to SignPath Foundation once the repo is public with a
licence, and add the signing step to `release.yml`.

## Decisions (2026-11-01)

- First releases ship unsigned; apply to SignPath Foundation once the repo is public.

## Owner decisions (2026-11-12)

- Not planned: no certificate will be bought for now.
