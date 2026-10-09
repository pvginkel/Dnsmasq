"""`generate`: the static config the init container renders before dnsmasq boots.

That dnsmasq serves what is rendered here (a CNAME resolving to its target's
addresses) is the integration tier's to show.
"""

from pathlib import Path

import pytest

from config_generator.cli import main
from config_generator.rendering import ConfigError
from config_generator.settings import Settings
from config_generator.static import generate


def _nonempty_lines(s: str) -> list[str]:
    return [line for line in s.splitlines() if line and not line.startswith("#")]


@pytest.fixture
def rendered(static_hosts: Path, tmp_path: Path) -> Path:
    """The fixture's static config, rendered into all three targets."""
    out = tmp_path / "out"
    out.mkdir()
    generate(
        Settings(
            static_hosts=str(static_hosts),
            dns_config_target=str(out / "hosts"),
            dhcp_config_target=str(out / "dhcp-hosts"),
            cname_config_target=str(out / "cname.conf"),
        )
    )
    return out


def _generate_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, static_text: str
) -> Path:
    """Run `generate` through the command line against a one-off static file,
    as the init container does. Returns the output directory."""
    static = tmp_path / "static.yaml"
    static.write_text(static_text)
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setenv("STATIC_HOSTS", str(static))
    monkeypatch.setenv("DNS_CONFIG_TARGET", str(out / "hosts"))
    monkeypatch.setenv("DHCP_CONFIG_TARGET", str(out / "dhcp-hosts"))
    monkeypatch.setenv("CNAME_CONFIG_TARGET", str(out / "cname.conf"))
    main(["generate"])
    return out


def _assert_generate_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    static_text: str,
    mentions: str,
) -> None:
    with pytest.raises(SystemExit) as exc:
        _generate_cli(monkeypatch, tmp_path, static_text)
    assert exc.value.code == 1
    assert mentions in caplog.text.lower()


# ---- DNS render -------------------------------------------------------------


def test_dns_render_emits_expected_hosts_lines(rendered: Path) -> None:
    """An addn-hosts line for every static A-record host."""
    assert _nonempty_lines((rendered / "hosts").read_text()) == [
        "10.99.0.1 alpha.home",
        "10.99.0.2 beta.home",
        "10.99.0.3 gamma.home",
        "10.99.0.4 gamma.home",
        "10.99.0.5 delta.home",
    ]


def test_dns_render_excludes_cname_from_hosts(rendered: Path) -> None:
    """A `type: cname` entry is a CNAME, not an A record."""
    assert "omega" not in (rendered / "hosts").read_text()


def test_dns_render_uses_field_naming_from_static_hosts(rendered: Path) -> None:
    """`name` and `address` flow through to the output verbatim — guards against
    key renames in the templates."""
    assert "10.99.0.1 alpha.home" in (rendered / "hosts").read_text()


# ---- CNAME render -----------------------------------------------------------


def test_cname_render_emits_cname_directive(rendered: Path) -> None:
    """`type: cname` entries become dnsmasq `cname=` directives, with the domain
    appended to both sides."""
    assert _nonempty_lines((rendered / "cname.conf").read_text()) == [
        "cname=omega.home,gamma.home"
    ]


# ---- DHCP render ------------------------------------------------------------


def test_dhcp_render_emits_only_hosts_with_mac(rendered: Path) -> None:
    assert _nonempty_lines((rendered / "dhcp-hosts").read_text()) == [
        "set:intranet,id:*,02:00:00:00:00:02,10.99.0.2,beta.home",
        "set:intranet,id:*,02:00:00:00:00:05,10.99.0.5,delta.home",
    ]


def test_dhcp_render_uses_field_naming_from_static_hosts(rendered: Path) -> None:
    assert "02:00:00:00:00:02,10.99.0.2,beta.home" in (
        (rendered / "dhcp-hosts").read_text()
    )


def test_only_the_configured_targets_are_written(
    static_hosts: Path, tmp_path: Path
) -> None:
    """The DNS pod renders hosts and CNAMEs, the DHCP pods the hostsfile alone."""
    generate(
        Settings(
            static_hosts=str(static_hosts),
            dhcp_config_target=str(tmp_path / "dhcp-hosts"),
        )
    )
    assert [p.name for p in tmp_path.iterdir()] == ["dhcp-hosts"]


# ---- Failure modes ----------------------------------------------------------


def test_missing_static_tag_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _assert_generate_fails(
        monkeypatch, tmp_path, caplog, "domain: home\nhosts: []\n", mentions="tag"
    )


def test_missing_static_domain_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _assert_generate_fails(
        monkeypatch, tmp_path, caplog, "tag: intranet\nhosts: []\n", mentions="domain"
    )


def test_cname_missing_target_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _assert_generate_fails(
        monkeypatch,
        tmp_path,
        caplog,
        "tag: intranet\ndomain: home\nhosts:\n  - name: b\n    type: cname\n",
        mentions="target",
    )


def test_unknown_host_type_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _assert_generate_fails(
        monkeypatch,
        tmp_path,
        caplog,
        "tag: intranet\ndomain: home\nhosts:\n  - name: b\n    type: mx\n",
        mentions="unknown type 'mx'",
    )


def test_host_without_name_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _assert_generate_fails(
        monkeypatch,
        tmp_path,
        caplog,
        "tag: intranet\ndomain: home\nhosts:\n  - address: 10.0.0.1\n",
        mentions="missing 'name'",
    )


def test_malformed_static_yaml_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _assert_generate_fails(
        monkeypatch,
        tmp_path,
        caplog,
        ":\n  this is: not - valid: yaml",
        mentions="failed to load static_hosts",
    )


def test_cname_to_unknown_target_is_allowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A CNAME target need not be a host in static-hosts.yaml — it may be a
    management-API reservation, or not exist yet. dnsmasq resolves the target at
    query time, so `generate` renders the directive regardless."""
    out = _generate_cli(
        monkeypatch,
        tmp_path,
        "tag: intranet\n"
        "domain: home\n"
        "hosts:\n"
        "  - name: a\n"
        "    address: 10.0.0.1\n"
        "  - name: b\n"
        "    type: cname\n"
        "    target: dynamic-vm\n",
    )
    assert "cname=b.home,dynamic-vm.home" in (out / "cname.conf").read_text()


def test_generate_needs_a_target(static_hosts: Path) -> None:
    with pytest.raises(ConfigError, match="must be set"):
        generate(Settings(static_hosts=str(static_hosts)))


def test_generate_needs_static_hosts(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="STATIC_HOSTS"):
        generate(Settings(dns_config_target=str(tmp_path / "hosts")))
