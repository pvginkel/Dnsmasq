"""`serve` beside a dnsmasq: renders the dynamic config, writes what changed,
and SIGHUPs dnsmasq.

The dnsmasq here is a stand-in that counts SIGHUPs; that the real one reloads
and answers for what was rendered is the integration tier's to show.
"""

import os
import threading
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from flask.testing import FlaskClient
from standin import DnsmasqStandIn

from config_generator.app import create_app
from config_generator.dnsmasq import Dnsmasq
from config_generator.dynamic import DynamicConfig
from config_generator.rendering import ConfigError
from config_generator.service_watch import ServiceWatcher
from config_generator.settings import Settings
from config_generator.static import generate


@dataclass
class DnsServe:
    """`serve` in a DNS pod's role, started."""

    settings: Settings
    config: DynamicConfig
    client: FlaskClient
    dynamic: Path
    target: Path
    dnsmasq: DnsmasqStandIn

    def write_reservations(self, hosts: list[dict[str, str]]) -> None:
        self.dynamic.write_text(yaml.safe_dump({"hosts": hosts}))

    def refresh(self) -> dict[str, Any]:
        r = self.client.post("/refresh")
        assert r.status_code == 200, r.text
        body: dict[str, Any] = r.get_json()
        return body


def _dns_settings(tmp_path: Path, pid_file: Path) -> Settings:
    (tmp_path / "dynamic").mkdir(exist_ok=True)
    (tmp_path / "conf-generated").mkdir(exist_ok=True)
    return Settings(
        dynamic_hosts=str(tmp_path / "dynamic" / "dynamic-hosts.yaml"),
        dns_config_target=str(tmp_path / "conf-generated" / "hosts"),
        domain="home",
        dnsmasq_pid_file=str(pid_file),
    )


def _start(settings: Settings) -> DynamicConfig:
    assert settings.dnsmasq_pid_file is not None
    config = DynamicConfig(
        settings, Dnsmasq(settings.dnsmasq_pid_file, recheck_timeout=0.5)
    )
    config.startup()
    return config


@pytest.fixture
def dns(tmp_path: Path, dnsmasq_standin: DnsmasqStandIn) -> DnsServe:
    settings = _dns_settings(tmp_path, dnsmasq_standin.pid_file)
    config = _start(settings)
    # The first render wrote the (empty) hosts file and signalled.
    dnsmasq_standin.wait_hups(1)
    assert settings.dynamic_hosts and settings.dns_config_target
    return DnsServe(
        settings=settings,
        config=config,
        client=create_app(config).test_client(),
        dynamic=Path(settings.dynamic_hosts),
        target=Path(settings.dns_config_target),
        dnsmasq=dnsmasq_standin,
    )


# ---- /healthz and /ready ----------------------------------------------------


def test_healthz_returns_ok(dns: DnsServe) -> None:
    r = dns.client.get("/healthz")
    assert r.status_code == 200
    assert r.get_json() == {"status": "ok"}


def test_ready_only_once_startup_has_run(
    tmp_path: Path, dnsmasq_standin: DnsmasqStandIn
) -> None:
    settings = _dns_settings(tmp_path, dnsmasq_standin.pid_file)
    assert settings.dnsmasq_pid_file
    config = DynamicConfig(settings, Dnsmasq(settings.dnsmasq_pid_file))
    client = create_app(config).test_client()

    assert client.get("/ready").status_code == 503
    config.startup()
    assert client.get("/ready").get_json() == {"status": "ok"}


# ---- /refresh ---------------------------------------------------------------


def test_refresh_noop_returns_changed_false(dns: DnsServe) -> None:
    mtime = os.path.getmtime(dns.target)

    assert dns.refresh() == {"status": "ok", "changed": False}

    assert os.path.getmtime(dns.target) == mtime
    dns.dnsmasq.assert_hups_settle_at(1)


def test_refresh_with_new_reservation_renders_and_signals(dns: DnsServe) -> None:
    dns.write_reservations([{"name": "leased", "address": "10.99.1.42"}])

    assert dns.refresh() == {"status": "ok", "changed": True}

    assert "10.99.1.42 leased.home" in dns.target.read_text()
    dns.dnsmasq.wait_hups(2)


def test_static_and_dynamic_render_into_separate_files(
    dns: DnsServe, static_hosts: Path, tmp_path: Path
) -> None:
    """There is no merge or override between the static and the dynamic set:
    `serve` never touches the static file, and the dynamic file carries only
    the dynamic entry, even for a name the static set also holds."""
    static_target = tmp_path / "static-hosts"
    generate(
        Settings(static_hosts=str(static_hosts), dns_config_target=str(static_target))
    )
    static_before = static_target.read_text()

    # `alpha` is a static host at 10.99.0.1.
    dns.write_reservations([{"name": "alpha", "address": "10.99.9.99"}])
    assert dns.refresh()["changed"] is True

    dynamic = dns.target.read_text()
    assert "10.99.9.99 alpha.home" in dynamic
    assert "10.99.0.1" not in dynamic
    assert static_target.read_text() == static_before


