"""The desktop half of the SSO login: a one-shot loopback listener on 127.0.0.1 (plan §6)."""
import secrets
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

DONE_PAGE = (b"<!doctype html><meta charset='utf-8'><title>Homebound</title>"
             b"<body style='font:16px system-ui;background:#0f1216;color:#d8dee6;display:grid;place-items:center;"
             b"height:100vh;margin:0'><p>Signed in. You can close this tab and go back to Homebound.</p>")


class LoopbackLogin:
    """Waits for the browser to come back with the one-time code.

    ``on_code(code)`` is called from the listener thread with the code once the state matched.
    """

    def __init__(self, on_code: Callable[[str], object]) -> None:
        """Pick a random state and a free port and start listening."""
        self.state = secrets.token_urlsafe(16)
        self.on_code = on_code
        login = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:   # noqa: N802 - http.server's name
                url = urlparse(self.path)
                query = parse_qs(url.query)
                if url.path != "/cb" or query.get("state") != [login.state] or not query.get("code"):
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"This sign-in link doesn't belong to this Homebound window.")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(DONE_PAGE)
                threading.Thread(target=login.stop, daemon=True).start()
                login.on_code(query["code"][0])

            def log_message(self, *args) -> None:
                pass

        try:
            self.server: HTTPServer | None = HTTPServer(("127.0.0.1", 0), Handler)
        except OSError:
            self.server = None            # no loopback: the user pastes the code instead
        self.port = self.server.server_address[1] if self.server else None
        if self.server:
            threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        """Stop listening."""
        if self.server is not None:
            server, self.server = self.server, None
            server.shutdown()
            server.server_close()
