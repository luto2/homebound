# Plan: TLS certificates in the packaged app (AppImage on Arch)

Status: implemented (2026-11-10, commit `e970367`). Deviations: certifi is loaded on top of the system store (not instead of it), `sync/api.py` is covered too, and the first-run dialog shows a certificate hint. Not yet tested on Arch: check that `certifi/cacert.pem` is in `dist/homebound/_internal` at the next build and that the AppImage downloads the data on CachyOS.

- **Audience:** the owner and coding agents who need to know why something was built the way it is.
- **Scope:** the record of "Plan: TLS certificates in the packaged app (AppImage on Arch)", finished. The status line says where the build differs from the plan; where the code lives now is in [`code-map.md`](../../code-map.md).
- **Kept current:** archived with the plan, not updated after it was implemented (see the status line). The rest of the text is the plan as it was written.

Depends on: nothing. One commit for the fix, one for the test. Ship it in the next AppImage, because the
bug stops the app before the first window.

## Problem (reported, not reproduced here)

On CachyOS the 1.0.0 AppImage fails its first-run universe build with
`URLError: <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] … unable to get local issuer certificate>`.
Without the universe file the app can't start, so this blocks every Arch-family user of the AppImage.

## Root cause (likely, from the code)

The bundled Python uses the OpenSSL built on the Ubuntu CI runner, whose default CA paths
(`/usr/lib/ssl/cert.pem`, `/usr/lib/ssl/certs`) don't exist on Arch. Any code that relies on the
default SSL context fails there. Checked which network code does that:

| Caller | Library | CA source | Affected |
|---|---|---|---|
| `universe/build_static.py` `fetch`, `zkill_statics` | `urllib.request.urlopen` | OpenSSL default paths | **yes** |
| `zkill.py`, `eve_scout.py` via `http.make_client` | httpx | certifi (httpx default) | no, if certifi is bundled |
| `sync/api.py` | httpx | certifi | no |
| `sync/client.py` `SocketWorker` | `websockets` | `ssl.create_default_context()` | **yes**: cloud sync would fail the same way |

certifi is already in `uv.lock` as an httpx dependency, and PyInstaller has a hook that bundles its
`cacert.pem`.

## Change

1. Add `homebound/http.py` `ssl_context()`: an `ssl.create_default_context()`, plus
   `load_verify_locations(certifi.where())`. Loading certifi on top keeps the system store (a company
   proxy CA still works) and adds a known-good bundle. Cache it with `functools.cache`.
2. `build_static.fetch` and `zkill_statics`: pass `context=ssl_context()` to `urlopen`. While there,
   put `zkill_statics`' `urlopen` in a `with` (small item E15).
3. `sync/client.py`: pass `ssl=ssl_context()` to `websockets.connect` for `wss://` URLs.
4. `http.make_client`: pass `verify=ssl_context()` so all three libraries trust the same set.
5. Add `certifi` to `[project].dependencies` in `pyproject.toml`: it is imported directly now.
6. Check `packaging/homebound.spec` collects certifi's data (the stock hook should; confirm
   `certifi/cacert.pem` is in the built folder).

## Tests

- `ssl_context()` returns a context with `verify_mode == CERT_REQUIRED` and at least one CA loaded
  (`cert_store_stats()["x509_ca"] > 0`).
- `fetch` passes a context to `urlopen` (patch `urlopen`, assert the `context` keyword).
- `SocketWorker` passes `ssl=` for `wss://` and nothing for `ws://`.
- Manual, before release: run the AppImage on Arch (or in an `archlinux` container) with an empty data
  folder and watch the build finish. The same run with `SSL_CERT_FILE=/nonexistent` must still work,
  because certifi is loaded explicitly.

## Risks

- certifi's bundle ages with the release. That is the usual trade-off; the system store is still
  loaded first, so a fresh system bundle wins where it exists.

## Open decisions

1. Should the first-run dialog (`BuildDataDialog`) show a specific hint for certificate errors, such as
   "Your system's certificates weren't found"? Recommended: yes, a short line, since this is the error
   a user is most likely to hit on an unusual distro.
