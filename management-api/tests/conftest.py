import ipaddress
from collections.abc import Iterator
from pathlib import Path

import pytest
from fake_refresh import FakeRefresh
from harness import TOKEN, ManagementApi

from management_api.settings import Settings

# Short enough that a refresh timing out keeps a test quick, long enough for the
# delays the ordering tests give their fakes.
REFRESH_TIMEOUT = 1.0


@pytest.fixture
def fakes() -> Iterator[dict[str, FakeRefresh]]:
    fakes = {name: FakeRefresh() for name in ("dns0", "dns1", "dhcp", "dhcp_app")}
    yield fakes
    for fake in fakes.values():
        fake.stop()


@pytest.fixture
def settings(tmp_path: Path, fakes: dict[str, FakeRefresh]) -> Settings:
    return Settings(
        network=ipaddress.IPv4Network("192.0.2.0/24"),
        data_dir=str(tmp_path / "data"),
        auth_token=TOKEN,
        dns0_endpoint=fakes["dns0"].url,
        dns1_endpoint=fakes["dns1"].url,
        dhcp_endpoint=fakes["dhcp"].url,
        dhcp_app_endpoint=fakes["dhcp_app"].url,
        refresh_timeout=REFRESH_TIMEOUT,
    )


@pytest.fixture
def api(settings: Settings, fakes: dict[str, FakeRefresh]) -> ManagementApi:
    api = ManagementApi(settings, fakes)
    api.start()
    return api
