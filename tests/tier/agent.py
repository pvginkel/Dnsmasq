"""The control agent: what the suite does inside a running stack, served over
the pod network.

The suite may not exec into a stack, so this agent runs as a container of the
stack pod, sharing its process namespace and mounting its volumes. It runs
from the config-generator image as `python -c <this source>` and imports
nothing outside the standard library.

    GET    /healthz
    GET    /file?path=P      the file's bytes; 404 when it does not exist
    PUT    /file?path=P      writes the body, creating parent directories
    DELETE /file?path=P      removes it; 404 when it does not exist
    GET    /processes        [{"pid": N, "argv": [...]}], every process in the pod
    POST   /signal?pid=N&signal=HUP
    POST   /lease-change     records the request: the stack's renewal endpoint
    GET    /lease-changes    [{"content_type": ..., "body": ...}], in arrival order
"""

import json
import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

_lease_changes: list[dict[str, str]] = []
_lease_lock = threading.Lock()


def _processes() -> list[dict[str, Any]]:
    processes = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as f:
                raw = f.read()
        except (FileNotFoundError, ProcessLookupError):
            continue
        argv = [part.decode() for part in raw.split(b"\0") if part]
        if argv:
            processes.append({"pid": int(entry), "argv": argv})
    return processes


class Handler(BaseHTTPRequestHandler):
    def _query(self) -> tuple[str, dict[str, str]]:
        parts = urlsplit(self.path)
        return parts.path, {k: v[0] for k, v in parse_qs(parts.query).items()}

    def _body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length", "0")))

    def _reply(self, status: int, body: bytes = b"", ctype: str = "text/plain") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, value: Any) -> None:
        self._reply(200, json.dumps(value).encode(), "application/json")

    def do_GET(self) -> None:
        route, query = self._query()
        if route == "/healthz":
            self._reply(200, b"ok")
        elif route == "/file":
            try:
                with open(query["path"], "rb") as f:
                    self._reply(200, f.read(), "application/octet-stream")
            except FileNotFoundError:
                self._reply(404)
        elif route == "/processes":
            self._json(_processes())
        elif route == "/lease-changes":
            with _lease_lock:
                self._json(list(_lease_changes))
        else:
            self._reply(404)

    def do_PUT(self) -> None:
        route, query = self._query()
        if route != "/file":
            self._reply(404)
            return
        path = query["path"]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(self._body())
        self._reply(204)

    def do_DELETE(self) -> None:
        route, query = self._query()
        if route != "/file":
            self._reply(404)
            return
        try:
            os.unlink(query["path"])
        except FileNotFoundError:
            self._reply(404)
            return
        self._reply(204)

    def do_POST(self) -> None:
        route, query = self._query()
        if route == "/signal":
            try:
                os.kill(int(query["pid"]), signal.Signals[f"SIG{query['signal']}"])
            except ProcessLookupError:
                self._reply(404)
                return
            self._reply(204)
        elif route == "/lease-change":
            change = {
                "content_type": self.headers.get("Content-Type", ""),
                "body": self._body().decode(),
            }
            with _lease_lock:
                _lease_changes.append(change)
            self._reply(204)
        else:
            self._reply(404)


def main() -> None:
    ThreadingHTTPServer(("", int(sys.argv[1])), Handler).serve_forever()


if __name__ == "__main__":
    main()
