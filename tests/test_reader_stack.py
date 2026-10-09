"""`serve` with no dnsmasq beside it, as DHCPApp's own pod runs it
(`dhcp-app-deployment.yaml`): an empty DNSMASQ_PID_FILE, so nothing to signal
and nothing to wait for."""

import time

import requests
import yaml

from tier.stacks import READER_SERVE_PORT, Stack

DYNAMIC = "/stack/data/reservations/state.yaml"
TARGET = "/stack/generated/dhcp-hosts"


def _refresh(stack: Stack) -> tuple[requests.Response, float]:
    started = time.monotonic()
    r = requests.post(stack.url(READER_SERVE_PORT, "/refresh"), timeout=30.0)
    return r, time.monotonic() - started


def test_reader_starts_without_dnsmasq(reader_stack: Stack) -> None:
    """The native sidecar's startup probe on /ready passed, or the pod's other
    containers would not have started, and the target is rendered."""
    r = requests.get(reader_stack.url(READER_SERVE_PORT, "/ready"), timeout=5.0)
    assert r.status_code == 200
    assert reader_stack.control.read(TARGET) is not None


def test_reader_refresh_renders_reservations_and_returns(reader_stack: Stack) -> None:
    reader_stack.control.write(
        DYNAMIC,
        yaml.safe_dump(
            {
                "hosts": [
                    {
                        "name": "host-a",
                        "address": "10.99.4.5",
                        "mac": "02:00:00:00:00:01",
                    }
                ]
            }
        ),
    )

    r, elapsed = _refresh(reader_stack)

    assert r.status_code == 200
    assert r.json()["changed"] is True
    # A SIGHUP attempt with no dnsmasq would hang, not merely slow down.
    assert elapsed < 5.0
    assert "set:intranet,id:*,02:00:00:00:00:01,10.99.4.5,host-a.home" in (
        reader_stack.control.read(TARGET) or ""
    )

    r, elapsed = _refresh(reader_stack)

    assert r.json()["changed"] is False
    assert elapsed < 5.0
