"""`generate`: render the static config once, before dnsmasq boots."""

import logging
from typing import Any

import yaml

from .rendering import (
    CNAME_TEMPLATE,
    DHCP_TEMPLATE,
    DNS_TEMPLATE,
    CnameRecord,
    ConfigError,
    DhcpRecord,
    HostRecord,
    atomic_write,
    load_yaml,
    render,
)
from .settings import Settings

logger = logging.getLogger(__name__)


def _load_static(path: str | None) -> dict[str, Any]:
    if not path:
        raise ConfigError("STATIC_HOSTS environment variable is not set")
    try:
        data = load_yaml(path)
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"failed to load STATIC_HOSTS ({path}): {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"STATIC_HOSTS ({path}) must be a mapping")
    if not data.get("tag"):
        raise ConfigError(f"STATIC_HOSTS ({path}) is missing 'tag'")
    if not data.get("domain"):
        raise ConfigError(f"STATIC_HOSTS ({path}) is missing 'domain'")
    if data.get("hosts") is None:
        data["hosts"] = []
    return data


def classify_static(
    hosts: list[dict[str, Any]], domain: str, tag: str
) -> tuple[list[HostRecord], list[DhcpRecord], list[CnameRecord]]:
    """Split static hosts into addn-hosts / dhcp-hostsfile / cname records.

    A bare entry is an A record (plus a DHCP entry when it carries a `mac`).
    An entry with `type: cname` is a CNAME; dnsmasq resolves its `target` at
    query time against everything it then knows (static hosts, management-API
    reservations, DHCP), so the target need not appear in this file.
    """
    dns_records: list[HostRecord] = []
    dhcp_records: list[DhcpRecord] = []
    cname_records: list[CnameRecord] = []
    for host in hosts:
        name = host.get("name")
        if not name:
            raise ConfigError(f"static host entry is missing 'name': {host!r}")
        host_type = host.get("type", "host")
        if host_type == "cname":
            target = host.get("target")
            if not target:
                raise ConfigError(f"cname {name!r} is missing 'target'")
            cname_records.append(
                {"alias": f"{name}.{domain}", "target": f"{target}.{domain}"}
            )
        elif host_type == "host":
            fqdn = f"{name}.{domain}"
            dns_records.append({"address": host.get("address"), "fqdn": fqdn})
            if host.get("mac"):
                dhcp_records.append(
                    {
                        "tag": tag,
                        "mac": host["mac"],
                        "address": host.get("address"),
                        "fqdn": fqdn,
                    }
                )
        else:
            raise ConfigError(f"host {name!r} has unknown type {host_type!r}")
    return dns_records, dhcp_records, cname_records


def generate(settings: Settings) -> None:
    """Render the static config into the configured target files."""
    if not (
        settings.dns_config_target
        or settings.dhcp_config_target
        or settings.cname_config_target
    ):
        raise ConfigError(
            "at least one of DNS_CONFIG_TARGET / DHCP_CONFIG_TARGET / "
            "CNAME_CONFIG_TARGET must be set"
        )
    static = _load_static(settings.static_hosts)
    dns_records, dhcp_records, cname_records = classify_static(
        static["hosts"], static["domain"], static["tag"]
    )
    if settings.dns_config_target:
        atomic_write(settings.dns_config_target, render(DNS_TEMPLATE, dns_records))
        logger.info(
            "Wrote %s (%d host record(s))",
            settings.dns_config_target,
            len(dns_records),
        )
    if settings.dhcp_config_target:
        atomic_write(settings.dhcp_config_target, render(DHCP_TEMPLATE, dhcp_records))
        logger.info(
            "Wrote %s (%d dhcp record(s))",
            settings.dhcp_config_target,
            len(dhcp_records),
        )
    if settings.cname_config_target:
        atomic_write(
            settings.cname_config_target, render(CNAME_TEMPLATE, cname_records)
        )
        logger.info(
            "Wrote %s (%d cname record(s))",
            settings.cname_config_target,
            len(cname_records),
        )
