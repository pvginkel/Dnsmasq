"""The Kubernetes Service watch, against an injected fake event source."""

import threading
from collections.abc import Callable, Iterator, Mapping
from types import SimpleNamespace
from typing import Any

from config_generator.service_watch import ServiceWatcher

Event = Mapping[str, Any]


# ---- Fake event source + service factories ----------------------------------


class FakeEventSource:
    def __init__(self, events: list[Event]) -> None:
        self._events = list(events)

    def stream(self) -> Iterator[Event]:
        return iter(self._events)


def _service(
    uid: str,
    name: str,
    namespace: str,
    ips: list[str] | None = None,
    annotations: dict[str, str] | None = None,
) -> SimpleNamespace:
    ingress = [SimpleNamespace(ip=ip) for ip in (ips or [])]
    return SimpleNamespace(
        metadata=SimpleNamespace(
            uid=uid,
            name=name,
            namespace=namespace,
            annotations=annotations,
        ),
        status=SimpleNamespace(
            load_balancer=SimpleNamespace(ingress=ingress),
        ),
    )


def _added(svc: SimpleNamespace) -> Event:
    return {"type": "ADDED", "object": svc}


def _deleted(svc: SimpleNamespace) -> Event:
    return {"type": "DELETED", "object": svc}


def _modified(svc: SimpleNamespace) -> Event:
    return {"type": "MODIFIED", "object": svc}


def _run_watcher(
    events: list[Event],
    *,
    nginx_name: str = "nginx",
    nginx_ns: str = "nginx",
    on_change: Callable[[], None] | None = None,
) -> ServiceWatcher:
    """Run the watcher over a fixed event list and return it once the
    debounced callback has had a chance to fire."""
    watcher = ServiceWatcher(
        event_source=FakeEventSource(events),
        nginx_service_name=nginx_name,
        nginx_service_namespace=nginx_ns,
        on_change=on_change or (lambda: None),
    )
    # watch_once() makes a single pass over the stream and returns; watch_loop()
    # is the never-returning reconnect supervisor.
    watcher.watch_once()
    # The render is debounced behind a 0.1s Timer; wait for any pending one.
    if watcher.timer:
        watcher.timer.join(timeout=5.0)
    return watcher


# ---- Tests -------------------------------------------------------------------


def test_hostname_annotation_yields_record() -> None:
    svc = _service(
        uid="u1",
        name="myapp",
        namespace="apps",
        ips=["1.2.3.4"],
        annotations={"dns.webathome.org/hostname": "myapp.home"},
    )
    watcher = _run_watcher([_added(svc)])
    assert watcher.records() == [{"address": "1.2.3.4", "fqdn": "myapp.home"}]


def test_server_name_with_is_public_false_uses_nginx_ip() -> None:
    nginx = _service(uid="ng", name="nginx", namespace="nginx", ips=["10.0.0.1"])
    svc = _service(
        uid="u1",
        name="registry",
        namespace="apps",
        ips=[],
        annotations={
            "nginx.webathome.org/server-name": "registry.home, registry",
            "nginx.webathome.org/is-public": "false",
        },
    )
    # Nginx event first so the IP is known when the longer-server-name service
    # is rendered.
    watcher = _run_watcher([_added(nginx), _added(svc)])
    assert watcher.records() == [{"address": "10.0.0.1", "fqdn": "registry.home"}]


def test_server_name_picks_longest() -> None:
    nginx = _service(uid="ng", name="nginx", namespace="nginx", ips=["10.0.0.1"])
    svc = _service(
        uid="u1",
        name="grafana",
        namespace="apps",
        annotations={
            "nginx.webathome.org/server-name": "g, grafana, grafana.home",
            "nginx.webathome.org/is-public": "no",  # falsy
        },
    )
    watcher = _run_watcher([_added(nginx), _added(svc)])
    assert watcher.records() == [{"address": "10.0.0.1", "fqdn": "grafana.home"}]


def test_delete_event_removes_entry() -> None:
    svc = _service(
        uid="u1",
        name="myapp",
        namespace="apps",
        ips=["1.2.3.4"],
        annotations={"dns.webathome.org/hostname": "myapp.home"},
    )
    watcher = _run_watcher([_added(svc), _deleted(svc)])
    assert watcher.records() == []


def test_hostname_annotation_removed_retracts_record() -> None:
    """Dropping dns.webathome.org/hostname must retract the record, not leave it
    until the watch reconnects — that is how a name moves between services."""
    svc = _service(
        uid="u1",
        name="dhcp",
        namespace="dnsmasq",
        ips=["203.0.113.10"],
        annotations={"dns.webathome.org/hostname": "dhcp.home"},
    )
    stripped = _service(
        uid="u1",
        name="dhcp",
        namespace="dnsmasq",
        ips=["203.0.113.10"],
        annotations={"metallb.io/loadBalancerIPs": "203.0.113.10"},
    )
    watcher = _run_watcher([_added(svc), _modified(stripped)])
    assert watcher.records() == []


