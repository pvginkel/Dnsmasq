"""The Kubernetes Service watch.

Watches Services across all namespaces and turns their DNS annotations into
addn-hosts records. The watcher writes no files and signals nothing: it keeps
the current set of service-derived records and fires `on_change`, so `serve`
renders the dynamic config (Services and reservations) in one place.

The event source is injected (`kube_source.KubernetesEventSource` is the real
one), which keeps this module free of the `kubernetes` package.
"""

import logging
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from threading import Timer
from typing import Any, NoReturn, Protocol

from .rendering import HostRecord

logger = logging.getLogger(__name__)

# A burst of watch events collapses into a single render.
_DEBOUNCE_SECONDS = 0.1


class EventSource(Protocol):
    def stream(self) -> Iterable[Mapping[str, Any]]:
        """Watch events: `type` (ADDED / MODIFIED / DELETED) and `object`, the
        Service."""
        ...


@dataclass
class _Entry:
    hostnames: list[str]
    # None: the name is served behind nginx, at nginx's load-balancer IPs.
    ip_addresses: list[str] | None


class ServiceWatcher:
    """Maintains the set of DNS records derived from Kubernetes Services.

    Two annotation flavours are recognised:

    - `dns.webathome.org/hostname` — the service's own load-balancer IPs are
      published under the given hostname(s).
    - `nginx.webathome.org/server-name` (with `is-public` false) — a non-public
      server published behind the nginx load balancer; the nginx service's IPs
      are used.
    """

    HOSTNAME_KEY = "dns.webathome.org/hostname"
    SERVER_NAME_KEY = "nginx.webathome.org/server-name"
    IS_PUBLIC_KEY = "nginx.webathome.org/is-public"

    def __init__(
        self,
        event_source: EventSource,
        nginx_service_name: str,
        nginx_service_namespace: str,
        on_change: Callable[[], None],
    ) -> None:
        self._event_source = event_source
        self._nginx_service_name = nginx_service_name
        self._nginx_service_namespace = nginx_service_namespace
        self._on_change = on_change
        self._entries: dict[str, _Entry] = {}
        self._nginx_ip_addresses: list[str] | None = None
        # The pending debounced `on_change`, if any.
        self.timer: Timer | None = None

    def records(self) -> list[HostRecord]:
        """The current service-derived DNS records.

        Entries whose IP addresses are not yet known (a server-name entry seen
        before the nginx load balancer reported its ingress) are skipped; they
        appear once the nginx IPs become known.
        """
        out: list[HostRecord] = []
        for entry in self._entries.values():
            ip_addresses = entry.ip_addresses
            if ip_addresses is None:
                ip_addresses = self._nginx_ip_addresses
            if not ip_addresses:
                continue
            for hostname in entry.hostnames:
                for ip_address in ip_addresses:
                    out.append({"address": ip_address, "fqdn": hostname})
        return out

    def watch_loop(self) -> NoReturn:
        while True:
            if self.timer:
                self.timer.cancel()
            self._entries.clear()

            try:
                self.watch_once()
                # A clean stream end is routine: the API server closes the
                # watch connection periodically. Reconnect.
                logger.info("Watch stream closed; reconnecting")

            except Exception as e:
                logger.error("Exception in watch loop: %s", e)

            time.sleep(5)

    def watch_once(self) -> None:
        """Consume the event stream until it ends."""
        for event in self._event_source.stream():
            self._handle(event["type"], event["object"])

    def _handle(self, event_type: str, service: Any) -> None:
        meta = service.metadata
        ref = f"{meta.namespace}/{meta.name}"

        if event_type == "DELETED":
            if meta.uid in self._entries:
                logger.info("Removing entry for service %s", ref)
                del self._entries[meta.uid]
            return

        if (
            meta.name == self._nginx_service_name
            and meta.namespace == self._nginx_service_namespace
        ):
            if not service.status.load_balancer.ingress:
                logger.info("Load balancer ingress is not available for %s", ref)
                return
            self._nginx_ip_addresses = [
                p.ip for p in service.status.load_balancer.ingress
            ]
            return

        hostnames = self._parse_hostnames(service, self.HOSTNAME_KEY)
        published = False

        if hostnames:
            if not service.status.load_balancer.ingress:
                logger.info("Load balancer ingress is not available for %s", ref)
                return

            ip_addresses = [p.ip for p in service.status.load_balancer.ingress]
            logger.info(
                "Updating entry for service %s with hostnames %s IP addresses %s",
                ref,
                hostnames,
                ip_addresses,
            )
            self._entries[meta.uid] = _Entry(hostnames, ip_addresses)
            published = True

        server_names = self._parse_hostnames(service, self.SERVER_NAME_KEY)
        is_public = self._parse_bool(service, self.IS_PUBLIC_KEY, False)

        if server_names and not is_public:
            # Only the longest server name is registered: a non-public server's
            # server_name reads like "registry.home, registry", and
            # "registry.home" is the one wanted.
            server_name = sorted(server_names, key=lambda v: -len(v))[0]
            logger.info(
                "Updating entry for service %s with server name %s NGINX IP addresses",
                ref,
                server_name,
            )
            self._entries[meta.uid] = _Entry([server_name], None)
            published = True

        # A MODIFIED event that no longer publishes a name retracts the record:
        # dropping the annotation (or flipping is-public to true) is how a name
        # moves between services, and a stale entry would resolve the name to
        # both until the watch reconnected (ANS-131).
        if not published and meta.uid in self._entries:
            logger.info(
                "Removing entry for service %s which no longer publishes a name", ref
            )
            del self._entries[meta.uid]

        if self.timer:
            self.timer.cancel()
        self.timer = Timer(_DEBOUNCE_SECONDS, self._fire)
        self.timer.start()

    def _fire(self) -> None:
        try:
            self._on_change()
        except Exception as e:
            logger.error("Exception applying service watch change: %s", e)

    def _parse_hostnames(self, service: Any, key: str) -> list[str] | None:
        annotations = service.metadata.annotations
        if not annotations or key not in annotations:
            return None
        return [n.strip() for n in annotations[key].split(",")]

    def _parse_bool(self, service: Any, key: str, default: bool) -> bool:
        annotations = service.metadata.annotations
        if not annotations or key not in annotations:
            return default
        return annotations[key] in ("yes", "true")
