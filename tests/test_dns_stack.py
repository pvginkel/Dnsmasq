"""The config generator beside dnsmasq, in the chart's DNS replica shape.

`generate` renders the static config before dnsmasq starts; `serve` renders the
dynamic config on `/refresh` and SIGHUPs dnsmasq across the shared process
namespace.
"""

import time
from collections.abc import Iterator
from typing import Any

import pytest
import requests
import yaml

from tier import dnsquery
from tier.cluster import Cluster
from tier.stacks import DNS_SERVE_PORT, HOLD_FILE, Stack, wait_serving
from tier.waiting import wait_for

DYNAMIC = "/stack/data/reservations/state.yaml"
TARGET = "/stack/dns-generated/hosts"
RUN = "/stack/dns-run"
# dnsmasq logs this line each time it reads the dynamic hosts file: at start
# and on every SIGHUP.
RELOAD_LINE = "read /etc/dnsmasq-generated.d/hosts"


def _refresh(stack: Stack) -> requests.Response:
    return requests.post(stack.url(DNS_SERVE_PORT, "/refresh"), timeout=30.0)


def _write_dynamic(stack: Stack, hosts: list[dict[str, Any]]) -> None:
    stack.control.write(DYNAMIC, yaml.safe_dump({"hosts": hosts}))


def _start_dnsmasq(stack: Stack) -> None:
    stack.control.remove(HOLD_FILE)
    stack.control.dnsmasq_pid(RUN)
    dnsquery.wait_up(stack.ip, 53)


def _kill_dnsmasq(stack: Stack) -> None:
    """dnsmasq gone and held down, its pid file left behind as after a crash.

    On SIGTERM dnsmasq removes its pid file, which it can in the chart's
    writable run volume.
    """
    stack.control.write(HOLD_FILE, "")
    pid = stack.control.dnsmasq_pid(RUN)
    stack.control.signal(pid, "KILL")
    stack.control.wait_gone(pid)


def _reloads(cluster: Cluster, stack: Stack) -> int:
    return cluster.log(stack.pod, "dns-dnsmasq").count(RELOAD_LINE)


@pytest.fixture
def dns(dns_stack: Stack) -> Iterator[Stack]:
    """dnsmasq up, no dynamic hosts file, and `serve` caught up with that."""
    _start_dnsmasq(dns_stack)
    dns_stack.control.remove(DYNAMIC)
    assert _refresh(dns_stack).status_code == 200
    yield dns_stack
    _start_dnsmasq(dns_stack)
    dns_stack.control.remove(DYNAMIC)


def test_static_hosts_resolve(dns: Stack) -> None:
    dnsquery.wait_a("alpha.home", dns.ip, 53, ["10.99.0.1"])
    dnsquery.wait_a("gamma.home", dns.ip, 53, ["10.99.0.3", "10.99.0.4"])


def test_cname_resolves_to_target_addresses(dns: Stack) -> None:
    """dnsmasq loads the rendered cname.conf through its conf-dir, so the CNAME
    answers with its target's A records."""
    dnsquery.wait_a("omega.home", dns.ip, 53, ["10.99.0.3", "10.99.0.4"])


def test_refresh_with_new_dynamic_entry_resolves(dns: Stack) -> None:
    pid = dns.control.dnsmasq_pid(RUN)
    _write_dynamic(dns, [{"name": "leased", "address": "10.99.1.42"}])

    r = _refresh(dns)

    assert r.status_code == 200
    assert r.json() == {"status": "ok", "changed": True}
    dnsquery.wait_a("leased.home", dns.ip, 53, ["10.99.1.42"])
    # Reloaded by SIGHUP, not restarted.
    assert dns.control.dnsmasq_pid(RUN) == pid


def test_refresh_static_and_dynamic_coexist(dns: Stack) -> None:
    """The static and dynamic sets render into separate files and dnsmasq loads
    both: a name in both resolves to both addresses."""
    _write_dynamic(dns, [{"name": "alpha", "address": "10.99.9.99"}])

    r = _refresh(dns)

    assert r.json() == {"status": "ok", "changed": True}
    assert "10.99.9.99 alpha.home" in (dns.control.read(TARGET) or "")
    dnsquery.wait_a("alpha.home", dns.ip, 53, ["10.99.0.1", "10.99.9.99"])


def test_refresh_against_down_dnsmasq_succeeds_and_persists(dns: Stack) -> None:
    """With dnsmasq down, a refresh writes the file and answers once its SIGHUP
    attempt gives up; dnsmasq reads the file when it comes back."""
    _kill_dnsmasq(dns)
    _write_dynamic(dns, [{"name": "down", "address": "10.99.1.99"}])

    r = _refresh(dns)

    assert r.status_code == 200
    assert r.json() == {"status": "ok", "changed": True}
    assert "10.99.1.99 down.home" in (dns.control.read(TARGET) or "")
    _start_dnsmasq(dns)
    dnsquery.wait_a("down.home", dns.ip, 53, ["10.99.1.99"])


def test_generator_restart_over_matching_files_reloads_nothing(
    dns: Stack, cluster: Cluster
) -> None:
    """`serve` restarting over the files it rendered sends dnsmasq no SIGHUP."""
    _write_dynamic(dns, [{"name": "kept", "address": "10.99.1.7"}])
    reloads = _reloads(cluster, dns)
    assert _refresh(dns).json()["changed"] is True
    # A reload does show in the log.
    wait_for(
        lambda: _reloads(cluster, dns) > reloads or None,
        15.0,
        "dnsmasq to log its re-read of the hosts file",
    )
    reloads = _reloads(cluster, dns)
    pid = dns.control.dnsmasq_pid(RUN)
    restarts = cluster.container(dns.pod, "dns-serve").restart_count

    dns.control.signal(dns.control.serve_pid(), "TERM")

    wait_for(
        lambda: (
            cluster.container(dns.pod, "dns-serve").restart_count > restarts or None
        ),
        60.0,
        "the serve container to restart",
    )
    # `serve` sends any startup SIGHUP before it answers /ready.
    wait_serving(dns, DNS_SERVE_PORT, "/ready")
    time.sleep(1.0)
    assert _reloads(cluster, dns) == reloads
    assert dns.control.dnsmasq_pid(RUN) == pid