def test_server_name_flipped_to_public_retracts_record() -> None:
    """A public server name is served by nginx's own config, not this DNS view,
    so flipping is-public on must retract the record too."""
    nginx = _service(uid="ng", name="nginx", namespace="nginx", ips=["10.0.0.1"])
    internal = _service(
        uid="u1",
        name="app",
        namespace="apps",
        annotations={
            "nginx.webathome.org/server-name": "app.home, app",
            "nginx.webathome.org/is-public": "false",
        },
    )
    public = _service(
        uid="u1",
        name="app",
        namespace="apps",
        annotations={
            "nginx.webathome.org/server-name": "app.example.org",
            "nginx.webathome.org/is-public": "yes",
        },
    )
    watcher = _run_watcher([_added(nginx), _added(internal), _modified(public)])
    assert watcher.records() == []


def test_name_moving_between_services_leaves_one_record() -> None:
    """The ANS-131 case end to end: dhcp.home moves off the dhcp Service onto
    dhcp-app's nginx server-name. Exactly one address must answer."""
    nginx = _service(uid="ng", name="nginx", namespace="nginx", ips=["203.0.113.7"])
    dhcp = _service(
        uid="u-dhcp",
        name="dhcp",
        namespace="dnsmasq",
        ips=["203.0.113.10"],
        annotations={"dns.webathome.org/hostname": "dhcp.home"},
    )
    dhcp_stripped = _service(
        uid="u-dhcp",
        name="dhcp",
        namespace="dnsmasq",
        ips=["203.0.113.10"],
        annotations=None,
    )
    dhcp_app = _service(
        uid="u-dhcp-app",
        name="dhcp-app",
        namespace="dnsmasq",
        annotations={
            "nginx.webathome.org/server-name": "dhcp.home, dhcp",
            "nginx.webathome.org/is-public": "false",
        },
    )
    watcher = _run_watcher(
        [_added(nginx), _added(dhcp), _added(dhcp_app), _modified(dhcp_stripped)]
    )
    assert watcher.records() == [{"address": "203.0.113.7", "fqdn": "dhcp.home"}]


def test_multiple_ips_emit_one_record_per_ip() -> None:
    svc = _service(
        uid="u1",
        name="multi",
        namespace="apps",
        ips=["1.1.1.1", "2.2.2.2"],
        annotations={"dns.webathome.org/hostname": "multi.home"},
    )
    watcher = _run_watcher([_added(svc)])
    assert watcher.records() == [
        {"address": "1.1.1.1", "fqdn": "multi.home"},
        {"address": "2.2.2.2", "fqdn": "multi.home"},
    ]


def test_two_services_same_hostname_both_emit() -> None:
    svc_a = _service(
        uid="ua",
        name="a",
        namespace="apps",
        ips=["10.0.0.1"],
        annotations={"dns.webathome.org/hostname": "shared.home"},
    )
    svc_b = _service(
        uid="ub",
        name="b",
        namespace="apps",
        ips=["10.0.0.2"],
        annotations={"dns.webathome.org/hostname": "shared.home"},
    )
    watcher = _run_watcher([_added(svc_a), _added(svc_b)])
    records = watcher.records()
    assert {"address": "10.0.0.1", "fqdn": "shared.home"} in records
    assert {"address": "10.0.0.2", "fqdn": "shared.home"} in records


def test_server_name_entry_resolves_once_nginx_ingress_appears() -> None:
    """A server-name service seen before nginx is rendered later, after nginx
    reports its load-balancer IP."""
    svc = _service(
        uid="u1",
        name="grafana",
        namespace="apps",
        annotations={
            "nginx.webathome.org/server-name": "grafana.home",
            "nginx.webathome.org/is-public": "false",
        },
    )
    nginx = _service(uid="ng", name="nginx", namespace="nginx", ips=["10.0.0.42"])
    watcher = _run_watcher([_added(svc), _added(nginx)])
    assert watcher.records() == [{"address": "10.0.0.42", "fqdn": "grafana.home"}]


def test_on_change_callback_fires_after_an_event() -> None:
    fired = threading.Event()
    svc = _service(
        uid="u1",
        name="myapp",
        namespace="apps",
        ips=["1.2.3.4"],
        annotations={"dns.webathome.org/hostname": "myapp.home"},
    )
    _run_watcher([_added(svc)], on_change=fired.set)
    assert fired.wait(timeout=5.0), "watcher should fire on_change after an event"
