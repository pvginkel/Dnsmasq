"""Pushes a reservation change to the config generators.

Each `POST /refresh` makes a generator re-render its dynamic config and SIGHUP
its dnsmasq. The calls are sequential — DNS0, DNS1, DHCP, then DHCPApp, which
only reads the reservations to report on leases, so nothing on the network
waits on it.

A failed call is logged, never raised: the change is already on disk, and a
generator that missed it picks it up at its next refresh or restart.
"""

import logging
from collections.abc import Sequence

import requests

logger = logging.getLogger(__name__)


def fan_out(endpoints: Sequence[str], timeout: float) -> None:
    for url in endpoints:
        _post_refresh(url, timeout)


def _post_refresh(url: str, timeout: float) -> None:
    try:
        r = requests.post(url, timeout=timeout)
    except requests.RequestException as e:
        logger.warning("refresh %s failed: %s", url, e.__class__.__name__)
        return
    if 200 <= r.status_code < 300:
        logger.info("refresh %s -> %d", url, r.status_code)
    else:
        logger.warning("refresh %s -> %d", url, r.status_code)
