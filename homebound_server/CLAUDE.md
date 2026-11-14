# Server: agent rules

The sync server: a FastAPI/WebSocket app (`app.py`, `hub.py`, `maps.py`, `service.py`) with accounts and billing through EVE SSO and ISK payments (`auth.py`, `esi.py`, `billing.py`, `corp.py`), background `jobs.py`, SQLite in `db.py`, and an admin CLI (`__main__.py`). It shares the merge code with the client in `homebound/sync/`, so client and server agree on every operation.

## Rules

- **A protocol change deploys the server first, then the client is released.** The server accepts the current and the previous protocol version; an older client gets `upgrade_required`. The version lives in `homebound/sync/protocol.py` (`PROTOCOL`, `MIN_PROTOCOL`); the wire format is in [`docs/plans/done/sync-protocol.md`](../docs/plans/done/sync-protocol.md). Update both and the protocol number in [`docs/architecture.md`](../docs/architecture.md).
- **Publishing a GitHub release is the deploy.** `hb-deploy` (`packaging/server/`) installs only the latest published, non-draft release, never an older one, and rolls back if `/api/health` does not answer. Pushes to `master` and draft releases do not deploy. Details: [`docs/plans/done/hosting-hetzner.md`](../docs/plans/done/hosting-hetzner.md), section 8.
- **Schema changes only add tables** (`CREATE TABLE IF NOT EXISTS`). Numbered migrations are not built yet; build them before the first change to an existing table.
- **Nothing sensitive in logs or docs**: no tokens, only character IDs. Secrets and server notes go in `docs/private/` (gitignored), never in a tracked file.
- **The server has no dev tools** and its tests run in the client's suite (`tests/server_helpers.py`). mypy covers this folder too.

## Admin CLI and billing

`homebound-server admin …` is the operator CLI (on the server it is wrapped as `hb-admin`). It links the receiving character (`receiver-link`, a one-time link valid 30 minutes), shows it (`receiver`), makes encryption keys (`new-key`), and handles accounts: `lookup`, `credit`, `credit-corp`, `comp`, `suspend`, `map-limit`, `free-maps`, `maps`, `delete-map`, `unmatched` and `refunded`. Billing reads the receiving character's wallet and sends receipts by EVE mail. The setup, environment variables and checklist are in the hosting plan; the server's operating notes are in `docs/private/operations.md` on the owner's machine.
