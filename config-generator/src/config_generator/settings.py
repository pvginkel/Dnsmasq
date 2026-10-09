"""The configuration, read from the environment the deploy chart sets."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Self

DEFAULT_DNSMASQ_PID_FILE = "/var/run/dnsmasq.pid"


@dataclass(frozen=True)
class Settings:
    """Every variable is optional here; each command validates the ones it needs.

    An empty variable counts as unset.
    """

    port: int = 9000
    static_hosts: str | None = None
    dynamic_hosts: str | None = None
    dns_config_target: str | None = None
    dhcp_config_target: str | None = None
    cname_config_target: str | None = None
    domain: str | None = None
    dhcp_tag: str | None = None
    nginx_service_name: str | None = None
    nginx_service_namespace: str | None = None
    # None means there is no dnsmasq beside this process to signal.
    dnsmasq_pid_file: str | None = DEFAULT_DNSMASQ_PID_FILE

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Self:
        def optional(name: str) -> str | None:
            return env.get(name) or None

        return cls(
            port=int(env.get("PORT", "9000")),
            static_hosts=optional("STATIC_HOSTS"),
            dynamic_hosts=optional("DYNAMIC_HOSTS"),
            dns_config_target=optional("DNS_CONFIG_TARGET"),
            dhcp_config_target=optional("DHCP_CONFIG_TARGET"),
            cname_config_target=optional("CNAME_CONFIG_TARGET"),
            domain=optional("DOMAIN"),
            dhcp_tag=optional("DHCP_TAG"),
            nginx_service_name=optional("NGINX_SERVICE_NAME"),
            nginx_service_namespace=optional("NGINX_SERVICE_NAMESPACE"),
            dnsmasq_pid_file=env.get("DNSMASQ_PID_FILE", DEFAULT_DNSMASQ_PID_FILE)
            or None,
        )
