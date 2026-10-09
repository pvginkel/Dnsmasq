"""`python main.py generate|serve`, the commands the deploy chart runs."""

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

MAIN_PY = Path(__file__).parents[1] / "main.py"


def _env(**variables: str) -> dict[str, str]:
    return {**os.environ, **variables}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def test_generate(static_hosts: Path, tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(MAIN_PY), "generate"],
        cwd=MAIN_PY.parent,
        env=_env(
            STATIC_HOSTS=str(static_hosts),
            DNS_CONFIG_TARGET=str(tmp_path / "hosts"),
            CNAME_CONFIG_TARGET=str(tmp_path / "cname.conf"),
        ),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "10.99.0.1 alpha.home" in (tmp_path / "hosts").read_text()
    assert "cname=omega.home,gamma.home" in (tmp_path / "cname.conf").read_text()


def test_generate_exits_non_zero_on_bad_config(tmp_path: Path) -> None:
    static = tmp_path / "static.yaml"
    static.write_text("domain: home\nhosts: []\n")

    result = subprocess.run(
        [sys.executable, str(MAIN_PY), "generate"],
        cwd=MAIN_PY.parent,
        env=_env(STATIC_HOSTS=str(static), DNS_CONFIG_TARGET=str(tmp_path / "hosts")),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 1
    assert "missing 'tag'" in result.stdout


@pytest.fixture
def reader_serve(tmp_path: Path) -> Iterator[str]:
    """`python main.py serve` in DHCPApp's reader role, on a free port."""
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, str(MAIN_PY), "serve"],
        cwd=MAIN_PY.parent,
        env=_env(
            DYNAMIC_HOSTS=str(tmp_path / "dynamic-hosts.yaml"),
            DHCP_CONFIG_TARGET=str(tmp_path / "dhcp-hosts"),
            DOMAIN="home",
            DHCP_TAG="intranet",
            DNSMASQ_PID_FILE="",
            PORT=str(port),
        ),
    )
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        process.wait(timeout=10)


def _wait_ready(url: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/ready", timeout=2.0) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError):
            pass
        time.sleep(0.1)
    raise TimeoutError(f"{url} did not become ready")


def test_serve_in_reader_role(reader_serve: str, tmp_path: Path) -> None:
    """The listener binds only once startup has rendered; nothing waits on a
    dnsmasq."""
    _wait_ready(reader_serve)
    assert (tmp_path / "dhcp-hosts").exists()

    request = urllib.request.Request(f"{reader_serve}/refresh", method="POST")
    with urllib.request.urlopen(request, timeout=5.0) as r:
        assert json.loads(r.read()) == {"status": "ok", "changed": False}
