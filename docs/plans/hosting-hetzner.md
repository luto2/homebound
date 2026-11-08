# Homebound Sync Server: Hetzner hosting spec

> **STATUS: WIP. This is a spec only. Do not set it up yet.**
> It belongs with [`cloud-sync-plan.md`](cloud-sync-plan.md). Open items are in §14.

## 1. What runs where

```
                  homebound.luthopian.de (A + AAAA)
                               │ 443 (HTTPS + WSS)
┌──────────────── Hetzner Cloud VPS ────────────────┐
│ Caddy ── TLS (Let's Encrypt), reverse proxy       │
│   ├─ /ws        → homebound-sync (uvicorn :8080)  │
│   ├─ /login, /sso/callback, /api → same           │
│   └─ /          → static web view (read-only)     │
│ homebound-sync (systemd)                          │
│   ├─ WebSocket relay + merge                      │
│   ├─ wallet poller (ESI, hourly)                  │
│   └─ auto-prune + lapse timers                    │
│ SQLite (WAL)  /var/lib/homebound/sync.db          │
│ Litestream (systemd) ── continuous replication ───┼──▶ off-site S3 bucket (other provider)
└───────────────────────────────────────────────────┘
```

Everything runs on one machine, with one app process and one database file. There is no Docker, Redis or Postgres.

## 2. What to order and set up

| item | choice | approx. cost / month | notes |
|------|--------|----------------------|-------|
| VPS | **x86**, smallest shared-vCPU plan (2 vCPU, 4 GB RAM, 40 GB disk) | ~€4–5 | Plenty: the app needs about 100 MB of RAM. |
| Location | Falkenstein or Nuremberg (DE) | — | Inside the EU, close to you. Latency to players is irrelevant for a relay. |
| Primary IPv4 | yes | ~€0.50 | Many players still have no IPv6. |
| Hetzner Backups | optional, +20% of the VPS price | ~€1 | Whole-machine snapshots, kept for 7 days. Handy for "I broke the OS" moments, but they are **not** off-site. |
| Cloud Firewall | yes | free | Applies in front of the VPS as well as `ufw` on it. |
| Off-site DB backup | **Backblaze B2, EU Central region (Amsterdam)** | cents | A different provider from Hetzner, used as the Litestream target. The EU region keeps the data inside the EU (see the privacy policy). |
| Domain | `homebound.luthopian.de` (subdomain of a domain you already have) | €0 | A dedicated `homebound.*` domain would be about €10 a year. |
| Uptime monitor | healthchecks.io (free) plus an external HTTP check | free | See §9. |

**Total: about €5–7 a month.**

## 3. Accounts and registrations needed

1. **Hetzner Cloud** account and project `homebound`.
2. **SSH key** (ed25519) uploaded to Hetzner, used when the server is created. Password login is never enabled.
3. **EVE third-party application** at developers.eveonline.com:
   - Name: Homebound.
   - Callback URL: `https://homebound.luthopian.de/sso/callback`. This is the only callback EVE needs. The desktop app's random loopback port sits behind it (cloud-sync-plan §6).
   - Scopes registered: `esi-wallet.read_character_wallet.v1`, `esi-mail.read_mail.v1` and `esi-mail.send_mail.v1`. Login itself requests **no scopes**. Only the one-time admin link of the receiving character requests the wallet and mail scopes. It reads payments and map-limit requests, and it sends each payer a receipt, or a mail saying why the ISK couldn't be assigned.
   - The client ID and secret go in the server's secrets file (§6). They are never committed to git.
4. **Receiving character:** **Homebound Billing**, character ID `2124836346` (created 2026-11-08). The admin links it once: `homebound-server admin receiver-link` prints a one-time link to `/admin/link-wallet` (valid 30 minutes). Open it, sign in as the receiving character, and the server stores its refresh token in the database. `admin receiver` shows the result. `HB_RECEIVER_REFRESH_TOKEN` is only a fallback.
5. **Backblaze B2** account (region **EU Central**) with a bucket called `homebound-backup`. It needs an application key limited to that bucket, with no delete permission if possible. Lifecycle setting: keep 30 days of versions.
6. **DNS** at the luthopian.de registrar: an `A` and an `AAAA` record for `homebound` pointing at the VPS, with a low TTL until the setup is stable.
7. **healthchecks.io** check called `homebound-wallet-poller`.
8. **Discord webhook** in your personal server, for admin notifications (cloud-sync-plan §17).
9. **A second EVE application** for development, with the callback `http://localhost:8080/sso/callback` (cloud-sync-plan §18).

## 4. Base OS setup

- **Image:** Debian 13 (or the current Debian stable).
- **Firewall:**
  - Hetzner Cloud Firewall allows inbound TCP 22, 80 and 443, plus ICMP. Everything else is denied.
  - Also run `ufw` on the box with the same rules, as defence in depth.
