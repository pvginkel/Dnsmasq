"""`serve`'s dynamic config: Kubernetes Service records and reservations."""

import logging
import os
import threading
from typing import Any

import yaml

from .dnsmasq import Dnsmasq
from .rendering import (
    DHCP_TEMPLATE,
    DNS_TEMPLATE,
    ConfigError,
    DhcpRecord,
    HostRecord,
    RenderError,
    atomic_write,
    load_yaml,
    read_target,
    render,
)
from .service_watch import ServiceWatcher
from .settings import Settings

logger = logging.getLogger(__name__)


class DynamicConfig:
    """Renders the dynamic config, writes what changed, and reloads dnsmasq.

    Every render runs under one lock: a /refresh and a Service change never
    interleave their writes.
    """

    def __init__(self, settings: Settings, dnsmasq: Dnsmasq | None) -> None:
        self._settings = settings
        self._dnsmasq = dnsmasq
        self._lock = threading.Lock()
        self._missing_logged = False
        self._watcher: ServiceWatcher | None = None
        self.ready = False

    def validate(self) -> None:
        s = self._settings
        if not (s.dns_config_target or s.dhcp_config_target):
            raise ConfigError(
                "at least one of DNS_CONFIG_TARGET / DHCP_CONFIG_TARGET must be set"
            )
        if not s.domain:
            raise ConfigError("DOMAIN environment variable is not set")
        if s.dhcp_config_target and not s.dhcp_tag:
            raise ConfigError("DHCP_TAG must be set when DHCP_CONFIG_TARGET is set")

    def startup(self) -> None:
        """Validate, apply the first render, and wait for dnsmasq to be up.

        `serve` binds its HTTP listener only after this returns, so a /refresh
        always finds a dnsmasq pid to signal, and DHCPApp's startup probe on
        /ready passes only once the first render is on disk.
        """
        self.validate()
        with self._lock:
            if self._render_and_apply():
                logger.info("Startup applied configuration changes")
            else:
                logger.info("Startup: configuration already up to date")
        if self._dnsmasq:
            self._dnsmasq.wait_for_pid()
        else:
            logger.info("DNSMASQ_PID_FILE is empty; rendering without signalling")
        self.ready = True

    def watch_services(self, watcher: ServiceWatcher) -> None:
        """Render the watcher's Service records into the DNS config from now on."""
        self._watcher = watcher

    def refresh(self) -> bool:
        """Render and apply. True if a file was written. Raises RenderError."""
        with self._lock:
            return self._render_and_apply()

    def on_service_change(self) -> None:
        """The Service watch's callback."""
        try:
            changed = self.refresh()
        except RenderError as e:
            logger.error("service-watch render failed: %s", e)
            return
        if changed:
            logger.info("Applied configuration change from service watch")

    def _render_and_apply(self) -> bool:
        dns_text, dhcp_text = self._render()
        return self._apply(dns_text, dhcp_text)

    def _load_reservations(self) -> list[dict[str, Any]]:
        """Reservation entries from DYNAMIC_HOSTS. A missing file is empty."""
        path = self._settings.dynamic_hosts
        if not path:
            return []
        if not os.path.exists(path):
            if not self._missing_logged:
                logger.info(
                    "DYNAMIC_HOSTS (%s) does not exist; treating as empty", path
                )
                self._missing_logged = True
            return []
        self._missing_logged = False
        try:
            data = load_yaml(path)
        except (OSError, yaml.YAMLError) as e:
            raise RenderError(f"failed to load DYNAMIC_HOSTS ({path}): {e}") from e
        if not isinstance(data, dict):
            raise RenderError(f"DYNAMIC_HOSTS ({path}) must be a mapping")
        hosts = data.get("hosts")
        if hosts is None:
            return []
        if not isinstance(hosts, list):
            raise RenderError(f"DYNAMIC_HOSTS ({path}) 'hosts' must be a list")
        return hosts

    def _reservation_fqdn(self, host: dict[str, Any]) -> str:
        name = host.get("name")
        if not name:
            raise RenderError(f"dynamic host entry is missing 'name': {host!r}")
        return f"{name}.{self._settings.domain}"

    def _render(self) -> tuple[str | None, str | None]:
        """DNS combines Service records with reservation A records; DHCP covers
        reservations that carry a MAC."""
        reservations = self._load_reservations()

        dns_text = None
        if self._settings.dns_config_target:
            records: list[HostRecord] = self._watcher.records() if self._watcher else []
            for host in reservations:
                records.append(
                    {
                        "address": host.get("address"),
                        "fqdn": self._reservation_fqdn(host),
                    }
                )
            dns_text = render(DNS_TEMPLATE, records)

        dhcp_text = None
        if self._settings.dhcp_config_target:
            dhcp_records: list[DhcpRecord] = []
            for host in reservations:
                if host.get("mac"):
                    dhcp_records.append(
                        {
                            "tag": self._settings.dhcp_tag,
                            "mac": host["mac"],
                            "address": host.get("address"),
                            "fqdn": self._reservation_fqdn(host),
                        }
                    )
            dhcp_text = render(DHCP_TEMPLATE, dhcp_records)

        return dns_text, dhcp_text

    def _apply(self, dns_text: str | None, dhcp_text: str | None) -> bool:
        """Write the targets whose content changed, then SIGHUP dnsmasq. True if
        a file was written."""
        changed = False
        for target, text in (
            (self._settings.dns_config_target, dns_text),
            (self._settings.dhcp_config_target, dhcp_text),
        ):
            if target and text is not None and read_target(target) != text:
                atomic_write(target, text)
                logger.info("Wrote %s", target)
                changed = True
        if changed and self._dnsmasq:
            self._dnsmasq.reload()
        return changed
