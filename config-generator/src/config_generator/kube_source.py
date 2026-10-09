"""The Kubernetes event source for the Service watch."""

import logging
import os
from collections.abc import Iterator, Mapping
from typing import Any

import urllib3
from kubernetes import client, config, watch

logger = logging.getLogger(__name__)

SERVICE_ACCOUNT_PATH = "/var/run/secrets/kubernetes.io/serviceaccount"


class KubernetesEventSource:
    """A watch stream over all Services in the cluster."""

    def __init__(self) -> None:
        if os.path.exists(SERVICE_ACCOUNT_PATH):
            config.load_incluster_config()
        else:
            config.load_kube_config()

        logger.warning(
            "Disabling SSL verification because of "
            "https://github.com/canonical/microk8s/issues/4864"
        )
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        configuration = client.Configuration.get_default_copy()
        configuration.verify_ssl = False

        self._client = client.CoreV1Api(client.ApiClient(configuration=configuration))

    def stream(self) -> Iterator[Mapping[str, Any]]:
        events: Iterator[Mapping[str, Any]] = watch.Watch().stream(
            self._client.list_service_for_all_namespaces,
            # Bounds the watch so a half-open connection cannot wedge it: the
            # server closes the stream after 5 min (the watch loop reconnects),
            # and the longer client-side read timeout raises if the connection
            # dies with no close.
            timeout_seconds=300,
            _request_timeout=310,
        )
        return events
