"""The reservation set on disk: its shape, the write protocol (`state.yaml`,
`.tmp`, `.bak`), and the recovery the API runs at startup."""

from pathlib import Path

import pytest
import yaml
from harness import ManagementApi

from management_api.settings import ConfigError, Settings
from management_api.store import ReservationStore


def _read(path: str) -> object:
    with open(path) as f:
        return yaml.safe_load(f)


def _write(path: str, data: object) -> None:
    with open(path, "w") as f:
        yaml.safe_dump(data, f)


# ---- file format -----------------------------------------------------------


def test_state_yaml_uses_static_hosts_field_naming(api: ManagementApi) -> None:
    assert api.put("bravo", "02:00:00:00:00:02").status_code == 201
    assert api.put("alpha", "02:00:00:00:00:01").status_code == 201

    # Exactly static-hosts.yaml's shape, sorted by name.
    assert _read(api.store.path) == {
        "hosts": [
            {"name": "alpha", "address": "192.0.2.2", "mac": "02:00:00:00:00:01"},
            {"name": "bravo", "address": "192.0.2.1", "mac": "02:00:00:00:00:02"},
        ]
    }
    with open(api.store.path) as f:
        assert f.readline() == "hosts:\n"
        assert f.readline() == "- name: alpha\n"


def test_previous_state_is_kept_as_bak(api: ManagementApi) -> None:
    api.put("alpha", "02:00:00:00:00:01")
    first = _read(api.store.path)
    api.put("bravo", "02:00:00:00:00:02")

    assert _read(api.store.bak_path) == first
    assert not Path(api.store.tmp_path).exists()


# ---- restart preservation --------------------------------------------------


def test_state_survives_restart(api: ManagementApi) -> None:
    api.put("alpha", "02:00:00:00:00:01")
    api.put("bravo", "02:00:00:00:00:02")
    api.delete("alpha")
    api.put("charlie", "02:00:00:00:00:03")
    pre = api.list().get_json()["reservations"]
    assert len(pre) == 2

    api.start()

    assert api.list().get_json()["reservations"] == pre


# ---- crash recovery --------------------------------------------------------


def test_recovery_renames_bak_to_state(api: ManagementApi) -> None:
    """A `.bak` without `state.yaml` is promoted to `state.yaml` and loaded."""
    seed = {
        "hosts": [
            {"name": "frombak", "address": "192.0.2.42", "mac": "02:00:00:00:0b:ad"}
        ]
    }
    _write(api.store.bak_path, seed)

    api.start()

    assert api.list().get_json()["reservations"] == [
        {"hostname": "frombak", "mac": "02:00:00:00:0B:AD", "ipv4": "192.0.2.42"}
    ]
    assert Path(api.store.path).exists()
    assert not Path(api.store.bak_path).exists()


def test_recovery_removes_leftover_tmp(api: ManagementApi) -> None:
    seed = {
        "hosts": [{"name": "real", "address": "192.0.2.7", "mac": "02:00:00:00:00:07"}]
    }
    _write(api.store.path, seed)
    Path(api.store.tmp_path).write_text("garbage half-written content\n")

    api.start()

    assert not Path(api.store.tmp_path).exists()
    items = api.list().get_json()["reservations"]
    assert [item["hostname"] for item in items] == ["real"]


def test_recovery_prefers_state_over_bak(api: ManagementApi) -> None:
    _write(
        api.store.path,
        {
            "hosts": [
                {"name": "current", "address": "192.0.2.1", "mac": "02:00:00:00:00:01"}
            ]
        },
    )
    _write(
        api.store.bak_path,
        {
            "hosts": [
                {"name": "previous", "address": "192.0.2.2", "mac": "02:00:00:00:00:02"}
            ]
        },
    )

    api.start()

    items = api.list().get_json()["reservations"]
    assert [item["hostname"] for item in items] == ["current"]


def test_state_that_is_not_a_mapping_fails_startup(settings: Settings) -> None:
    store = ReservationStore(settings.data_dir, settings.network)
    Path(store.directory).mkdir(parents=True)
    _write(store.path, ["not", "a", "mapping"])

    with pytest.raises(ConfigError, match="must be a mapping"):
        store.open()
