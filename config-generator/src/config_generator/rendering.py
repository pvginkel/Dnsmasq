"""Rendering records through the templates into the files dnsmasq reads."""

import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TypedDict

import yaml
from jinja2 import Template

_TEMPLATE_DIR = Path(__file__).parent / "templates"

DNS_TEMPLATE = "dns.j2"
DHCP_TEMPLATE = "dhcp.j2"
CNAME_TEMPLATE = "cname.j2"


class ConfigError(Exception):
    """Fatal misconfiguration; the process exits non-zero."""


class RenderError(Exception):
    """A render attempt failed; `serve` keeps running, the caller gets a 500."""


class HostRecord(TypedDict):
    """An addn-hosts line."""

    address: str | None
    fqdn: str


class DhcpRecord(TypedDict):
    """A dhcp-hostsfile line."""

    tag: str | None
    mac: str
    address: str | None
    fqdn: str


class CnameRecord(TypedDict):
    """A `cname=` directive."""

    alias: str
    target: str


def render(template: str, records: Sequence[Mapping[str, object]]) -> str:
    compiled: Template = Template((_TEMPLATE_DIR / template).read_text())
    return compiled.render(records=records)


def load_yaml(path: str) -> Any:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def atomic_write(path: str, content: str) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        f.write(content)
    os.replace(tmp, path)


def read_target(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return f.read()