- **SSH:**
  - `PasswordAuthentication no` and `PermitRootLogin no`.
  - A personal sudo user logs in. Optionally, SSH can be limited to your home IP in the cloud firewall.
- **Updates:** `unattended-upgrades` for security updates, with an automatic reboot window at 04:00 server time, when EVE downtime is close anyway.
- **Time:** `systemd-timesyncd` enabled. Correct time matters for HLC timestamps and SSO JWT checks.
- **Packages:** `caddy` (official repo), `sqlite3`, `git`, `uv`, `litestream` (release .deb), `age` (for one-off encrypted exports).
- **fail2ban:** optional. With key-only SSH it mostly reduces log noise.
- **Swap:** a 1 GB swapfile as a safety margin.

## 5. Service layout

| path | purpose | owner |
|------|---------|-------|
| `/opt/homebound/` | the app checkout (git), with its `uv` venv in `.venv` | `homebound` (system user, no login shell) |
| `/var/lib/homebound/sync.db` | the SQLite database (WAL) | `homebound`, mode 0600 |
| `/etc/homebound/env` | secrets and config (§6) | `root:homebound`, mode 0640 |
| `/etc/caddy/Caddyfile` | proxy config | root |
| `/etc/litestream.yml` | replication config | `root:homebound`, mode 0640 |

**systemd units:**
- `homebound-sync.service`:
  - runs `uv run homebound-server serve --host 127.0.0.1 --port 8080`, with `HB_DB`, `HB_BASE_URL=https://homebound.luthopian.de`, `HB_SSO_CLIENT_ID`, `HB_SSO_CLIENT_SECRET` and `HB_RECEIVER_CHAR_ID` from the secrets file
  - the port is only local. The public side is 443, shared with any other site on the VPS: the reverse proxy routes by hostname. If 8080 is taken, any free local port works, as long as the proxy points there.
  - `User=homebound` and `Restart=on-failure`
  - hardening: `ProtectSystem=strict`, `ReadWritePaths=/var/lib/homebound`, `PrivateTmp`, `NoNewPrivileges`, `MemoryMax=512M`
- `litestream.service`: replicates `sync.db` to B2.
- The wallet poller and prune timers run **inside** the app process, as asyncio tasks. That keeps it to one process.

**Caddyfile, roughly:**
```
homebound.luthopian.de {
    encode zstd gzip
    handle /ws*        { reverse_proxy 127.0.0.1:8080 }
    handle /api/*      { reverse_proxy 127.0.0.1:8080 }
    handle /login*     { reverse_proxy 127.0.0.1:8080 }
    handle /sso/*      { reverse_proxy 127.0.0.1:8080 }
    handle /admin/*    { reverse_proxy 127.0.0.1:8080 }   # also checks the admin role in the app
    handle             { root * /opt/homebound/web; file_server }
    header Strict-Transport-Security "max-age=31536000"
}
```

## 6. Configuration and secrets (`/etc/homebound/env`)

```
HB_BASE_URL=https://homebound.luthopian.de   # must match the EVE app's callback host
HB_DB=/var/lib/homebound/sync.db
HB_SSO_CLIENT_ID=…
HB_SSO_CLIENT_SECRET=…
HB_TOKEN_ENC_KEY=…          # encrypts the receiver's refresh token in the DB; make one with `admin new-key`
HB_RECEIVER_CHAR_ID=2124836346   # Homebound Billing; the receiver link refuses any other character
HB_DISCORD_WEBHOOK=https://discord.com/api/webhooks/…   # admin notifications (cloud-sync-plan §17)
HB_MUTE_BILLING=0           # 1: no posts for billing state changes
HB_DISK_WARN_PERCENT=80
HB_HEALTHCHECK_URL=https://hc-ping.com/…   # pinged after each successful wallet poll
LITESTREAM_ACCESS_KEY_ID=…
LITESTREAM_SECRET_ACCESS_KEY=…
```

- Secrets live only here and in your password manager.
- The wallet refresh token is stored encrypted with `HB_TOKEN_ENC_KEY` (Fernet), so a leaked DB backup can't read the wallet. Outside dev mode the receiver link refuses to run without the key. Losing the key only means linking the receiver again.
- Session tokens are random and only their hashes are stored, so no signing key is needed. `/admin/link-wallet` needs a one-time key from `homebound-server admin receiver-link`, so only someone with a shell on the server can start it; there is no `HB_ADMIN_CHAR_IDS`.
- **Rotation:** if the EVE secret leaks, regenerate it in the EVE developer portal. If `HB_TOKEN_ENC_KEY` leaks, set a new one and run the receiver link again. To log everyone out, delete the rows of the `sessions` table; people log in again with SSO.

## 7. Backups and restore

