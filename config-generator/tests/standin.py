"""A process standing in for dnsmasq.

Like dnsmasq, it writes its pid file once it is ready for SIGHUP; it counts the
SIGHUPs it receives into a file the test reads.
"""

import subprocess
import sys
import time
from pathlib import Path

_PROGRAM = """
import os
import signal
import sys
import time

pid_file, counter = sys.argv[1], sys.argv[2]
hups = 0


def write(path, text):
    with open(path + ".tmp", "w") as f:
        f.write(text)
    os.replace(path + ".tmp", path)


def on_hup(signum, frame):
    global hups
    hups += 1
    write(counter, str(hups))


signal.signal(signal.SIGHUP, on_hup)
write(counter, "0")
write(pid_file, str(os.getpid()))
while True:
    time.sleep(3600)
"""

# Long enough for a SIGHUP already sent to have been handled.
_SETTLE_SECONDS = 0.3


class DnsmasqStandIn:
    def __init__(self, run_dir: Path) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        self.pid_file = run_dir / "dnsmasq.pid"
        self._counter = run_dir / "hups"
        self._process: subprocess.Popen[bytes] | None = None

    @property
    def pid(self) -> int:
        assert self._process is not None
        return self._process.pid

    def start(self, timeout: float = 10.0) -> None:
        self._process = subprocess.Popen(
            [sys.executable, "-c", _PROGRAM, str(self.pid_file), str(self._counter)]
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.pid_file.exists() and self.pid_file.read_text() == str(self.pid):
                return
            time.sleep(0.02)
        raise TimeoutError("the dnsmasq stand-in did not write its pid file")

    def stop(self) -> None:
        """Kill it, leaving its pid file behind as a crashed dnsmasq does."""
        if self._process is not None and self._process.poll() is None:
            self._process.kill()
            self._process.wait()

    def hups(self) -> int:
        return int(self._counter.read_text())

    def wait_hups(self, expected: int, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.hups() >= expected:
                break
            time.sleep(0.02)
        assert self.hups() == expected

    def assert_hups_settle_at(self, expected: int) -> None:
        time.sleep(_SETTLE_SECONDS)
        assert self.hups() == expected
