"""`python main.py`, the command the deploy chart runs, and the environment it
reads."""

import ipaddress
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from fake_refresh import FakeRefresh

from management_api.settings import ConfigError, Settings

MAIN_PY = Path(__file__).parents[1] / "main.py"

VARIABLES = (
    "PORT",
    "RESERVATION_CIDR",
    "DATA",
    "AUTH_TOKEN",
    "DNS0_ENDPOINT",
    "DNS1_ENDPOINT",
    "DHCP_ENDPOINT",
    "DHCP_APP_ENDPOINT",
    "REFRESH_TIMEOUT_SECONDS",
)

CHART_ENV = {
    "RESERVATION_CIDR": "192.0.2.0/24",
    "DATA": "/data",
    "AUTH_TOKEN": "secret",
    "DNS0_ENDPOINT": "http://dns-0.dns-headless:9000/refresh",
}


def _env(**variables: str) -> dict[str, str]:
    inherited = {k: v for k, v in os.environ.items() if k not in VARIABLES}
    return {**inherited, **variables}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@pytest.fixture
def dns0() -> Iterator[FakeRefresh]:
    fake = FakeRefresh()
    yield fake
    fake.stop()


def _wait_healthy(url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        assert process.poll() is None, "the management API exited"
        try:
            with urllib.request.urlopen(f"{url}/healthz", timeout=2) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError):
            pass
        time.sleep(0.1)
    raise TimeoutError(f"{url} did not become healthy")


def test_main_serves_the_api(tmp_path: Path, dns0: FakeRefresh) -> None:
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, str(MAIN_PY)],
        cwd=MAIN_PY.parent,
        env=_env(
            PORT=str(port),
            RESERVATION_CIDR="192.0.2.0/24",
            DATA=str(tmp_path),
            AUTH_TOKEN="secret",
            DNS0_ENDPOINT=dns0.url,
            REFRESH_TIMEOUT_SECONDS="5",
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        url = f"http://127.0.0.1:{port}"
        _wait_healthy(url, process)

        request = urllib.request.Request(
            f"{url}/reservations/host-a",
            method="PUT",
            data=json.dumps({"mac": "02:00:00:00:00:01"}).encode(),
            headers={
                "Authorization": "Bearer secret",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=10) as r:
            assert r.status == 201
            assert json.load(r)["ipv4"] == "192.0.2.1"
        assert len(dns0.hits) == 1
        assert (tmp_path / "reservations" / "state.yaml").exists()
    finally:
        process.terminate()
        output, _ = process.communicate(timeout=10)
    assert f"Loaded 0 reservations from {tmp_path}/reservations/state.yaml" in output
    assert f"Listening on port {port}" in output


def test_main_exits_non_zero_on_missing_configuration(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(MAIN_PY)],
        cwd=MAIN_PY.parent,
        env=_env(RESERVATION_CIDR="192.0.2.0/24", DATA=str(tmp_path)),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 1
    assert (
        "startup configuration error: missing required environment variable(s): "
        "AUTH_TOKEN, DNS0_ENDPOINT"
    ) in result.stdout


def test_main_exits_non_zero_on_unreadable_state(tmp_path: Path) -> None:
    (tmp_path / "reservations").mkdir()
    (tmp_path / "reservations" / "state.yaml").write_text("- not a mapping\n")
    result = subprocess.run(
        [sys.executable, str(MAIN_PY)],
        cwd=MAIN_PY.parent,
        env=_env(**{**CHART_ENV, "DATA": str(tmp_path)}),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 1
    assert "startup configuration error:" in result.stdout
    assert "state.yaml must be a mapping" in result.stdout


# ---- Settings.from_env -----------------------------------------------------


def test_settings_from_the_charts_environment() -> None:
    settings = Settings.from_env(
        {
            **CHART_ENV,
            "PORT": "8080",
            "DNS1_ENDPOINT": "http://dns-1.dns-headless:9000/refresh",
            "DHCP_ENDPOINT": "http://dhcp:9000/refresh",
            "DHCP_APP_ENDPOINT": "http://dhcp-app:9000/refresh",
            "REFRESH_TIMEOUT_SECONDS": "12.5",
        }
    )

    assert settings == Settings(
        network=ipaddress.IPv4Network("192.0.2.0/24"),
        data_dir="/data",
        auth_token="secret",
        dns0_endpoint="http://dns-0.dns-headless:9000/refresh",
        dns1_endpoint="http://dns-1.dns-headless:9000/refresh",
        dhcp_endpoint="http://dhcp:9000/refresh",
        dhcp_app_endpoint="http://dhcp-app:9000/refresh",
        refresh_timeout=12.5,
        port=8080,
    )
    assert settings.refresh_endpoints == [
        "http://dns-0.dns-headless:9000/refresh",
        "http://dns-1.dns-headless:9000/refresh",
        "http://dhcp:9000/refresh",
        "http://dhcp-app:9000/refresh",
    ]


def test_settings_defaults_and_empty_optionals() -> None:
    settings = Settings.from_env({**CHART_ENV, "DNS1_ENDPOINT": ""})

    assert settings.port == 8080
    assert settings.refresh_timeout == 30.0
    assert settings.dns1_endpoint is None
    assert settings.refresh_endpoints == ["http://dns-0.dns-headless:9000/refresh"]


def test_settings_empty_required_counts_as_missing() -> None:
    with pytest.raises(ConfigError, match=r"variable\(s\): DATA, AUTH_TOKEN$"):
        Settings.from_env({**CHART_ENV, "DATA": "", "AUTH_TOKEN": ""})


@pytest.mark.parametrize(
    ("cidr", "message"),
    [
        ("192.0.2", "is not a valid IPv4 network"),
        ("192.0.2.5/24", "is not a valid IPv4 network: 192.0.2.5/24 has host bits set"),
        ("192.0.0.0/16", "must be a /24, got /16"),
    ],
)
def test_settings_reject_a_bad_cidr(cidr: str, message: str) -> None:
    with pytest.raises(ConfigError, match=f"RESERVATION_CIDR \\('{cidr}'\\)") as e:
        Settings.from_env({**CHART_ENV, "RESERVATION_CIDR": cidr})
    assert message in str(e.value)