**Litestream** (`/etc/litestream.yml`):
- Replica: `s3://homebound-backup/sync.db` on the B2 endpoint.
- Snapshot interval 24 h, retention 30 days, sync interval about 10 s.
- At most roughly 10 s of edits can be lost. Clients still have their own local copies anyway.

**Data that is not backed up:**
- live presence, which is in memory only
- the op tail beyond what the DB holds

**Restore:**
1. Create a fresh VPS (§4 and §5).
2. Run `litestream restore -o /var/lib/homebound/sync.db s3://…`.
3. Start the services and repoint DNS.
4. Clients reconnect. Their `last_seq` is either still inside the tail or they get a snapshot.

**Checks:**
- Do a **restore test once a month**: restore to a temp path and run `sqlite3 … "PRAGMA integrity_check; SELECT count(*) FROM maps;"`. This can be a systemd timer that pings healthchecks.io.
- Hetzner Backups (if enabled) are only for quick OS-level rollback. They are not the DB backup.

## 8. Deploying updates

- The server code lives in the same repo, as `homebound_server/` plus the shared `homebound/sync/protocol.py`, so that client and server use the same merge code.
- Deploy is a small script, `deploy.sh`, run over SSH:
  1. `git -C /opt/homebound pull --ff-only`
  2. `uv sync --frozen`
  3. run DB migrations (numbered SQL files, applied at startup and recorded in a `schema_version` table)
  4. `systemctl restart homebound-sync`
- Restarts drop the WebSockets for a few seconds. Clients reconnect with backoff and resend their outbox, so no data is lost.
- **Protocol versioning:**
  - `hello` carries `protocol_version`.
  - The server accepts the current version and the previous one.
  - Older clients get an `error{code: "upgrade_required"}`, which the app shows as "please update".

## 9. Monitoring

- **External HTTP check** on `GET /api/health`, which returns DB OK, the poller's last run and the version. Use healthchecks.io or a home Uptime Kuma.
- **Wallet poller** pings healthchecks.io after each successful run. If no ping arrives within 2 h, you get an email. Silent payment failures would lock out paying users, so this check matters most.
- **Logs:** journald, kept for 14 days. The app logs to stdout. Nothing sensitive is logged: no tokens, and only character IDs.
- **Disk:** an alert at 80%. That should never happen at this data size.

## 10. Security checklist

- [ ] SSH key-only login, no root login, cloud firewall in place
- [ ] Caddy HTTPS with HSTS, and the app bound to `127.0.0.1` only
- [ ] Rate limits in the app, per IP and per account: for logins, WebSocket connects and ops per second
- [ ] SSO JWT checked against the EVE SSO JWKS (issuer, audience, expiry)
- [ ] Session tokens signed, with expiry and a revocation list (logout, member removed)
- [ ] Every `push` checks role and map state (viewer, lapsed, read-only)
- [ ] `HB_TOKEN_ENC_KEY` set, and `admin receiver` shows the receiver linked
- [ ] Backups encrypted (B2 server-side encryption plus a bucket-scoped key), and the restore tested
- [ ] `unattended-upgrades` active

## 11. Legal and policy (to check before going public)

- **Fenris Creations (formerly CCP) rules:** the third-party developer license allows tools, but forbids charging **real money**. ISK-only payments are what other EVE tools do. Re-read the current EVE Developer License Agreement and EULA before launch.
- **German Impressum (DDG §5) and privacy policy:** drafts are in [`legal/`](legal/) and will be served at `/impressum`, `/datenschutz` and `/terms`. The contact placeholders still need filling in. The service stores character IDs and names, and corp IDs, which are personal-ish data under GDPR.
  - Data minimisation already helps: no emails, no IPs stored beyond the log retention, and presence never stored.
  - Hetzner offers a data processing agreement (AVV/DPA) in the console. Accept it.
- A **Terms page:** ISK is non-refundable except at the admin's discretion, the service is provided as-is, and lapsed maps are deleted after 2 months.

## 12. Cost summary

| item | €/month |
|------|---------|
| VPS (smallest, with IPv4) | ~4.5–5.5 |
| Hetzner Backups (optional) | ~1 |
| Backblaze B2 | <0.10 |
| Domain (subdomain) | 0 |
| **Total** | **~5–7** |

## 13. Decided

- **VPS:** x86, the current smallest shared-vCPU plan.
- **Domain:** subdomain `homebound.luthopian.de`.
- **Off-site backup:** Backblaze B2, EU Central region.
- **Receiving character:** Homebound Billing (ID `2124836346`).
- **Legal pages:** drafted now in [`legal/`](legal/) (WIP).

## 14. Open items

- Fill in the Impressum contact details (name, postal address, email).
- Have the legal drafts checked before going public (e.g. with a generator such as e-recht24 or by a lawyer). They are not legal advice.
