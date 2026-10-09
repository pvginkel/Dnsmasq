"""The dnsmasq beside `serve`, signalled to reload its config.

The two share the pod's process namespace and the volume dnsmasq writes its
pid file into.
"""

import logging
import os
import signal
import time

logger = logging.getLogger(__name__)


class Dnsmasq:
    def __init__(
        self,
        pid_file: str,
        *,
        pid_wait_step: float = 1.0,
        recheck_timeout: float = 5.0,
        recheck_step: float = 0.1,
    ) -> None:
        self._pid_file = pid_file
        self._pid_wait_step = pid_wait_step
        self._recheck_timeout = recheck_timeout
        self._recheck_step = recheck_step

    def read_pid(self) -> int | None:
        """The pid in the pid file, or None while it is missing or unparseable."""
        try:
            with open(self._pid_file) as f:
                return int(f.read().strip())
        except (FileNotFoundError, ValueError):
            return None

    def wait_for_pid(self) -> int:
        """Block until dnsmasq has written its pid file."""
        while True:
            pid = self.read_pid()
            if pid is not None:
                return pid
            logger.info("DNSMasq not ready; waiting...")
            time.sleep(self._pid_wait_step)

    def reload(self) -> None:
        """SIGHUP dnsmasq, once it has written its pid file."""
        pid = self.wait_for_pid()
        logger.info("SIGHUP dnsmasq (pid %d)", pid)
        self._signal(pid)

    def _signal(self, pid: int) -> None:
        """SIGHUP `pid`. When it is gone, poll the pid file briefly for the pid
        of a restarted dnsmasq and signal that one instead."""
        try:
            os.kill(pid, signal.SIGHUP)
            return
        except ProcessLookupError:
            pass

        deadline = time.monotonic() + self._recheck_timeout
        last_pid = pid
        while time.monotonic() < deadline:
            new_pid = self.read_pid()
            if new_pid is not None and new_pid != last_pid:
                try:
                    os.kill(new_pid, signal.SIGHUP)
                    logger.info(
                        "SIGHUP target moved from pid %d to pid %d", pid, new_pid
                    )
                    return
                except ProcessLookupError:
                    last_pid = new_pid
            time.sleep(self._recheck_step)

        logger.warning(
            "SIGHUP target pid %d not found and no fresh pid appeared within %.1fs; "
            "files are on disk and will be picked up on next dnsmasq start",
            pid,
            self._recheck_timeout,
        )
