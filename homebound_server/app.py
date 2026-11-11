"""HTTPS API and the sync WebSocket (protocol §1, §10), on Starlette."""
import asyncio
import html
import json
import logging
import time
from collections import defaultdict, deque
from urllib.parse import urlencode

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from homebound.sync.protocol import CLIENT_ID, MAX_MESSAGE_BYTES, MIN_PROTOCOL, PROTOCOL, peer_protocol

from .auth import AuthError
from .billing import BillingError
from .hub import Connection
from .maps import MapError
from .service import Service

log = logging.getLogger("homebound.server")
SOCKETS_PER_ACCOUNT = 5
LOGINS_PER_MINUTE = 10
IDLE_SECONDS = 90


def _version(text: str) -> tuple[int, ...]:
    """Turn ``"0.4.2"`` into ``(0, 4, 2)`` (junk counts as 0)."""
    parts = []
    for part in str(text).split("."):
        digits = "".join(character for character in part if character.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts)


def page(title: str, body: str, status: int = 200) -> HTMLResponse:
    """A minimal page for the browser side of the login."""
    return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)} · Homebound</title>
<style>body{{font:16px system-ui,sans-serif;background:#0f1216;color:#d8dee6;display:grid;place-items:center;
min-height:100vh;margin:0;padding:16px}}main{{max-width:28rem}}code{{font-size:1.4rem;background:#1b2128;
padding:.3rem .6rem;border-radius:6px;user-select:all}}a,button{{color:#7cc4ff}}</style></head>
<body><main><h1>{html.escape(title)}</h1>{body}</main></body></html>""", status)


def create_app(service: Service) -> Starlette:
    """Build the web app around a :class:`Service`."""
    login_hits: dict[str, deque] = defaultdict(deque)
    sockets: dict[int, set] = defaultdict(set)

    def limited(request: Request) -> bool:
        """10 logins / code exchanges per IP per minute."""
        address = request.client.host if request.client else "?"
        hits, now = login_hits[address], time.monotonic()
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= LOGINS_PER_MINUTE:
            return True
        hits.append(now)
        return False

    def error(code: str, detail: str = "", status: int = 400, **extra) -> JSONResponse:
        """A JSON error response."""
        return JSONResponse({"error": code, "detail": detail or code, **extra}, status)

    def session(request: Request) -> dict:
        """Return the session of a ``Authorization: Bearer`` request.

        Raises:
            AuthError: For a missing or bad token.
        """
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            raise AuthError("unauthorized")
        return service.auth.session(header[7:].strip())

    def api(handler):
        """Wrap an API handler: session check and error mapping."""
        async def endpoint(request: Request) -> Response:
            try:
                current = session(request)
                body = {}
                if request.method in ("POST", "PATCH", "PUT"):
                    raw = await request.body()
                    if len(raw) > 4 * MAX_MESSAGE_BYTES * 8:
                        return error("too_large", status=413)
                    body = json.loads(raw or b"{}")
                    if not isinstance(body, dict):
                        return error("invalid", "The body must be a JSON object.")
                result = handler(request, current, body)
                return result if isinstance(result, Response) else JSONResponse(result)
            except AuthError as problem:
                status = {"unauthorized": 401, "relogin_due": 401, "invalid": 400}.get(problem.code, 403)
                return error(problem.code, str(problem), status)
            except (MapError, BillingError) as problem:
                status = {"not_member": 403, "forbidden": 403, "map_deleted": 404, "unavailable": 503}.get(problem.code, 400)
                return error(problem.code, problem.detail, status)
            except (ValueError, KeyError, TypeError) as problem:
                return error("invalid", str(problem))
        return endpoint

    # ------------------------------------------------------------ login (browser side)
    async def login(request: Request) -> Response:
        if limited(request):
            return page("Too many logins", "<p>Please wait a minute and try again.</p>", 429)
        query = request.query_params
        try:
            port = int(query["port"]) if query.get("port") else None
            state = service.auth.start_login(port, query.get("state", ""), query.get("link"))
        except (AuthError, ValueError) as problem:
            return page("Can't sign in", f"<p>{html.escape(str(problem))}</p>", 400)
        return RedirectResponse(service.eve.authorize_url(state), 302)

    async def dev_sso(request: Request) -> Response:
        if not service.config.dev:
            return Response(status_code=404)
        state = request.query_params.get("state", "")
        if "char_id" in request.query_params:
            code = f"fake:{int(request.query_params['char_id'])}:{request.query_params.get('name', 'Dev Pilot')}"
            return RedirectResponse("/sso/callback?" + urlencode({"code": code, "state": state}), 302)
        return page("Dev login", f"""<form><input type="hidden" name="state" value="{html.escape(state)}">
<p><label>Character ID <input name="char_id" value="90000001"></label></p>
<p><label>Name <input name="name" value="Dev Pilot"></label></p><button>Sign in</button></form>""")

    async def link_wallet(request: Request) -> Response:
        if limited(request):
            return page("Too many logins", "<p>Please wait a minute and try again.</p>", 429)
        try:
            return RedirectResponse(service.start_receiver_link(request.query_params.get("key", "")), 302)
        except AuthError as problem:
            return page("Can't link the receiving character", f"<p>{html.escape(str(problem))}</p>", 403)

    async def receiver_callback(query) -> Response:
        try:
            name = await asyncio.to_thread(service.finish_receiver_link, query.get("state", ""), query.get("code", ""))
        except AuthError as problem:
            return page("Can't link the receiving character", f"<p>{html.escape(str(problem))}</p>", 403)
        except Exception as problem:   # noqa: BLE001 - EVE SSO answered badly
            log.warning("receiver link failed: %s", problem)
            return page("Can't link the receiving character", "<p>EVE's login didn't answer as expected.</p>", 502)
        return page("Receiving character linked", f"<p>{html.escape(name)} is now the receiving character. "
                                                  "The server reads its wallet and mail and sends payment receipts.</p>")

    async def sso_callback(request: Request) -> Response:
        query = request.query_params
        if service.is_receiver_state(query.get("state", "")):
            return await receiver_callback(query)
        try:
            port, client_state, code = await asyncio.to_thread(service.finish_sso, query.get("state", ""),
                                                               query.get("code", ""))
        except AuthError as problem:
            return page("Can't sign in", f"<p>{html.escape(str(problem))}</p>", 400)
        except Exception as problem:   # noqa: BLE001 - EVE SSO answered badly
            log.warning("SSO callback failed: %s", problem)
            return page("Can't sign in", "<p>EVE's login didn't answer as expected. Please try again.</p>", 502)
        if port is None:
            return page("Signed in", f"<p>Paste this code into Homebound:</p><p><code>{html.escape(code)}</code></p>"
                                     "<p>It works once, for 60 seconds.</p>")
        return RedirectResponse(f"http://127.0.0.1:{port}/cb?" + urlencode({"code": code, "state": client_state}), 302)

    async def exchange(request: Request) -> Response:
        if limited(request):
            return error("rate_limited", status=429, retry_after=60)
        try:
            body = json.loads(await request.body() or b"{}")
            token = service.auth.exchange_code(str(body.get("code", "")), str(body.get("state", "")),
                                               str(body.get("device", "")))
        except AuthError as problem:
            return error(problem.code, str(problem), 401)
        except ValueError:
            return error("invalid")
        return JSONResponse({"token": token})

    # ------------------------------------------------------------ API
    def logout(request, current, body):
        service.auth.revoke(current["account_id"], token=request.headers["authorization"][7:].strip())
        return {"ok": True}

    def sessions_list(request, current, body):
        return {"sessions": service.auth.sessions(current["account_id"]), "current": current["token_hash"][:16]}

    def session_delete(request, current, body):
        return {"revoked": service.auth.revoke(current["account_id"], session_id=request.path_params["session"])}

    async def defer(request: Request) -> Response:
        # works exactly when the session check says relogin_due, so it can't go through api()
        header = request.headers.get("authorization", "")
        try:
            return JSONResponse({"until": service.auth.defer(header[7:].strip() if header.lower().startswith("bearer ")
                                                             else "")})
        except AuthError as problem:
            return error(problem.code, str(problem), 401 if problem.code == "unauthorized" else 403)

    def account_get(request, current, body):
        return service.account(current["account_id"])

    def account_delete(request, current, body):
        if body.get("confirm") != "DELETE":
            raise MapError("invalid", 'Send {"confirm": "DELETE"} to delete the account.')
        service.delete_account(current["account_id"])
        return {"ok": True}

    def account_export(request, current, body):
        return service.export(current["account_id"])

    def link_character(request, current, body):
        code = service.auth.create_link_code(current["account_id"])
        return {"link_code": code, "url": f"{service.config.base_url}/login?" + urlencode({"link": code})}

    def unlink_character(request, current, body):
        service.auth.unlink(current["account_id"], int(request.path_params["char_id"]))
        return {"ok": True}

    def maps_create(request, current, body):
        client_id = str(body.get("client_id", ""))
        if not CLIENT_ID.match(client_id):
            raise MapError("invalid", "bad client_id")
        return service.create_map(current["account_id"], client_id, str(body.get("name", "")),
                                  body.get("char_id"), bool(body.get("corp")), body.get("ops") or [])

    def map_get(request, current, body):
        map_id = request.path_params["map_id"]
        service.maps.require_role(map_id, current["account_id"], ("owner", "manager", "editor", "viewer"))
        the_map = service.maps.map(map_id)
        return {"map": {key: the_map[key] for key in ("id", "name", "created_at", "seq", "corp_join", "seat_limit")},
                "access": service.access.entries(map_id), "restricted": service.access.is_restricted(map_id),
                "members": service.maps.members(map_id), "role": service.maps.role(map_id, current["account_id"]),
                "status": service.billing.status(map_id, current["account_id"]),
                "invites": service.maps.invites(map_id) if service.maps.role(map_id, current["account_id"])
                in ("owner", "manager") else []}

    def map_delete(request, current, body):
        map_id = request.path_params["map_id"]
        service.maps.require_role(map_id, current["account_id"], ("owner",))
        service.maps.delete(map_id)
        return {"ok": True}

    def invites_create(request, current, body):
        return service.maps.create_invite(request.path_params["map_id"], current["account_id"],
                                          str(body.get("role", "editor")), int(body.get("hours", 72)),
                                          int(body.get("uses", 1)))

    def invite_delete(request, current, body):
        return {"ok": service.maps.revoke_invite(request.path_params["map_id"], current["account_id"],
                                                 request.path_params["code"])}

    def invite_accept(request, current, body):
        return service.accept_invite(request.path_params["code"], current["account_id"])

    def member_patch(request, current, body):
        service.maps.set_role(request.path_params["map_id"], current["account_id"],
                              int(request.path_params["account"]), str(body.get("role", "")))
        return {"ok": True}

    def member_delete(request, current, body):
        service.maps.remove_member(request.path_params["map_id"], current["account_id"],
                                   int(request.path_params["account"]))
        return {"ok": True}

    def corp_settings(request, current, body):
        service.corps.set_join(request.path_params["map_id"], current["account_id"], bool(body.get("corp_join")),
                               body.get("seat_limit"))
        return {"ok": True}

    def access_add(request, current, body):
        return service.add_access(request.path_params["map_id"], current["account_id"], str(body.get("kind", "")),
                                  str(body.get("value", "")))

    def access_delete(request, current, body):
        removed = service.access.remove(request.path_params["map_id"], current["account_id"],
                                        request.path_params["kind"], int(request.path_params["subject"]))
        return {"removed": len(removed)}

    def corp_join(request, current, body):
        return {"role": service.corps.join(request.path_params["map_id"], current["account_id"])}

    def billing_quote(request, current, body):
        query = request.query_params
        payer_id = service.maps.payer_for_account(current["account_id"])
        if query.get("map"):
            map_id = query["map"]
            service.maps.require_role(map_id, current["account_id"], ("owner", "manager"))
            payer_id = service.db.value("SELECT payer_id FROM maps WHERE id=?", (map_id,))
            return service.billing.quote(payer_id, extra_seats={map_id: int(query.get("seats", 1))})
        return service.billing.quote(payer_id, new_map_seats=int(query["new_map_seats"]) if query.get("new_map_seats")
                                     else None)

    async def health(request: Request) -> Response:
        try:
            return JSONResponse(service.health())
        except Exception as problem:   # noqa: BLE001
            return JSONResponse({"ok": False, "detail": str(problem)}, 503)

    # ------------------------------------------------------------ WebSocket
    async def websocket(socket: WebSocket) -> None:
        await socket.accept()
        outbox: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        connection: Connection | None = None

        def deliver(message: dict | None) -> None:
            # the hub may call this from another thread (jobs) or another socket's loop
            loop.call_soon_threadsafe(outbox.put_nowait, message)

        async def writer() -> None:
            while True:
                message = await outbox.get()
                if message is None:
                    return
                await socket.send_text(json.dumps(message))

        writer_task = asyncio.create_task(writer())
        try:
            while True:
                try:
                    text = await asyncio.wait_for(socket.receive_text(), IDLE_SECONDS)
                except TimeoutError:
                    break
                if len(text.encode()) > MAX_MESSAGE_BYTES:
                    outbox.put_nowait({"t": "error", "code": "too_large"})
                    continue
                try:
                    message = json.loads(text)
                except ValueError:
                    outbox.put_nowait({"t": "error", "code": "invalid"})
                    continue
                if not isinstance(message, dict):
                    continue
                kind, request_id = message.get("t"), message.get("id")
                if connection is None:
                    if kind != "hello":
                        outbox.put_nowait({"t": "error", "re": request_id, "code": "unauthorized"})
                        continue
                    if not isinstance(message.get("protocol"), int) or message["protocol"] < MIN_PROTOCOL \
                            or _version(message.get("app", "0")) < _version(service.config.min_app):
                        outbox.put_nowait({"t": "error", "re": request_id, "code": "upgrade_required",
                                           "min_app": service.config.min_app})
                        break
                    try:
                        current = service.auth.session(str(message.get("token", "")))
                    except AuthError as problem:
                        outbox.put_nowait({"t": "error", "re": request_id, "code": problem.code})
                        break
                    client_id = str(message.get("client_id", ""))
                    if not CLIENT_ID.match(client_id):
                        outbox.put_nowait({"t": "error", "re": request_id, "code": "invalid", "detail": "bad client_id"})
                        break
                    if len(sockets[current["account_id"]]) >= SOCKETS_PER_ACCOUNT:
                        outbox.put_nowait({"t": "error", "re": request_id, "code": "rate_limited",
                                           "detail": "too many sockets"})
                        break
                    connection = Connection(current["account_id"], client_id, deliver,
                                            web=bool(message.get("web")), protocol=peer_protocol(message["protocol"]))
                    sockets[current["account_id"]].add(connection.id)
                    outbox.put_nowait({"t": "welcome", "re": request_id, "account_id": current["account_id"],
                                       "protocol": PROTOCOL,
                                       "characters": service.auth.characters(current["account_id"]),
                                       "server_time": int(time.time() * 1000)})
                    continue
                if kind == "ping":
                    outbox.put_nowait({"t": "pong", "re": request_id, "server_time": int(time.time() * 1000)})
                elif kind == "join":
                    service.hub.join(connection, message.get("map"), message.get("last_seq") or 0, request_id)
                elif kind == "leave":
                    service.hub.leave(connection, message.get("map"))
                elif kind == "push":
                    service.hub.push(connection, message)
                elif kind == "presence":
                    service.hub.report_presence(connection, message)
        except WebSocketDisconnect:
            pass
        finally:
            if connection is not None:
                service.hub.disconnect(connection)
                sockets[connection.account_id].discard(connection.id)
            outbox.put_nowait(None)
            try:
                await writer_task
            except Exception:   # noqa: BLE001 - the socket is gone
                pass
            try:
                await socket.close()
            except Exception:   # noqa: BLE001
                pass

    routes = [
        Route("/login", login), Route("/dev/sso", dev_sso), Route("/sso/callback", sso_callback),
        Route("/admin/link-wallet", link_wallet),
        Route("/api/login/exchange", exchange, methods=["POST"]),
        Route("/api/logout", api(logout), methods=["POST"]),
        Route("/api/sessions", api(sessions_list)),
        Route("/api/sessions/current/defer", defer, methods=["POST"]),
        Route("/api/sessions/{session}", api(session_delete), methods=["DELETE"]),
        Route("/api/account", api(account_get)),
        Route("/api/account/delete", api(account_delete), methods=["POST"]),
        Route("/api/account/export", api(account_export)),
        Route("/api/account/characters", api(link_character), methods=["POST"]),
        Route("/api/account/characters/{char_id:int}", api(unlink_character), methods=["DELETE"]),
        Route("/api/maps", api(maps_create), methods=["POST"]),
        Route("/api/maps/{map_id}", api(map_get)),
        Route("/api/maps/{map_id}/delete", api(map_delete), methods=["POST"]),
        Route("/api/maps/{map_id}/invites", api(invites_create), methods=["POST"]),
        Route("/api/maps/{map_id}/invites/{code}", api(invite_delete), methods=["DELETE"]),
        Route("/api/maps/{map_id}/members/{account:int}", api(member_patch), methods=["PATCH"]),
        Route("/api/maps/{map_id}/members/{account:int}", api(member_delete), methods=["DELETE"]),
        Route("/api/maps/{map_id}/corp", api(corp_settings), methods=["POST"]),
        Route("/api/maps/{map_id}/corp-join", api(corp_join), methods=["POST"]),
        Route("/api/maps/{map_id}/access", api(access_add), methods=["POST"]),
        Route("/api/maps/{map_id}/access/{kind}/{subject:int}", api(access_delete), methods=["DELETE"]),
        Route("/api/invites/{code}/accept", api(invite_accept), methods=["POST"]),
        Route("/api/billing/quote", api(billing_quote)),
        Route("/api/health", health),
        WebSocketRoute("/ws", websocket),
    ]
    app = Starlette(routes=routes)
    app.state.service = service
    return app
