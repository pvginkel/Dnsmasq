"""Signalling dnsmasq through its pid file, against a stand-in process."""

import threading
import time
from pathlib import Path

import pytest
from standin import DnsmasqStandIn

from config_generator.dnsmasq import Dnsmasq


def test_reload_sighups_the_pid_in_the_pid_file(
    dnsmasq_standin: DnsmasqStandIn,
) -> None:
    Dnsmasq(str(dnsmasq_standin.pid_file)).reload()

    dnsmasq_standin.wait_hups(1)


def test_reload_waits_for_the_pid_file(tmp_path: Path) -> None:
    standin = DnsmasqStandIn(tmp_path / "run")
    dnsmasq = Dnsmasq(str(standin.pid_file), pid_wait_step=0.05)
    reload = threading.Thread(target=dnsmasq.reload)
    reload.start()
    try:
        time.sleep(0.2)
        assert reload.is_alive()

        standin.start()
        reload.join(timeout=5.0)

        assert not reload.is_alive()
        standin.wait_hups(1)
    finally:
        standin.stop()


def test_reload_follows_a_restarted_dnsmasq(
    dnsmasq_standin: DnsmasqStandIn, caplog: pytest.LogCaptureFixture
) -> None:
    """The pid in the pid file is gone; a restarted dnsmasq writes its fresh pid
    within the re-poll window and is signalled instead."""
    stale_pid = dnsmasq_standin.pid
    dnsmasq_standin.stop()
    restart = threading.Timer(0.3, dnsmasq_standin.start)
    restart.start()

    Dnsmasq(str(dnsmasq_standin.pid_file), recheck_timeout=5.0).reload()

    restart.join()
    dnsmasq_standin.wait_hups(1)
    assert (
        f"SIGHUP target moved from pid {stale_pid} to pid {dnsmasq_standin.pid}"
        in caplog.text
    )


def test_reload_gives_up_when_no_fresh_pid_appears(
    dnsmasq_standin: DnsmasqStandIn, caplog: pytest.LogCaptureFixture
) -> None:
    dnsmasq_standin.stop()

    t0 = time.monotonic()
    Dnsmasq(str(dnsmasq_standin.pid_file), recheck_timeout=0.3).reload()

    assert time.monotonic() - t0 >= 0.3
    assert "no fresh pid appeared within 0.3s" in caplog.text
