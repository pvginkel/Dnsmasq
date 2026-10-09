"""The full chain: a reservation made through the management API reaches both
DNS replicas' answers and the DHCP replica's hosts file, and leaves them when it
is deleted."""

import ipaddress
from typing import Any

import pytest
import requests

from tier import dnsquery
from tier.stacks import AUTH_TOKEN, CHAIN_DNS, MANAGEMENT_PORT, RESERVATION_CIDR, Stack

DHCP_HOSTS = "/stack/dhcp-generated/dhcp-hosts"


def _api(stack: Stack, method: str, path: str, **kwargs: Any) -> requests.Response:
    return requests.request(
        method,
        stack.url(MANAGEMENT_PORT, path),
        headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
        timeout=60.0,
        **kwargs,
    )


def _put(stack: Stack, hostname: str, mac: str) -> dict[str, str]:
    r = _api(stack, "PUT", f"/reservations/{hostname}", json={"mac": mac})
    assert r.status_code == 201
    body: dict[str, str] = r.json()
    return body


def _dhcp_line(reservation: dict[str, str]) -> str:
    return f"{reservation['mac']},{reservation['ipv4']},{reservation['hostname']}.home"


@pytest.fixture
def chain(chain_stack: Stack) -> Stack:
    """No reservations."""
    for item in _api(chain_stack, "GET", "/reservations").json()["reservations"]:
        r = _api(chain_stack, "DELETE", f"/reservations/{item['hostname']}")
        assert r.status_code == 204
    return chain_stack


def test_put_propagates_to_both_dns_replicas(chain: Stack) -> None:
    reservation = _put(chain, "intgtest", "02:00:00:00:c0:01")

    assert reservation["hostname"] == "intgtest"
    ip = reservation["ipv4"]
    assert ipaddress.ip_address(ip) in ipaddress.ip_network(RESERVATION_CIDR)
    for dns_port, _ in CHAIN_DNS.values():
        dnsquery.wait_a("intgtest.home", chain.ip, dns_port, [ip])
    # The fan-out has run by the time the PUT answers.
    assert _dhcp_line(reservation) in (chain.control.read(DHCP_HOSTS) or "")


def test_delete_drops_resolution_on_both_dns_replicas(chain: Stack) -> None:
    reservation = _put(chain, "intgdel", "02:00:00:00:c0:02")
    for dns_port, _ in CHAIN_DNS.values():
        dnsquery.wait_a("intgdel.home", chain.ip, dns_port, [reservation["ipv4"]])

    r = _api(chain, "DELETE", "/reservations/intgdel")

    assert r.status_code == 204
    for dns_port, _ in CHAIN_DNS.values():
        dnsquery.wait_unknown("intgdel.home", chain.ip, dns_port)
    assert _dhcp_line(reservation) not in (chain.control.read(DHCP_HOSTS) or "")
