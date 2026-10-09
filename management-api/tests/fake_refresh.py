"""A `/refresh` endpoint standing in for a config generator's.

It records every POST on arrival, before any configured delay, so a test can
read the order and spacing of the fan-out from the arrival times.
"""

import http.server
import socketserver
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Hit:
    ts: float
    path: str


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    # A handler still sleeping out its delay must not hold up `stop`.
    block_on_close = False

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.hits: list[Hit] = []
        self.status = 200
        self.delay: float | None = None


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        server = self.server
        assert isinstance(server, _Server)
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        server.hits.append(Hit(ts=time.monotonic(), path=self.path))

        if server.delay:
            time.sleep(server.delay)
        try:
            self.send_response(server.status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            ok = 200 <= server.status < 300
            self.wfile.write(b'{"status":"ok"}' if ok else b'{"error":"forced"}')
        except (BrokenPipeError, ConnectionResetError):
            # The API timed out and closed the connection during the delay.
            pass


class FakeRefresh:
    def __init__(self) -> None:
        self._server = _Server()
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.02}
        )
        self._thread.start()

    @property
    def url(self) -> str:
        port: int = self._server.server_address[1]
        return f"http://127.0.0.1:{port}/refresh"

    @property
    def hits(self) -> list[Hit]:
        return list(self._server.hits)

    def reset(self) -> None:
        self._server.hits.clear()

    def configure(self, *, status: int = 200, delay: float | None = None) -> None:
        self._server.status = status
        self._server.delay = delay

    def stop(self) -> None:
        """Connections are refused from here on. Safe to call twice."""
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()
