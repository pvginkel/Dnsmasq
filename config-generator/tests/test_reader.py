"""`serve` with no dnsmasq beside it — the role DHCPApp's pod runs it in.

DHCPApp renders the same dynamic dhcp-hostsfile the DHCP instance renders, but
only to report which leases are reservations; its pod holds no dnsmasq. With
DNSMASQ_PID_FILE empty, `serve` writes the file and signals nothing. Were it to
wait for a pid file, startup and every /refresh would block forever.
"""

import time
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml
from flask.testing import FlaskClient

from config_generator.app import create_app
from config_generator.dynamic import DynamicConfig
from config_generator.settings import Settings


@dataclass
class Reader:
    client: FlaskClient
    dynamic: Path
    target: Path

    def write_reservations(self, hosts: list[dict[str, str]]) -> None:
        self.dynamic.write_text(yaml.safe_dump({"hosts": hosts}))


@pytest.fixture
def reader(tmp_path: Path) -> Reader:
    settings = Settings.from_env(
        {
            "DYNAMIC_HOSTS": str(tmp_path / "dynamic-hosts.yaml"),
            "DHCP_CONFIG_TARGET": str(tmp_path / "dhcp-hosts"),
            "DOMAIN": "home",
            "DHCP_TAG": "intranet",
            "DNSMASQ_PID_FILE": "",
        }
    )
    assert settings.dnsmasq_pid_file is None
    config = DynamicConfig(settings, None)
    config.startup()
    return Reader(
        client=create_app(config).test_client(),
        dynamic=tmp_path / "dynamic-hosts.yaml",
        target=tmp_path / "dhcp-hosts",
    )


def _refresh(reader: Reader) -> tuple[bool, float]:
    t0 = time.monotonic()
    r = reader.client.post("/refresh")
    elapsed = time.monotonic() - t0
    assert r.status_code == 200, r.text
    changed: bool = r.get_json()["changed"]
    return changed, elapsed


def test_startup_completes_without_dnsmasq(reader: Reader) -> None:
    """Ready, and the target already rendered — nothing to signal."""
    assert reader.client.get("/ready").status_code == 200
    assert reader.target.exists()


def test_refresh_renders_reservations_and_returns(reader: Reader) -> None:
    reader.write_reservations(
        [{"name": "host-a", "address": "192.0.2.5", "mac": "02:00:00:00:00:01"}]
    )

    changed, elapsed = _refresh(reader)

    assert changed is True
    assert elapsed < 5.0, f"/refresh took {elapsed:.2f}s"
    assert "set:intranet,id:*,02:00:00:00:00:01,192.0.2.5,host-a.home" in (
        reader.target.read_text()
    )


def test_refresh_is_idempotent(reader: Reader) -> None:
    """Unchanged input writes nothing, and still answers without a dnsmasq."""
    reader.write_reservations(
        [{"name": "host-a", "address": "192.0.2.5", "mac": "02:00:00:00:00:01"}]
    )
    _refresh(reader)

    changed, elapsed = _refresh(reader)

    assert changed is False
    assert elapsed < 5.0, f"/refresh took {elapsed:.2f}s"


def test_dhcp_render_skips_reservations_without_a_mac(reader: Reader) -> None:
    reader.write_reservations(
        [
            {"name": "host-a", "address": "192.0.2.5", "mac": "02:00:00:00:00:01"},
            {"name": "host-b", "address": "192.0.2.6"},
        ]
    )
    _refresh(reader)

    assert "host-b" not in reader.target.read_text()
