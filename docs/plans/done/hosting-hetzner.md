# Homebound Sync Server: Hetzner hosting spec

> **STATUS: the server is built and merged into `master` (1.0.0). §15 has the setup steps, command by command. Backups (§7) are set up in `packaging/server/` (Litestream to Hetzner Object Storage, encrypted with age, with a daily restore test).**
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
| DB backup | **Hetzner Object Storage**, in a different location from the VPS | ~€5 | The Litestream target. Backblaze B2 was the plan, but new B2 accounts no longer offer the EU region (2026-11), and the privacy policy promises EU storage. Same provider as the VPS, so an account problem at Hetzner would hit both. |
| Domain | `homebound.luthopian.de` (subdomain of a domain you already have) | €0 | A dedicated `homebound.*` domain would be about €10 a year. |
| Uptime monitor | healthchecks.io (free) plus an external HTTP check | free | See §9. |

**Total: about €10–12 a month.**

## 3. Accounts and registrations needed

1. **Hetzner Cloud** account and project `homebound`.
2. **SSH key** (ed25519) uploaded to Hetzner, used when the server is created. Password login is never enabled.
3. **EVE third-party application** at developers.eveonline.com:
   - Name: Homebound.
   - Callback URL: `https://homebound.luthopian.de/sso/callback`. This is the only callback EVE needs. The desktop app's random loopback port sits behind it (cloud-sync-plan §6).
   - Scopes registered: `esi-wallet.read_character_wallet.v1`, `esi-mail.read_mail.v1` and `esi-mail.send_mail.v1`. Login itself requests **no scopes**. Only the one-time admin link of the receiving character requests the wallet and mail scopes. It reads payments and map-limit requests, and it sends each payer a receipt, or a mail saying why the ISK couldn't be assigned.
   - The client ID and secret go in the server's secrets file (§6). They are never committed to git.
