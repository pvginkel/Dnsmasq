"""The refresh fan-out after a change: DNS0, DNS1, DHCP, then DHCPApp, one at
a time, and no failure among them fails the request."""

import time

import pytest
from conftest import REFRESH_TIMEOUT
from fake_refresh import FakeRefresh
from harness import ManagementApi

from management_api.settings import Settings


def test_all_endpoints_2xx_each_gets_one_hit(api: ManagementApi) -> None:
    r = api.put("host-a", "02:00:00:00:00:01")
    assert r.status_code == 201

    for name, fake in api.fakes.items():
        assert [hit.path for hit in fake.hits] == ["/refresh"], name


def test_dns_chain_serialises(api: ManagementApi) -> None:
    # DNS1 must not be called until DNS0 has answered.
    api.fakes["dns0"].configure(delay=0.5)

    assert api.put("host-a", "02:00:00:00:00:01").status_code == 201

    dns0_hits = api.fakes["dns0"].hits
    dns1_hits = api.fakes["dns1"].hits
    assert len(dns0_hits) == 1 and len(dns1_hits) == 1
    delta = dns1_hits[0].ts - dns0_hits[0].ts
    assert delta >= 0.4, f"DNS1 fired too early relative to DNS0: {delta:.3f}s"


def test_dhcp_then_dhcp_app_run_after_dns_chain(api: ManagementApi) -> None:
    api.fakes["dns0"].configure(delay=0.4)

    assert api.put("host-a", "02:00:00:00:00:01").status_code == 201

    hits = {name: fake.hits for name, fake in api.fakes.items()}
    assert all(len(h) == 1 for h in hits.values()), hits
    # Each arrives strictly after the one before it, DHCPApp last.
    assert (
        hits["dns0"][0].ts
        < hits["dns1"][0].ts
        < hits["dhcp"][0].ts
        < hits["dhcp_app"][0].ts
    )
    delta = hits["dhcp"][0].ts - hits["dns0"][0].ts
    assert delta >= 0.3, f"DHCP fired too soon after DNS0 ({delta:.3f}s)"


def test_dns0_5xx_does_not_abort_the_chain(
    api: ManagementApi, caplog: pytest.LogCaptureFixture
) -> None:
    api.fakes["dns0"].configure(status=503)

    # The change is persisted; a failed refresh is only logged.
    assert api.put("host-a", "02:00:00:00:00:01").status_code == 201

    assert api.hit_counts() == dict.fromkeys(api.fakes, 1)
    assert f"refresh {api.fakes['dns0'].url} -> 503" in caplog.messages


def test_dns0_timeout_does_not_abort_chain(
    api: ManagementApi, caplog: pytest.LogCaptureFixture
) -> None:
    api.fakes["dns0"].configure(delay=REFRESH_TIMEOUT * 3)

    t0 = time.monotonic()
    r = api.put("host-a", "02:00:00:00:00:01")
    elapsed = time.monotonic() - t0

    assert r.status_code == 201
    # DNS0's timeout, then three immediate answers: well short of DNS0's delay.
    assert elapsed < REFRESH_TIMEOUT * 2, f"PUT took too long: {elapsed:.2f}s"
    assert api.hit_counts() == dict.fromkeys(api.fakes, 1)
    assert f"refresh {api.fakes['dns0'].url} failed: ReadTimeout" in caplog.messages


def test_all_endpoints_down_still_returns_2xx(
    api: ManagementApi, caplog: pytest.LogCaptureFixture
) -> None:
    for fake in api.fakes.values():
        fake.stop()

    r = api.put("host-a", "02:00:00:00:00:01")
    assert r.status_code == 201, r.text
    assert api.get("host-a").status_code == 200
    failures = [m for m in caplog.messages if m.endswith("failed: ConnectionError")]
    assert len(failures) == 4


def test_noop_put_does_not_fan_out(api: ManagementApi) -> None:
    api.put("host-a", "02:00:00:00:00:01")
    api.reset_fakes()

    r = api.put("host-a", "02:00:00:00:00:01")
    assert r.status_code == 200
    assert api.hit_counts() == dict.fromkeys(api.fakes, 0)


def test_delete_triggers_fan_out(api: ManagementApi) -> None:
    api.put("host-a", "02:00:00:00:00:01")
    api.reset_fakes()

    r = api.delete("host-a")
    assert r.status_code == 204
    assert api.hit_counts() == dict.fromkeys(api.fakes, 1)


def test_only_configured_endpoints_are_refreshed(
    settings: Settings, fakes: dict[str, FakeRefresh]
) -> None:
    """A dev cluster's shape: one DNS replica, no DHCP, no DHCPApp."""
    only_dns0 = Settings(
        network=settings.network,
        data_dir=settings.data_dir,
        auth_token=settings.auth_token,
        dns0_endpoint=fakes["dns0"].url,
        refresh_timeout=settings.refresh_timeout,
    )
    api = ManagementApi(only_dns0, fakes)
    api.start()

    assert api.put("host-a", "02:00:00:00:00:01").status_code == 201
    assert api.hit_counts() == {"dns0": 1, "dns1": 0, "dhcp": 0, "dhcp_app": 0}