def test_concurrent_refresh_calls_serialise(dns: DnsServe) -> None:
    """Ten parallel refreshes after one content change: the render lock lets
    exactly one of them write and signal; all succeed."""
    dns.write_reservations([{"name": "concurrent", "address": "10.99.1.50"}])
    app = dns.client.application
    results: list[Any] = []

    def hit() -> None:
        r = app.test_client().post("/refresh")
        results.append((r.status_code, r.get_json()))

    threads = [threading.Thread(target=hit) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30.0)

    assert [status for status, _ in results] == [200] * 10
    assert sum(body["changed"] for _, body in results) == 1
    assert "10.99.1.50 concurrent.home" in dns.target.read_text()
    dns.dnsmasq.wait_hups(2)
    dns.dnsmasq.assert_hups_settle_at(2)


def test_refresh_with_malformed_dynamic_yaml_returns_render_failed(
    dns: DnsServe,
) -> None:
    before = dns.target.read_text()
    dns.dynamic.write_text(":\n  this is: not - valid: yaml")

    r = dns.client.post("/refresh")

    assert r.status_code == 500
    body = r.get_json()
    assert body["error"] == "render_failed"
    assert "DYNAMIC_HOSTS" in body["message"]
    assert dns.target.read_text() == before
    dns.dnsmasq.assert_hups_settle_at(1)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("- just\n- a list\n", "must be a mapping"),
        ("hosts: not-a-list\n", "'hosts' must be a list"),
        ("hosts:\n  - address: 10.99.1.1\n", "missing 'name'"),
    ],
)
def test_refresh_rejects_malformed_reservations(
    dns: DnsServe, content: str, message: str
) -> None:
    dns.dynamic.write_text(content)

    r = dns.client.post("/refresh")

    assert r.status_code == 500
    assert message in r.get_json()["message"]


def test_refresh_while_dnsmasq_is_down_writes_the_file(
    dns: DnsServe, caplog: pytest.LogCaptureFixture
) -> None:
    """dnsmasq gone and its pid file stale: the file is still written, the
    SIGHUP gives up after the bounded re-poll, and the refresh succeeds — a
    restarted dnsmasq reads the file at startup."""
    dns.dnsmasq.stop()
    dns.write_reservations([{"name": "down", "address": "10.99.1.99"}])

    assert dns.refresh() == {"status": "ok", "changed": True}

    assert "10.99.1.99 down.home" in dns.target.read_text()
    assert "no fresh pid appeared" in caplog.text


def test_restart_over_matching_files_sends_no_sighup(
    dns: DnsServe, caplog: pytest.LogCaptureFixture
) -> None:
    """A restarted `serve` finds the rendered file already on disk: the startup
    byte-compare is a no-op and dnsmasq is not signalled."""
    dns.write_reservations([{"name": "kept", "address": "10.99.3.1"}])
    dns.refresh()
    dns.dnsmasq.wait_hups(2)

    _start(dns.settings)

    assert "configuration already up to date" in caplog.text
    dns.dnsmasq.assert_hups_settle_at(2)


def test_missing_dynamic_hosts_logged_once(
    dns: DnsServe, caplog: pytest.LogCaptureFixture
) -> None:
    """A missing DYNAMIC_HOSTS is empty, INFO-logged at the present-to-missing
    transition; later missing reads stay quiet."""
    dns.write_reservations([{"name": "transient", "address": "10.99.2.1"}])
    dns.refresh()

    caplog.clear()
    dns.dynamic.unlink()
    assert dns.refresh()["changed"] is True
    assert "does not exist" in caplog.text

    caplog.clear()
    dns.refresh()
    assert "does not exist" not in caplog.text


# ---- The Service watch ------------------------------------------------------


class _OneServiceSource:
    def stream(self) -> Iterator[Mapping[str, Any]]:
        service = SimpleNamespace(
            metadata=SimpleNamespace(
                uid="u1",
                name="myapp",
                namespace="apps",
                annotations={"dns.webathome.org/hostname": "myapp.home"},
            ),
            status=SimpleNamespace(
                load_balancer=SimpleNamespace(
                    ingress=[SimpleNamespace(ip="203.0.113.7")]
                )
            ),
        )
        return iter([{"type": "ADDED", "object": service}])


def test_service_records_render_beside_reservations(dns: DnsServe) -> None:
    dns.write_reservations([{"name": "leased", "address": "10.99.1.42"}])
    dns.refresh()
    watcher = ServiceWatcher(
        event_source=_OneServiceSource(),
        nginx_service_name="nginx",
        nginx_service_namespace="nginx",
        on_change=dns.config.on_service_change,
    )
    dns.config.watch_services(watcher)

    watcher.watch_once()
    assert watcher.timer is not None
    watcher.timer.join(timeout=5.0)

    rendered = dns.target.read_text()
    assert "203.0.113.7 myapp.home" in rendered
    assert "10.99.1.42 leased.home" in rendered
    dns.dnsmasq.wait_hups(3)


# ---- Startup validation -----------------------------------------------------


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        (Settings(domain="home"), "DNS_CONFIG_TARGET / DHCP_CONFIG_TARGET"),
        (Settings(dns_config_target="/unused/hosts"), "DOMAIN"),
        (
            Settings(dhcp_config_target="/unused/dhcp-hosts", domain="home"),
            "DHCP_TAG",
        ),
    ],
)
def test_startup_rejects_incomplete_settings(settings: Settings, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        DynamicConfig(settings, None).startup()
