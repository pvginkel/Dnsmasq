"""The reservation set and its persistence under `${DATA}/reservations/`.

`state.yaml` has `static-hosts.yaml`'s shape — a `hosts` list of `name`,
`address` and `mac` — because the config generators read it as their dynamic
hosts file.
"""

import ipaddress
import os
import threading
from dataclasses import dataclass, replace
from enum import Enum

import yaml

from .settings import ConfigError


@dataclass(frozen=True)
class Reservation:
    name: str
    address: str
    mac: str


class PutResult(Enum):
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


class MacConflict(Exception):
    def __init__(self, mac: str, holder: str) -> None:
        super().__init__(f"MAC {mac} is already reserved for {holder}.")
        self.mac = mac
        self.holder = holder


class AddressesExhausted(Exception):
    pass


class ReservationStore:
    """Every operation is serialised; a change is on disk when it returns."""

    def __init__(self, data_dir: str, network: ipaddress.IPv4Network) -> None:
        self.directory = os.path.join(data_dir, "reservations")
        self.path = os.path.join(self.directory, "state.yaml")
        self.tmp_path = self.path + ".tmp"
        self.bak_path = self.path + ".bak"
        self._network = network
        self._lock = threading.Lock()
        self._reservations: dict[str, Reservation] = {}

    def open(self) -> None:
        """Recovers from an interrupted write, then loads the set.

        A leftover `.tmp` is a write that never completed and is dropped; a
        `.bak` without `state.yaml` is the last complete state, interrupted
        between its two renames.
        """
        os.makedirs(self.directory, exist_ok=True)
        if os.path.exists(self.tmp_path):
            os.remove(self.tmp_path)
        if not os.path.exists(self.path) and os.path.exists(self.bak_path):
            os.rename(self.bak_path, self.path)
        self._reservations = self._load()

    def __len__(self) -> int:
        with self._lock:
            return len(self._reservations)

    def reservations(self) -> list[Reservation]:
        """Sorted by hostname."""
        with self._lock:
            return sorted(self._reservations.values(), key=lambda r: r.name)

    def get(self, name: str) -> Reservation | None:
        with self._lock:
            return self._reservations.get(name)

    def put(self, name: str, mac: str) -> tuple[Reservation, PutResult]:
        """Creates `name` on the lowest free address, or moves it to a new MAC
        on the address it already holds.

        Raises `MacConflict` when another hostname holds `mac`, and
        `AddressesExhausted` when a new hostname finds no free address.
        """
        with self._lock:
            existing = self._reservations.get(name)
            if existing and existing.mac == mac:
                return existing, PutResult.UNCHANGED

            for other in self._reservations.values():
                if other.name != name and other.mac == mac:
                    raise MacConflict(mac, other.name)

            if existing:
                reservation = replace(existing, mac=mac)
                result = PutResult.UPDATED
            else:
                reservation = Reservation(name, self._allocate(), mac)
                result = PutResult.CREATED
            self._reservations[name] = reservation
            self._save()
        return reservation, result

    def delete(self, name: str) -> bool:
        """Releases `name`'s address; False when there is no such reservation."""
        with self._lock:
            if name not in self._reservations:
                return False
            del self._reservations[name]
            self._save()
        return True

    def _allocate(self) -> str:
        """The lowest free address of .1 to .254; .0 and .255 are never handed
        out."""
        taken = {r.address for r in self._reservations.values()}
        base = int(self._network.network_address)
        for offset in range(1, 255):
            candidate = str(ipaddress.IPv4Address(base + offset))
            if candidate not in taken:
                return candidate
        raise AddressesExhausted

    def _load(self) -> dict[str, Reservation]:
        if not os.path.exists(self.path):
            return {}
        with open(self.path) as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            raise ConfigError(f"{self.path} must be a mapping")
        loaded = {}
        for entry in data.get("hosts") or []:
            name = entry["name"]
            loaded[name] = Reservation(name, entry["address"], entry["mac"].upper())
        return loaded

    def _save(self) -> None:
        """Caller holds the lock. The new state is written beside the old one,
        which becomes `.bak` before the new one takes its name."""
        payload = {
            "hosts": [
                {"name": r.name, "address": r.address, "mac": r.mac}
                for r in sorted(self._reservations.values(), key=lambda r: r.name)
            ]
        }
        with open(self.tmp_path, "w") as f:
            yaml.safe_dump(payload, f, sort_keys=False, default_flow_style=False)

        if os.path.exists(self.bak_path):
            os.remove(self.bak_path)
        if os.path.exists(self.path):
            os.rename(self.path, self.bak_path)
        os.rename(self.tmp_path, self.path)
