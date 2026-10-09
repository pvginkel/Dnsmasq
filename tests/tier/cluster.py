"""The stacks' Jobs, through the only cluster verbs the suite uses.

Those are Jobs create/get/delete and pods list/get plus pods/log get: plain
REST calls held both by Role `jenkins-agent-jobs` in the Jenkins validation
Job's namespace and by ClusterRole `edit` in `development`. The suite never
execs, and never creates or deletes a Pod: a stack's pod comes and goes with
its Job.
"""

from dataclasses import dataclass
from typing import Any

import kubernetes

from .waiting import wait_for

# A container in one of these states will not start without a change to the
# stack, so waiting longer only delays the report.
_STUCK = {
    "CrashLoopBackOff",
    "CreateContainerConfigError",
    "CreateContainerError",
    "ErrImagePull",
    "ImagePullBackOff",
    "InvalidImageName",
}


class StackFailed(Exception):
    pass


@dataclass(frozen=True)
class Pod:
    name: str
    ip: str


class Cluster:
    def __init__(self, namespace: str) -> None:
        kubernetes.config.load_config()
        configuration = kubernetes.client.Configuration.get_default_copy()
        # The cluster's CA carries no keyUsage extension, which certificate
        # verification on Python 3.13+ rejects
        # (https://github.com/canonical/microk8s/issues/4864). The config
        # generator's Service watch turns verification off for the same reason.
        configuration.verify_ssl = False
        client = kubernetes.client.ApiClient(configuration)
        self._batch = kubernetes.client.BatchV1Api(client)
        self._core = kubernetes.client.CoreV1Api(client)
        self.namespace = namespace

    def create_job(self, manifest: dict[str, Any]) -> None:
        self._batch.create_namespaced_job(self.namespace, manifest)

    def delete_job(self, job: str) -> None:
        """Deletes the Job and, with it, its pod.

        A Job deleted without a propagation policy orphans its pod.
        """
        self._batch.delete_namespaced_job(
            job, self.namespace, propagation_policy="Foreground"
        )

    def wait_deleted(self, job: str) -> None:
        """Until the Job is gone: under foreground deletion, after its pod."""
        wait_for(
            lambda: self._job(job) is None or None,
            120.0,
            f"Job {job} and its pod to be deleted",
            interval=1.0,
        )

    def _job(self, job: str) -> Any:
        try:
            return self._batch.read_namespaced_job(job, self.namespace)
        except kubernetes.client.ApiException as e:
            if e.status == 404:
                return None
            raise

    def _pod(self, job: str) -> Any:
        pods = self._core.list_namespaced_pod(
            self.namespace, label_selector=f"job-name={job}"
        ).items
        assert len(pods) <= 1, f"Job {job} has {len(pods)} pods; a stack has one"
        return pods[0] if pods else None

    def wait_running(self, job: str, timeout: float) -> Pod:
        """The Job's pod, once every one of its containers is running."""

        def running() -> Pod | None:
            pod = self._pod(job)
            if pod is None:
                if self._job(job) is None:
                    raise StackFailed(f"Job {job} is gone before its pod ran")
                return None
            self._raise_if_stuck(pod)
            statuses = pod.status.container_statuses or []
            if pod.status.phase == "Running" and all(s.state.running for s in statuses):
                return Pod(pod.metadata.name, pod.status.pod_ip)
            return None

        return wait_for(running, timeout, f"Job {job}'s pod to run", interval=1.0)

    def _raise_if_stuck(self, pod: Any) -> None:
        statuses = (pod.status.init_container_statuses or []) + (
            pod.status.container_statuses or []
        )
        stuck = pod.status.phase == "Failed" or any(
            (s.state.waiting and s.state.waiting.reason in _STUCK)
            or (s.state.terminated and s.state.terminated.exit_code != 0)
            or (s.last_state.terminated and s.last_state.terminated.exit_code != 0)
            for s in statuses
        )
        if stuck:
            raise StackFailed(self._describe(pod.metadata.name, statuses))

    def _describe(self, pod_name: str, statuses: list[Any]) -> str:
        lines = [f"stack pod {pod_name} cannot start:"]
        for s in statuses:
            lines.append(f"--- {s.name}: {s.state}, {s.restart_count} restart(s)")
            if s.state.running or s.state.terminated or s.last_state.terminated:
                previous = s.last_state.terminated is not None
                log = self.log(pod_name, s.name, previous=previous)
                lines.extend(log.splitlines()[-40:])
        return "\n".join(lines)

    def container(self, pod_name: str, container: str) -> Any:
        """The container's status: `ready`, `restart_count`, `container_id`, ..."""
        pod = self._core.read_namespaced_pod(pod_name, self.namespace)
        statuses = (pod.status.init_container_statuses or []) + (
            pod.status.container_statuses or []
        )
        return next(s for s in statuses if s.name == container)

    def log(self, pod_name: str, container: str, previous: bool = False) -> str:
        log: str = self._core.read_namespaced_pod_log(
            pod_name, self.namespace, container=container, previous=previous
        )
        return log
