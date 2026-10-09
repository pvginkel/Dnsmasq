"""The management API in-process, its refresh endpoints faked beside it."""

from dataclasses import dataclass, field

from fake_refresh import FakeRefresh
from flask.testing import FlaskClient
from werkzeug.test import TestResponse

from management_api.app import create_app
from management_api.settings import Settings
from management_api.store import ReservationStore

TOKEN = "testtoken"


@dataclass
class ManagementApi:
    settings: Settings
    # Keyed dns0, dns1, dhcp, dhcp_app.
    fakes: dict[str, FakeRefresh]
    store: ReservationStore = field(init=False)
    client: FlaskClient = field(init=False)

    def start(self) -> None:
        """Starts the API, or starts it again over the same data directory, the
        way the process does: recovery and load, then a fresh app."""
        self.store = ReservationStore(self.settings.data_dir, self.settings.network)
        self.store.open()
        self.client = create_app(self.settings, self.store).test_client()

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {TOKEN}"}

    def put(self, hostname: str, mac: str) -> TestResponse:
        return self.client.put(
            f"/reservations/{hostname}", headers=self.headers, json={"mac": mac}
        )

    def get(self, hostname: str) -> TestResponse:
        return self.client.get(f"/reservations/{hostname}", headers=self.headers)

    def delete(self, hostname: str) -> TestResponse:
        return self.client.delete(f"/reservations/{hostname}", headers=self.headers)

    def list(self) -> TestResponse:
        return self.client.get("/reservations", headers=self.headers)

    def reset_fakes(self) -> None:
        for fake in self.fakes.values():
            fake.reset()

    def hit_counts(self) -> dict[str, int]:
        return {name: len(fake.hits) for name, fake in self.fakes.items()}
