"""The dnsmasq image: it starts and reloads on SIGHUP without restarting, it
carries the tools the chart's probes call, and its lease-change script POSTs to
DHCPApp's endpoint (`dnsmasq/files/dhcp-script.sh`)."""

import time

from tier import dnsquery
from tier.cluster import Cluster
from tier.stacks import Stack
from tier.waiting import wait_for

RUN = "/stack/run"


def _instance(cluster: Cluster, stack: Stack) -> tuple[str, int]:
    """Which run of the dnsmasq container this is."""
    status = cluster.container(stack.pod, "dnsmasq")
    return status.container_id, status.restart_count


def test_dnsmasq_starts_and_writes_pidfile(bare_stack: Stack) -> None:
    assert bare_stack.control.dnsmasq_pid(RUN) > 0


def test_addn_hosts_resolves_after_sighup(bare_stack: Stack, cluster: Cluster) -> None:
    """A name written into the addn-hosts target the image bakes in resolves
    after a SIGHUP, and neither the container nor dnsmasq restarts."""
    stack = bare_stack
    stack.control.write("/stack/static/hosts", "10.99.3.1 foo.home\n")
    before = _instance(cluster, stack)
    pid = stack.control.dnsmasq_pid(RUN)

    stack.control.signal(pid, "HUP")

    dnsquery.wait_a("foo.home", stack.ip, 53, ["10.99.3.1"])
    assert _instance(cluster, stack) == before
    assert stack.control.dnsmasq_pid(RUN) == pid


def test_sighup_does_not_restart_dnsmasq(bare_stack: Stack, cluster: Cluster) -> None:
    stack = bare_stack
    before = _instance(cluster, stack)
    pid = stack.control.dnsmasq_pid(RUN)

    stack.control.signal(pid, "HUP")
    time.sleep(0.5)

    assert _instance(cluster, stack) == before
    assert stack.control.dnsmasq_pid(RUN) == pid


def test_image_answers_the_charts_dns_liveness_probe(
    bare_stack: Stack, cluster: Cluster
) -> None:
    """The bare stack runs the chart's DNS liveness command as its readiness
    probe."""
    wait_for(
        lambda: cluster.container(bare_stack.pod, "dnsmasq").ready or None,
        30.0,
        "dnsmasq to pass `nslookup . 127.0.0.1`",
    )


def test_image_passes_the_charts_dhcp_readiness_probe(
    chain_stack: Stack, cluster: Cluster
) -> None:
    wait_for(
        lambda: cluster.container(chain_stack.pod, "dhcp-dnsmasq").ready or None,
        30.0,
        "the DHCP dnsmasq to pass the chart's readiness probe",
    )


def test_lease_change_script_posts_to_the_renewal_endpoint(
    chain_stack: Stack, cluster: Cluster
) -> None:
    """dnsmasq runs its lease-change script for the seeded lease at startup, and
    the script POSTs an empty form to DHCP_RENEWAL_ENDPOINT. dnsmasq logs each
    run that exits non-zero, as curl does when its POST is refused."""
    changes = wait_for(
        lambda: chain_stack.control.lease_changes() or None,
        30.0,
        "a POST to the renewal endpoint",
    )
    assert changes[0] == {
        "content_type": "application/x-www-form-urlencoded",
        "body": "",
    }
    assert "script process exited" not in cluster.log(chain_stack.pod, "dhcp-dnsmasq")