4. **Receiving character:** **Homebound Billing**, character ID `2124836346` (created 2026-11-08). The admin links it once: `homebound-server admin receiver-link` prints a one-time link to `/admin/link-wallet` (valid 30 minutes). Open it, sign in as the receiving character, and the server stores its refresh token in the database. `admin receiver` shows the result. `HB_RECEIVER_REFRESH_TOKEN` is only a fallback.
5. **Hetzner Object Storage** bucket `homebound-backup` in a location other than the VPS's, with its own S3 credentials (§15 step 10).
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
  - runs `/opt/homebound/.venv/bin/homebound-server serve --host 127.0.0.1 --port 8080` (the venv directly, so uv isn't needed at runtime), with `HB_DB`, `HB_BASE_URL=https://homebound.luthopian.de`, `HB_SSO_CLIENT_ID`, `HB_SSO_CLIENT_SECRET` and `HB_RECEIVER_CHAR_ID` from the secrets file
  - the port is only local. The public side is 443, shared with any other site on the VPS: the reverse proxy routes by hostname. If 8080 is taken, any free local port works, as long as the proxy points there.
  - `User=homebound` and `Restart=on-failure`
  - hardening: `ProtectSystem=strict`, `ReadWritePaths=/var/lib/homebound`, `PrivateTmp`, `NoNewPrivileges`, `MemoryMax=512M`
- `litestream.service`: replicates `sync.db` to Hetzner Object Storage.
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
HB_FREE_MAPS=alliance:99000001=1,corp:98000001=1   # free maps per owner (alliance|corp|char); alliance: members + executor corp
HB_HEALTHCHECK_URL=https://hc-ping.com/…   # pinged after each successful wallet poll
LITESTREAM_ACCESS_KEY_ID=…       # object storage access key
LITESTREAM_SECRET_ACCESS_KEY=…   # object storage secret key
HB_BACKUP_ENDPOINT=hel1.your-objectstorage.com   # the bucket's location endpoint, without https://
HB_BACKUP_REGION=hel1            # the location code: fsn1, nbg1 or hel1
HB_BACKUP_AGE_RECIPIENT=age1…    # public key: backups are encrypted to it before upload
HB_BACKUP_AGE_IDENTITY=AGE-SECRET-KEY-1…   # private key: needed to restore. Also in the password manager!
HB_RESTORE_HEALTHCHECK_URL=https://hc-ping.com/…   # the daily restore test reports here
```

- Secrets live only here and in your password manager.
- The wallet refresh token is stored encrypted with `HB_TOKEN_ENC_KEY` (Fernet), so a leaked DB backup can't read the wallet. Outside dev mode the receiver link refuses to run without the key. Losing the key only means linking the receiver again.
- Session tokens are random and only their hashes are stored, so no signing key is needed. `/admin/link-wallet` needs a one-time key from `homebound-server admin receiver-link`, so only someone with a shell on the server can start it; there is no `HB_ADMIN_CHAR_IDS`.
- **Rotation:** if the EVE secret leaks, regenerate it in the EVE developer portal. If `HB_TOKEN_ENC_KEY` leaks, set a new one and run the receiver link again. To log everyone out, delete the rows of the `sessions` table; people log in again with SSO.

## 7. Backups and restore

**Litestream** copies every change of `sync.db` to Hetzner Object Storage (setup: §15 step 10). The files are in `packaging/server/`:
- `litestream.yml` (to `/etc/litestream.yml`): replica `s3://homebound-backup/sync.db.age` on the storage endpoint, **encrypted with age** before upload, sync every 10 s, a snapshot every 24 h, 30 days kept with every change in between (point-in-time restore).
- `litestream-override.conf`: Litestream runs as the `homebound` user with the credentials from `/etc/homebound/env`.
- At most about 10 s of edits can be lost. Clients still have their own local copies anyway.
- The bucket lives in a different Hetzner location from the VPS, so one data center outage can't take both.
- Alliance Auth on the same VPS is **not** part of this.

**Data that is not backed up:**
- live presence, which is in memory only
- the op tail beyond what the DB holds

**Restore (new VPS or broken database):**
1. Set up the server (§15 steps 2–5 and the Litestream install from step 10), but don't start `homebound-sync` yet.
2. `sudo -u homebound sh -c 'set -a; . /etc/homebound/env; litestream restore -config /etc/litestream.yml /var/lib/homebound/sync.db'`
   - an older point in time: add `-timestamp 2026-11-20T12:00:00Z`
3. Start `homebound-sync` and `litestream`; repoint DNS if the IP changed.
4. Clients reconnect. Their `last_seq` is either still inside the tail or they get a snapshot.

**Checks:**
- **A restore test every day** (`hb-restore-test`, run by `homebound-restore-test.timer`): restores the newest backup to a temp folder, runs `PRAGMA integrity_check`, counts the maps, and checks the backup is less than 2 h behind (it compares the last wallet poll). It pings `HB_RESTORE_HEALTHCHECK_URL`: healthchecks.io emails you when a test fails or none arrives. A daily test of a few MB costs nothing extra.
- Hetzner Backups (if enabled) are only for quick OS-level rollback. They are not the DB backup.

## 8. Deploying updates

- The server code lives in the same repo, as `homebound_server/` plus the shared `homebound/sync/protocol.py`, so that client and server use the same merge code.
- Deploy is a small script, `deploy.sh`, run over SSH:
  1. `git -C /opt/homebound pull --ff-only`
  2. `UV_PYTHON_PREFERENCE=only-system uv sync --extra server --no-dev --frozen`
  3. `systemctl restart homebound-sync`
- Schema changes: so far the server only adds tables (`CREATE TABLE IF NOT EXISTS` at startup). Numbered migrations recorded in a `schema_version` table are still to be built, before the first change to an existing table.
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
- [ ] Backups encrypted with age (`HB_BACKUP_AGE_*` set, the private key in the password manager), and the restore tested
- [ ] `unattended-upgrades` active

## 11. Legal and policy (to check before going public)

- **Fenris Creations (formerly CCP) rules:** the third-party developer license allows tools, but forbids charging **real money**. ISK-only payments are what other EVE tools do. Re-read the current EVE Developer License Agreement and EULA before launch.
- **German Impressum (DDG §5) and privacy policy:** drafts are in [`legal/`](../legal/) and will be served at `/impressum`, `/datenschutz` and `/terms`. The contact placeholders still need filling in. The service stores character IDs and names, and corp IDs, which are personal-ish data under GDPR.
  - Data minimisation already helps: no emails, no IPs stored beyond the log retention, and presence never stored.
  - Hetzner offers a data processing agreement (AVV/DPA) in the console. Accept it.
- A **Terms page:** ISK is non-refundable except at the admin's discretion, the service is provided as-is, and lapsed maps are deleted after 2 months.

## 12. Cost summary

| item | €/month |
|------|---------|
| VPS (smallest, with IPv4) | ~4.5–5.5 |
| Hetzner Backups (optional) | ~1 |
| Hetzner Object Storage | ~5 |
| Domain (subdomain) | 0 |
| **Total** | **~10–12** |

## 13. Decided

- **VPS:** x86, the current smallest shared-vCPU plan.
- **Domain:** subdomain `homebound.luthopian.de`.
- **Backup:** Hetzner Object Storage in another location (B2's EU region isn't offered to new accounts).
- **Receiving character:** Homebound Billing (ID `2124836346`).
- **Legal pages:** drafted now in [`legal/`](../legal/) (WIP).

## 14. Open items

- Fill in the Impressum contact details (name, email). No postal address: the operator's decision (2026-11-08), as payment is in-game ISK only.
- Have the legal drafts checked before going public (e.g. with a generator such as e-recht24 or by a lawyer). They are not legal advice.

## 15. First-time setup, step by step

Debian or Ubuntu, as root (or with sudo). The reverse proxy that already runs on the VPS (for the auth service) handles TLS; Homebound only listens on `127.0.0.1`. These steps haven't been run on the real VPS yet, so correct this section where it differs.

### 1. DNS
An `A` record (and `AAAA` with IPv6) for `homebound.luthopian.de` pointing to the VPS. No port and no new firewall rule: it uses the 443 that is already open.

### 2. User, packages, code
```bash
apt install git sqlite3 python3 python3-venv
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

useradd --system --home /var/lib/homebound --create-home --shell /usr/sbin/nologin homebound
git clone -b cloud-sync https://github.com/luto2/homebound.git /opt/homebound
cd /opt/homebound
UV_PYTHON_PREFERENCE=only-system uv sync --extra server --no-dev --frozen
```
- `only-system` makes the venv use Debian's Python (3.12 or newer is needed). Otherwise uv installs its own Python under `/root`, which the `homebound` user can't read.

### 3. Universe data
The server checks system IDs against EVE's static data. Build it once (it downloads the sources; no display needed):
```bash
cd /opt/homebound && .venv/bin/homebound --build-static
ls -l data/universe.sqlite
```
Run it again after EVE patches that add or change systems, then restart the service.

### 4. Secrets
Make the key that encrypts the receiving character's refresh token:
```bash
/opt/homebound/.venv/bin/homebound-server --dev admin new-key
```
- `--dev` only keeps this command from reaching EVE. The key it prints is a normal key.
- Keep a copy in your password manager. If it's lost, set a new one and link the receiver again; old database backups then can't be used for the wallet.

Then write `/etc/homebound/env` (all settings: §6):
```bash
mkdir -p /etc/homebound
cat > /etc/homebound/env <<'EOF'
HB_BASE_URL=https://homebound.luthopian.de
HB_DB=/var/lib/homebound/sync.db
HB_UNIVERSE=/opt/homebound/data/universe.sqlite
HB_SSO_CLIENT_ID=<from developers.eveonline.com>
HB_SSO_CLIENT_SECRET=<from developers.eveonline.com>
HB_TOKEN_ENC_KEY=<output of new-key>
HB_RECEIVER_CHAR_ID=2124836346
HB_DISCORD_WEBHOOK=<Discord: channel settings > Integrations > Webhooks>
HB_HEALTHCHECK_URL=<optional: healthchecks.io ping URL>
EOF
chown root:homebound /etc/homebound/env && chmod 640 /etc/homebound/env
```

### 5. Service
```bash
cat > /etc/systemd/system/homebound-sync.service <<'EOF'
[Unit]
Description=Homebound sync server
After=network-online.target
Wants=network-online.target

[Service]
User=homebound
EnvironmentFile=/etc/homebound/env
WorkingDirectory=/var/lib/homebound
ExecStart=/opt/homebound/.venv/bin/homebound-server serve --host 127.0.0.1 --port 8080
Restart=on-failure
RestartSec=5
ProtectSystem=strict
ReadWritePaths=/var/lib/homebound
PrivateTmp=true
NoNewPrivileges=true
MemoryMax=512M

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload && systemctl enable --now homebound-sync
journalctl -u homebound-sync -f        # "Uvicorn running on http://127.0.0.1:8080"
curl -s localhost:8080/api/health
```
- If 8080 is taken, change `--port` here and in the proxy.
- **One process only:** live sync keeps its state in memory. No `--workers`, no second instance.

### 6. Reverse proxy
Add the subdomain to the existing proxy. It needs the WebSocket upgrade headers (for `/ws`) and a long read timeout. nginx:
```nginx
server {
    server_name homebound.luthopian.de;
    listen 443 ssl;          # plus the certificate lines
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 1h;
    }
}
```
Caddy: `homebound.luthopian.de { reverse_proxy 127.0.0.1:8080 }`. Then check from outside: `curl -s https://homebound.luthopian.de/api/health`.

### 7. Admin helper and the receiver link
Admin commands need the service's environment and user:
```bash
cat > /usr/local/bin/hb-admin <<'EOF'
#!/bin/sh
exec sudo -u homebound sh -c 'set -a; . /etc/homebound/env; cd /var/lib/homebound; exec /opt/homebound/.venv/bin/homebound-server admin "$@"' hb-admin "$@"
EOF
chmod 755 /usr/local/bin/hb-admin
```
1. `hb-admin receiver-link`, then open the printed link within 30 minutes.
2. Sign in as Homebound Billing and approve the three scopes (wallet read, mail read, mail send).
3. `hb-admin receiver` should show `"has_token": true`.
4. Within a minute, Discord gets "Receiving character linked". That also proves the webhook works.

### 8. End-to-end test
1. Start the app (branch build; it points at `https://homebound.luthopian.de` by default) and sign in under **Cloud > Sign in**.
2. Give a small amount, such as 1M ISK, from that character to Homebound Billing.
3. `hb-admin run poll_wallet`, then `hb-admin run send_mails` (or wait up to an hour). A receipt mail arrives in-game, and `hb-admin lookup "<your character>"` shows the balance.
4. Give 1M from a character that isn't linked: a problem mail in-game and an "Unmatched donation" in Discord. Refund it, then `hb-admin refunded <journal id>`.

### 9. Updates
```bash
cd /opt/homebound && git pull --ff-only && UV_PYTHON_PREFERENCE=only-system uv sync --extra server --no-dev --frozen
systemctl restart homebound-sync
```

### 10. Backups (Litestream to Hetzner Object Storage)
**In the Hetzner Console:**
1. Your project > **Object Storage** > create the bucket `homebound-backup`, **private**, in a location other than the VPS's (e.g. VPS in Nuremberg, bucket in Helsinki or Falkenstein).
2. **Security > S3 credentials** > generate. Note the access key and the secret key (shown once) in the password manager.
3. Note the bucket's endpoint, e.g. `hel1.your-objectstorage.com`, and its location code (`hel1`).

**On the VPS:**
```bash
# Litestream 0.3.13 (the config is written for 0.3.x)
wget https://github.com/benbjohnson/litestream/releases/download/v0.3.13/litestream-v0.3.13-linux-amd64.deb
dpkg -i litestream-v0.3.13-linux-amd64.deb

# credentials and endpoint: add LITESTREAM_ACCESS_KEY_ID, LITESTREAM_SECRET_ACCESS_KEY,
# HB_BACKUP_ENDPOINT, HB_BACKUP_REGION and HB_RESTORE_HEALTHCHECK_URL to /etc/homebound/env (§6)
nano /etc/homebound/env

cd /opt/homebound/packaging/server
install -m 640 -o root -g homebound litestream.yml /etc/litestream.yml
install -D -m 644 litestream-override.conf /etc/systemd/system/litestream.service.d/override.conf
install -m 755 hb-restore-test /usr/local/bin/hb-restore-test
install -m 644 homebound-restore-test.service homebound-restore-test.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now litestream homebound-restore-test.timer
journalctl -u litestream -f      # "initialized db", then "snapshot written" and "wal segment written"
```

**Encryption (age):** Litestream encrypts every file before upload, so the storage provider only sees ciphertext.
```bash
apt install age
age-keygen       # prints "# public key: age1…" and the private key "AGE-SECRET-KEY-1…"
```
- Put both in `/etc/homebound/env` as `HB_BACKUP_AGE_RECIPIENT` (the `age1…` public key) and `HB_BACKUP_AGE_IDENTITY` (the `AGE-SECRET-KEY-1…` line). Save the private key in the password manager too: **without it no backup can be restored**, also not by you.
- Don't print the key to a file on the server; copy it from the terminal.
- **Litestream 0.3.x only.** Litestream 0.5 dropped age encryption, so stay on 0.3.13 until there's a plan for that.

**Check it:**
1. In the bucket's file list, `homebound-backup/sync.db.age/` has `generations/…` with a snapshot and WAL files after a minute.
2. `sudo -u homebound hb-restore-test` prints `restore ok: integrity ok, N maps, …`.
3. On healthchecks.io, the check `homebound-restore-test` (period 1 day, grace 2 h) turns green.
