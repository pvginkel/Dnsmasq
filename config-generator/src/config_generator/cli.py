"""The command line: `generate` or `serve`, configured from the environment."""

import argparse
import logging
import os
import sys
from collections.abc import Sequence

from .rendering import ConfigError
from .serve import serve
from .settings import Settings
from .static import generate

logger = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(
        stream=sys.stdout,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = argparse.ArgumentParser(prog="dnsmasq-config-generator")
    parser.add_argument(
        "command",
        choices=["generate", "serve"],
        help="generate: render static config once; serve: run the dynamic service",
    )
    args = parser.parse_args(argv)
    settings = Settings.from_env(os.environ)

    if args.command == "generate":
        try:
            generate(settings)
        except ConfigError as e:
            logger.error("generate failed: %s", e)
            sys.exit(1)
    else:
        serve(settings)
