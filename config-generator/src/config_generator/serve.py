"""`serve`: the long-running sidecar that owns the dynamic config."""

import logging
import os
import sys
import threading

from waitress import serve as waitress_serve

from .app import create_app
from .dnsmasq import Dnsmasq
from .dynamic import DynamicConfig
from .rendering import ConfigError, RenderError
from .service_watch import ServiceWatcher
from .settings import Settings

logger = logging.getLogger(__name__)


def _watch_thread(watcher: ServiceWatcher) -> None:
    try:
        watcher.watch_loop()
    except Exception as e:
        logger.error("Exception in watch thread: %s", e)
        os._exit(99)


def serve(settings: Settings) -> None:
    dnsmasq = Dnsmasq(settings.dnsmasq_pid_file) if settings.dnsmasq_pid_file else None
    config = DynamicConfig(settings, dnsmasq)
    try:
        config.startup()
    except ConfigError as e:
        logger.error("startup configuration error: %s", e)
        sys.exit(1)
    except RenderError as e:
        logger.error("startup render error: %s", e)
        sys.exit(1)

    nginx_name = settings.nginx_service_name
    nginx_namespace = settings.nginx_service_namespace
    if settings.dns_config_target and nginx_name and nginx_namespace:
        # Imported here: the kubernetes client is needed only with the watch on.
        from .kube_source import KubernetesEventSource

        watcher = ServiceWatcher(
            event_source=KubernetesEventSource(),
            nginx_service_name=nginx_name,
            nginx_service_namespace=nginx_namespace,
            on_change=config.on_service_change,
        )
        config.watch_services(watcher)
        threading.Thread(target=_watch_thread, args=(watcher,), daemon=True).start()
        logger.info("Kubernetes service watch enabled")
    else:
        logger.info(
            "Kubernetes service watch disabled "
            "(needs DNS_CONFIG_TARGET, NGINX_SERVICE_NAME, NGINX_SERVICE_NAMESPACE)"
        )

    app = create_app(config)
    logging.getLogger("waitress").setLevel(logging.INFO)
    logger.info("Listening on port %d", settings.port)
    waitress_serve(app, listen=f"*:{settings.port}")
