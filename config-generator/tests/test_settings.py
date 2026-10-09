"""The environment the deploy chart sets, read into Settings."""

from config_generator.settings import Settings


def test_defaults() -> None:
    settings = Settings.from_env({})
    assert settings.port == 9000
    assert settings.dnsmasq_pid_file == "/var/run/dnsmasq.pid"
    assert settings.domain is None


def test_empty_pid_file_means_no_dnsmasq() -> None:
    assert Settings.from_env({"DNSMASQ_PID_FILE": ""}).dnsmasq_pid_file is None


def test_empty_variables_count_as_unset() -> None:
    settings = Settings.from_env({"DOMAIN": "", "DHCP_CONFIG_TARGET": ""})
    assert settings.domain is None
    assert settings.dhcp_config_target is None


def test_reads_the_dns_pods_serve_environment() -> None:
    settings = Settings.from_env(
        {
            "DYNAMIC_HOSTS": "/mnt/dynamic/reservations/state.yaml",
            "DNS_CONFIG_TARGET": "/mnt/target/hosts",
            "DOMAIN": "home",
            "NGINX_SERVICE_NAME": "nginx",
            "NGINX_SERVICE_NAMESPACE": "nginx-ns",
            "PORT": "9000",
        }
    )
    assert settings == Settings(
        port=9000,
        dynamic_hosts="/mnt/dynamic/reservations/state.yaml",
        dns_config_target="/mnt/target/hosts",
        domain="home",
        nginx_service_name="nginx",
        nginx_service_namespace="nginx-ns",
    )
