"""The integration tier: what only the built images show, each stack a one-pod Job.

The namespace the stacks run in and the images under test come from the
environment: STACK_NAMESPACE, DNSMASQ_IMAGE, CONFIG_GENERATOR_IMAGE and
MANAGEMENT_API_IMAGE. The stacks the collected tests use start together, at the
first test that needs one, and are deleted when the session ends.
"""

import os
from collections.abc import Iterator

import pytest

from tier.cluster import Cluster
from tier.stacks import KINDS, Images, Launcher, Stack

_STACK_FIXTURES = {f"{kind}_stack": kind for kind in KINDS}


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise pytest.UsageError(f"{name} is not set")
    return value


@pytest.fixture(scope="session")
def cluster() -> Cluster:
    return Cluster(_env("STACK_NAMESPACE"))


@pytest.fixture(scope="session")
def launcher(request: pytest.FixtureRequest, cluster: Cluster) -> Iterator[Launcher]:
    images = Images(
        dnsmasq=_env("DNSMASQ_IMAGE"),
        config_generator=_env("CONFIG_GENERATOR_IMAGE"),
        management_api=_env("MANAGEMENT_API_IMAGE"),
    )
    wanted = sorted(
        {
            _STACK_FIXTURES[name]
            for item in request.session.items
            for name in getattr(item, "fixturenames", ())
            if name in _STACK_FIXTURES
        }
    )
    launcher = Launcher(cluster, images)
    try:
        for kind in wanted:
            launcher.start(kind)
        yield launcher
    finally:
        launcher.delete_all()


@pytest.fixture(scope="session")
def bare_stack(launcher: Launcher) -> Stack:
    return launcher.ready("bare")


@pytest.fixture(scope="session")
def dns_stack(launcher: Launcher) -> Stack:
    return launcher.ready("dns")


@pytest.fixture(scope="session")
def reader_stack(launcher: Launcher) -> Stack:
    return launcher.ready("reader")


@pytest.fixture(scope="session")
def chain_stack(launcher: Launcher) -> Stack:
    return launcher.ready("chain")
