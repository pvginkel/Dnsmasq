"""The HTTP API; `dns-reservation-api.md` is its contract."""

import functools
import hmac
import logging
import re
import time
from collections.abc import Callable

from flask import Flask, Response, g, jsonify, request
from flask.logging import default_handler
from flask.typing import ResponseReturnValue

from .fanout import fan_out
from .settings import Settings
from .store import (
    AddressesExhausted,
    MacConflict,
    PutResult,
    Reservation,
    ReservationStore,
)

MAC_RE = re.compile(r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$")
HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def _error(code: str, message: str, status: int) -> ResponseReturnValue:
    return jsonify(error=code, message=message), status


def _invalid_hostname(hostname: str) -> ResponseReturnValue:
    return _error("invalid_hostname", f"Hostname {hostname!r} fails format check.", 400)


def _not_found(hostname: str) -> ResponseReturnValue:
    return _error("not_found", f"No reservation for hostname {hostname!r}.", 404)


def _body(reservation: Reservation) -> dict[str, str]:
    return {
        "hostname": reservation.name,
        "mac": reservation.mac,
        "ipv4": reservation.address,
    }


def create_app(settings: Settings, store: ReservationStore) -> Flask:
    app = Flask(__name__)
    app.logger.setLevel(logging.INFO)
    app.logger.removeHandler(default_handler)

    expected_authorization = f"Bearer {settings.auth_token}"

    def authenticated[**P](
        view: Callable[P, ResponseReturnValue],
    ) -> Callable[P, ResponseReturnValue]:
        @functools.wraps(view)
        def check(*args: P.args, **kwargs: P.kwargs) -> ResponseReturnValue:
            header = request.headers.get("Authorization", "")
            if not hmac.compare_digest(header, expected_authorization):
                return _error("unauthorized", "Missing or invalid bearer token.", 401)
            return view(*args, **kwargs)

        return check

    def refresh() -> None:
        fan_out(settings.refresh_endpoints, settings.refresh_timeout)

    @app.before_request
    def log_request() -> None:
        g.start_time = time.monotonic()
        if request.path != "/healthz":
            app.logger.info("%s %s", request.method, request.full_path)

    @app.after_request
    def log_response(response: Response) -> Response:
        if request.path != "/healthz":
            duration_ms = (time.monotonic() - g.start_time) * 1000
            app.logger.info(
                "%s %s -> %d (%.0fms)",
                request.method,
                request.full_path,
                response.status_code,
                duration_ms,
            )
        return response

    # Registered for Exception, so it also answers Flask's own HTTP errors — an
    # unknown path or method included.
    @app.errorhandler(Exception)
    def handle_exception(e: Exception) -> ResponseReturnValue:
        app.logger.error("Unhandled exception: %s", e, exc_info=True)
        return _error("internal", str(e), 500)

    @app.get("/healthz")
    def healthz() -> ResponseReturnValue:
        return jsonify(status="ok")

    @app.get("/reservations")
    @authenticated
    def list_reservations() -> ResponseReturnValue:
        return jsonify(reservations=[_body(r) for r in store.reservations()])

    @app.get("/reservations/<hostname>")
    @authenticated
    def get_reservation(hostname: str) -> ResponseReturnValue:
        if not HOSTNAME_RE.match(hostname):
            return _invalid_hostname(hostname)
        reservation = store.get(hostname)
        if not reservation:
            return _not_found(hostname)
        return jsonify(_body(reservation))

    @app.put("/reservations/<hostname>")
    @authenticated
    def put_reservation(hostname: str) -> ResponseReturnValue:
        if not HOSTNAME_RE.match(hostname):
            return _invalid_hostname(hostname)

        body = request.get_json(silent=True)
        if not isinstance(body, dict) or "mac" not in body:
            return _error(
                "bad_request", "Body must be a JSON object containing 'mac'.", 400
            )
        mac = body["mac"]
        if not isinstance(mac, str) or not MAC_RE.match(mac):
            return _error("invalid_mac", f"MAC {mac!r} fails format check.", 400)

        try:
            reservation, result = store.put(hostname, mac.upper())
        except MacConflict as e:
            return _error("mac_conflict", str(e), 409)
        except AddressesExhausted:
            return _error(
                "ipv4_exhausted",
                f"No free addresses left in {settings.network}.",
                409,
            )

        if result is not PutResult.UNCHANGED:
            refresh()
        return jsonify(_body(reservation)), 201 if result is PutResult.CREATED else 200

    @app.delete("/reservations/<hostname>")
    @authenticated
    def delete_reservation(hostname: str) -> ResponseReturnValue:
        if not HOSTNAME_RE.match(hostname):
            return _invalid_hostname(hostname)
        if not store.delete(hostname):
            return _not_found(hostname)
        refresh()
        return "", 204

    return app
