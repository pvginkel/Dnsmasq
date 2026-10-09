"""`serve`'s HTTP API: liveness, readiness, and the refresh the management API
pushes."""

import logging

from flask import Flask, jsonify
from flask.logging import default_handler
from flask.typing import ResponseReturnValue

from .dynamic import DynamicConfig
from .rendering import RenderError

logger = logging.getLogger(__name__)


def create_app(config: DynamicConfig) -> Flask:
    app = Flask(__name__)
    app.logger.setLevel(logging.INFO)
    app.logger.removeHandler(default_handler)

    @app.get("/healthz")
    def healthz() -> ResponseReturnValue:
        return jsonify(status="ok")

    @app.get("/ready")
    def ready() -> ResponseReturnValue:
        if not config.ready:
            return jsonify(status="not-ready"), 503
        return jsonify(status="ok")

    @app.post("/refresh")
    def refresh() -> ResponseReturnValue:
        try:
            changed = config.refresh()
        except RenderError as e:
            logger.error("refresh render failed: %s", e)
            return jsonify(error="render_failed", message=str(e)), 500
        return jsonify(status="ok", changed=changed)

    return app
