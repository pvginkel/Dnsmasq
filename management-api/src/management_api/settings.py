"""The configuration, read from the environment the deploy chart sets."""

import ipaddress
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Self


class ConfigError(Exception):
    """The service cannot start with the configuration or state it was given."""


@dataclass(frozen=True)
class Settings:
    network: ipaddress.IPv4Network
    data_dir: str
    auth_token: str
    dns0_endpoint: str
    dns1_endpoint: str | None = None
    dhcp_endpoint: str | None = None
    dhcp_app_endpoint: str | None = None
    refresh_timeout: float = 30.0
    port: int = 8080

    @property
    def refresh_endpoints(self) -> list[str]:
        """The configured `/refresh` URLs in fan-out order: DNS0, DNS1, DHCP,
        DHCPApp."""
        return [
            url
            for url in (
                self.dns0_endpoint,
                self.dns1_endpoint,
                self.dhcp_endpoint,
                self.dhcp_app_endpoint,
            )
            if url
        ]

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Self:
        """Raises `ConfigError` for a missing required variable or a bad CIDR.

        An empty variable counts as unset, except `PORT` and
        `REFRESH_TIMEOUT_SECONDS`: those must parse when present, and raise
        `ValueError` otherwise.
        """
        port = int(env.get("PORT", "8080"))
        refresh_timeout = float(env.get("REFRESH_TIMEOUT_SECONDS", "30"))

        def optional(name: str) -> str | None:
            return env.get(name) or None

        # Only DNS0_ENDPOINT is required: a dev cluster runs one DNS replica and
        # no DHCP, and DHCP_APP_ENDPOINT is absent wherever DHCPApp is not
        # deployed.
        required = ("RESERVATION_CIDR", "DATA", "AUTH_TOKEN", "DNS0_ENDPOINT")
        missing = [name for name in required if not env.get(name)]
        if missing:
            raise ConfigError(
                f"missing required environment variable(s): {', '.join(missing)}"
            )

        cidr = env["RESERVATION_CIDR"]
        try:
            network = ipaddress.IPv4Network(cidr, strict=True)
        except ValueError as e:
            raise ConfigError(
                f"RESERVATION_CIDR ({cidr!r}) is not a valid IPv4 network: {e}"
            ) from e
        if network.prefixlen != 24:
            raise ConfigError(
                f"RESERVATION_CIDR ({cidr!r}) must be a /24, got /{network.prefixlen}"
            )

        return cls(
            network=network,
            data_dir=env["DATA"],
            auth_token=env["AUTH_TOKEN"],
            dns0_endpoint=env["DNS0_ENDPOINT"],
            dns1_endpoint=optional("DNS1_ENDPOINT"),
            dhcp_endpoint=optional("DHCP_ENDPOINT"),
            dhcp_app_endpoint=optional("DHCP_APP_ENDPOINT"),
            refresh_timeout=refresh_timeout,
            port=port,
        )
