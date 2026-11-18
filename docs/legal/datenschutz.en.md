> **DRAFT / WIP**: fill in the placeholders `[…]` and compare with the actual server setup before publishing. Not legal advice.
> It will later be served at `https://homebound.luthopian.de/datenschutz`.

# Privacy policy

- **Audience:** visitors of the sync service and the owner.
- **Scope:** the English translation of [`datenschutz.de.md`](datenschutz.de.md), the privacy policy of the sync service and the desktop app's network requests. The German text also ends with a short English summary.
- **Kept current:** draft, to be checked by a lawyer before the sync service goes public; checked against `9c545d7` (2026-11-17). Update it together with `datenschutz.de.md`.

> Translation. The German text is binding.

## 1. Controller

Tobias Luther, email: luther-tobias@gmx.de (see [Impressum](impressum.en.md)).
No data protection officer has been appointed, and none is required.

## 2. Principle

Homebound processes as little data as possible:
- There is no registration with an email address or password.
- There is no advertising, no tracking and no analytics tools.
- No third-party cookies are set.

The desktop app also works completely without the sync service. In that case no data leaves your computer apart from the requests to public services listed in section 3.

## 3. Connections the desktop app makes to other services

Even without the sync service the desktop app fetches public data from the internet. With every request the service technically receives the computer's IP address, the time and a program identifier (user agent, e.g. `homebound/1.0 (personal offline wormhole mapper)`). Further data is sent only where stated below. The requests go straight from your computer to each service. The provider of Homebound does not receive or store them, except the requests to the Homebound server; for those the legal basis is Art. 6(1)(f) GDPR (legitimate interest in giving the app its map data). The other services are responsible for their own processing, and their own privacy policies apply.

**On first start**, and when the map data is rebuilt (`--build-static`):

| Service | What for | What else is sent |
|---|---|---|
| Homebound server (homebound.luthopian.de, see section 1, hosted at Hetzner) | wormhole data (statics and wormhole types) | nothing; server logs as in section 4 (14 days) |
| Fenris Creations, Iceland (developers.eveonline.com) | EVE Online's static game data (SDE: systems, classes, gates) | nothing |

Only if the Homebound server gives no usable wormhole data does the app fetch it from the sources directly:

| Service | What for | What else is sent |
|---|---|---|
| GitHub, Inc., USA (raw.githubusercontent.com) | wormhole data of the Wanderer project | nothing |
| anoik.is (operator and location not stated) | statics of the wormhole systems | nothing |
| whtype.info (run privately, location not stated) | where wormhole types can lead | nothing |
| zKillboard (zkillboard.com, run privately, location not stated) | statics of single systems where the sources disagree | the IDs of those solar systems |

**While the app runs:**

| Service | What for | What else is sent | Turn off |
|---|---|---|---|
| zKillboard (zkillboard.com) | kill activity of the systems in the visible chains | the IDs of those solar systems, which show which systems you are looking at | Settings → zKill |
| EVE-Scout (api.eve-scout.com, operator and location not stated) | public Thera and Turnur connections for routes, every 5 minutes | nothing | Settings → Routes |
| Fenris Creations, Iceland (esi.evetech.net) | only when two character IDs in the chat logs share a name (a copy of a character on a test server) | those character IDs | not needed: no shared name, no request |

There is no sign-in with these services, and no map contents, character names or locations are sent. Some of these services are outside the EU and the EEA or do not state where they are: GitHub, Inc. is in the USA, and the location of anoik.is, whtype.info, zKillboard and EVE-Scout is not known. Data protection there may be weaker than in the EU. To avoid this, turn zKillboard and EVE-Scout off in Settings; the first-start requests do not happen while the Homebound server can be reached. Fenris Creations is in Iceland (EEA).

