from collections.abc import Iterator
from pathlib import Path

import pytest
from standin import DnsmasqStandIn

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def static_hosts() -> Path:
    return FIXTURES / "static-hosts.yaml"


@pytest.fixture
def dnsmasq_standin(tmp_path: Path) -> Iterator[DnsmasqStandIn]:
    standin = DnsmasqStandIn(tmp_path / "run")
    standin.start()
    yield standin
    standin.stop()
