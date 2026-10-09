"""The service: configuration, crash recovery and load, then the HTTP listener."""

import logging
import os
import sys

from waitress import serve

from .app import create_app
from .settings import ConfigError, Settings
from .store import ReservationStore

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(
        stream=sys.stdout,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("waitress").setLevel(logging.INFO)

    try:
        settings = Settings.from_env(os.environ)
        store = ReservationStore(settings.data_dir, settings.network)
        store.open()
    except ConfigError as e:
        logger.error("startup configuration error: %s", e)
        sys.exit(1)
    logger.info(
        "Loaded %d reservations from %s (CIDR %s)",
        len(store),
        store.path,
        settings.network,
    )

    app = create_app(settings, store)
    logger.info("Listening on port %d", settings.port)
    serve(app, listen=f"*:{settings.port}")