[For the lawyer: the draft assumes the provider of Homebound is not a controller for the requests to other services (he never receives the data, Recital 78 GDPR), so there is no transfer under Chapter V GDPR (EDPB Guidelines 05/2021, criterion 2). If joint control for triggering the requests is assumed (CJEU C-40/17 Fashion ID), Art. 26 and, for GitHub, Art. 45 GDPR with the EU-US Data Privacy Framework need checking (confirm GitHub, Inc.'s certification on dataprivacyframework.gov; appeal C-703/25 P pending).]

## 4. Which data is processed

| Data | Source | Purpose | Legal basis | Retention |
|---|---|---|---|---|
| EVE character ID and name | EVE SSO login (Fenris Creations) | Sign-in, assignment to maps and payments | Art. 6(1)(b) GDPR (contract) | as long as the account exists |
| Corporation ID of the character | public ESI interface of Fenris Creations | Assigning missed payments (see the terms of service) | Art. 6(1)(b) and (f) GDPR | as long as the account exists; warnings 12 months |
| Session token | generated by the server | Staying signed in | Art. 6(1)(b) GDPR | until sign-out or expiry |
| Map content (systems, connections, signatures, notes, settings) | entered by users | Synchronisation between members | Art. 6(1)(b) GDPR | connections are removed automatically after 2 days, systems after 4 days (except home and pinned systems); the whole map 2 months after the payment lapses |
| Live location of characters | desktop app, only if enabled per character (opt-in) | Display for map members | Art. 6(1)(a) GDPR (consent, can be switched off at any time) | **not stored**, only in memory while the connection lasts |
| Payment data (journal ID, paying character, ISK amount, date, reason) | wallet journal of the receiving character (ESI) | Billing of the subscription | Art. 6(1)(b) GDPR | as long as the account exists, then 12 months |
| Use of the free trial (character ID, account ID, time) | when the trial starts | Abuse protection: the trial exists once per character, even after deletion or resale | Art. 6(1)(f) GDPR | unlimited |
| IP address, time, request | technically with every request | Operation, abuse protection (rate limits), troubleshooting | Art. 6(1)(f) GDPR | rate limits only in memory; server logs 14 days |

No special categories of personal data are processed.

## 5. Cookies and local storage

The web view sets only one **technically necessary session cookie** for sign-in (§ 25(2) no. 2 TDDDG). No consent is needed for it. There are no other cookies. The desktop app stores its session token locally on your own computer.

## 6. Sign-in through EVE Online (Fenris Creations)

Sign-in uses EVE Single Sign-On by Fenris Creations (formerly CCP Games), Reykjavík, Iceland. At login you are redirected to Fenris Creations. Homebound requests **no permissions** (scopes) and receives only the character ID and character name. Processing at Fenris Creations is covered by their privacy policy: https://www.ccpgames.com/privacy-policy. Iceland is treated as equivalent for data protection purposes as a member of the EEA.

## 7. Recipients and processors

- **Hetzner Online GmbH**, Industriestr. 25, 91710 Gunzenhausen, Germany: server hosting in Germany. A data processing agreement under Art. 28 GDPR is in place with Hetzner.
- **Hetzner Online GmbH** (as above): database backups in Hetzner Object Storage at a different location from the server (Helsinki1). The data processing agreement with Hetzner covers this too.
- **healthchecks.io** (SIA Monkey See Monkey Do, Latvia): receives only technical status signals ("server is running") and **no** personal data of users.
- **Other members of the same map** see map content, the members' character names and, if enabled, live locations.

Data is not passed on to any other third parties and is not sold.

## 8. Deletion

- Connections and systems are removed automatically as described in the table.
- If a subscription lapses, the map is deleted on the server **2 months after it lapses**. Local copies in the members' desktop apps are not affected and are the members' own responsibility.
- An account can be deleted at any time by email to luther-tobias@gmx.de or in the app under Cloud → Account → "Delete account …". Linked characters and sessions are deleted immediately. Payment data and payment confirmations are kept for another 12 months and then deleted for good together with the rest of the account; only the free-trial entry remains (see the table). Local maps stay on your computer.
- Backups are overwritten after 30 days at the latest.

## 9. Your rights

You have the right to:
- access (Art. 15 GDPR)
- rectification (Art. 16)
- erasure (Art. 17)
- restriction of processing (Art. 18)
- data portability (Art. 20)
- object to processing based on legitimate interests (Art. 21)
- withdraw consent, for example for live locations, at any time with effect for the future (Art. 7(3))

An email to luther-tobias@gmx.de is enough for any request. Please include your character ID or character name.

## 10. Security

- All connections are encrypted with TLS.
- The server can be reached only with an SSH key.
- Backups are encrypted before they are uploaded; the storage provider sees only encrypted data.
- The wallet token of the receiving character is also stored encrypted.

## 11. Changes

This policy is updated when the service changes. Version: 17 November 2026.
