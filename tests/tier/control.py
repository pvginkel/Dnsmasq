"""The suite's side of the control agent (`tier.agent`)."""

from dataclasses import dataclass
from typing import Any

import requests

from .waiting import wait_for


@dataclass(frozen=True)
class Process:
    pid: int
    argv: list[str]


class Control:
    def __init__(self, url: str) -> None:
        self._url = url

    def _request(self, method: str, route: str, **kwargs: Any) -> requests.Response:
        return requests.request(method, f"{self._url}{route}", timeout=10.0, **kwargs)

    def wait_healthy(self, timeout: float) -> None:
        def healthy() -> bool | None:
            try:
                return self._request("GET", "/healthz").status_code == 200 or None
            except requests.ConnectionError:
                return None

        wait_for(healthy, timeout, "the stack's control agent")

    def read(self, path: str) -> str | None:
        """The file's text, or None when it does not exist."""
        r = self._request("GET", "/file", params={"path": path})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content.decode()

    def write(self, path: str, text: str) -> None:
        self._request(
            "PUT", "/file", params={"path": path}, data=text.encode()
        ).raise_for_status()

    def remove(self, path: str) -> None:
        """Removes the file if it exists."""
        r = self._request("DELETE", "/file", params={"path": path})
        if r.status_code != 404:
            r.raise_for_status()

    def processes(self) -> list[Process]:
        r = self._request("GET", "/processes")
        r.raise_for_status()
        return [Process(p["pid"], p["argv"]) for p in r.json()]

    def signal(self, pid: int, name: str) -> None:
        self._request(
            "POST", "/signal", params={"pid": pid, "signal": name}
        ).raise_for_status()

    def lease_changes(self) -> list[dict[str, str]]:
        r = self._request("GET", "/lease-changes")
        r.raise_for_status()
        changes: list[dict[str, str]] = r.json()
        return changes

    def alive(self, pid: int) -> bool:
        return any(p.pid == pid for p in self.processes())

    def dnsmasq_pid(self, run_dir: str, timeout: float = 30.0) -> int:
        """The pid in `run_dir`'s dnsmasq pid file, once it names a live dnsmasq.

        dnsmasq leaves its pid file behind when it exits, so a pid file alone
        does not mean dnsmasq is running.
        """

        def live_pid() -> int | None:
            text = self.read(f"{run_dir}/dnsmasq.pid")
            if not text or not text.strip().isdigit():
                return None
            pid = int(text)
            running = {p.pid: p.argv for p in self.processes()}
            if pid in running and running[pid][0].endswith("dnsmasq"):
                return pid
            return None

        return wait_for(live_pid, timeout, f"a live dnsmasq pid in {run_dir}")

    def wait_gone(self, pid: int, timeout: float = 15.0) -> None:
        wait_for(lambda: not self.alive(pid) or None, timeout, f"pid {pid} to exit")

    def serve_pid(self) -> int:
        """The pid of the one `python main.py serve` in the pod."""
        serving = [
            p.pid for p in self.processes() if p.argv[-2:] == ["main.py", "serve"]
        ]
        assert len(serving) == 1, f"expected one serve process, found {serving}"
        return serving[0]
