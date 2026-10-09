"""A-record queries against a stack's dnsmasq, at its pod IP."""

import dns.exception
import dns.message
import dns.query
import dns.rdatatype

from .waiting import wait_for


def query_a(name: str, host: str, port: int) -> list[str]:
    """The A records dnsmasq answers for `name`; empty for a name it does not know.

    The stacks' dnsmasq has no upstream server, so it refuses names it does not
    hold rather than forwarding them.
    """
    query = dns.message.make_query(name, dns.rdatatype.A)
    response = dns.query.udp(query, host, port=port, timeout=2.0)
    return sorted(
        rr.address
        for rrset in response.answer
        for rr in rrset
        if rr.rdtype == dns.rdatatype.A
    )


def wait_up(host: str, port: int, timeout: float = 30.0) -> None:
    """Until dnsmasq answers at all; a refusal counts."""

    def answers() -> bool | None:
        try:
            query_a("probe.invalid.", host, port)
        except dns.exception.DNSException:
            return None
        return True

    wait_for(answers, timeout, f"dnsmasq at {host}:{port} to answer")


def wait_a(
    name: str, host: str, port: int, expected: list[str], timeout: float = 15.0
) -> None:
    def resolved() -> bool | None:
        try:
            return query_a(name, host, port) == sorted(expected) or None
        except dns.exception.DNSException:
            return None

    wait_for(resolved, timeout, f"{name} to resolve to {expected} at {host}:{port}")


def wait_unknown(name: str, host: str, port: int, timeout: float = 15.0) -> None:
    def gone() -> bool | None:
        try:
            return not query_a(name, host, port) or None
        except dns.exception.DNSException:
            return None

    wait_for(gone, timeout, f"{name} to stop resolving at {host}:{port}")
