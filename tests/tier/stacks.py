"""The stacks: each one Job with one pod, in the shapes the deploy chart runs.

The containers are the chart's (`DnsmasqDeploy/chart/templates/`): a DNS or DHCP
replica is the config generator's `generate` as an init container, dnsmasq, and
`serve` beside it in one process namespace. Everything a stack needs at start
arrives through the Job spec: a `stage` init container writes the files a
ConfigMap supplies in production. A `control` native sidecar (`tier.agent`)
serves what the suite does inside the stack, mounting every volume at
`/stack/<volume>`. Its startup probe holds back the stack's other containers,
so what they send it at startup, such as dnsmasq's lease-change script run,
reaches it.
"""

import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from . import agent, dnsquery
from .cluster import Cluster
from .control import Control
from .waiting import wait_for

CONTROL_PORT = 7000
MANAGEMENT_PORT = 8080
AUTH_TOKEN = "testtoken"
RESERVATION_CIDR = "192.0.2.0/24"

# A suite that dies mid-run leaves its stacks to these: the deadline stops a
# stack's pod, and the finished Job is deleted at once.
ACTIVE_DEADLINE_SECONDS = 1800
TTL_SECONDS_AFTER_FINISHED = 0

STATIC_HOSTS = (
    Path(__file__).parent.parent / "fixtures" / "static-hosts.yaml"
).read_text()

# The chart's `00-setup.conf` for DNS (`stage-manifests.yaml`). Its upstream
# servers are left out: a name a stack does not hold is refused, not forwarded.
DNS_CONF = "no-hosts\nno-resolv\nlog-facility=/proc/self/fd/1\ndomain=home\n"

# The chart's DHCP `00-setup.conf` and `10-dhcp.conf`, with a range of its own.
DHCP_CONF = (
    "no-hosts\nno-resolv\nlog-facility=/proc/self/fd/1\ndomain=home\nport=0\n"
    "dhcp-leasefile=/var/lib/dnsmasq/dnsmasq.leases\nlog-dhcp\n"
    "dhcp-range=set:intranet,198.51.100.10,198.51.100.199,255.255.255.0,86400\n"
)

# One lease that never expires: dnsmasq runs its lease-change script for it at
# startup.
SEEDED_LEASE = "0 02:00:00:00:aa:01 198.51.100.20 leased *\n"

# The chart's probes on dnsmasq (`dns-statefulset.yaml`, `dhcp-deployment.yaml`).
DNS_LIVENESS_COMMAND = ["sh", "-c", "nslookup . 127.0.0.1"]
DHCP_READINESS_COMMAND = [
    "sh",
    "-c",
    "grep -q '^ *[0-9]*: [0-9A-F]*:0043 ' /proc/net/udp",
]

# dnsmasq run as the image runs it, except that it stays down while the hold
# file exists: a container in a Job's pod comes back only when it fails.
HOLD_FILE = "/stack/hold/dnsmasq"
_HELD_DNSMASQ = [
    "sh",
    "-c",
    "while :; do [ -e /hold/dnsmasq ] || dnsmasq -k; sleep 0.2; done",
]

_STAGE = (
    "import json, os\n"
    "for path, text in json.loads(os.environ['STACK_FILES']).items():\n"
    "    os.makedirs(os.path.dirname(path), exist_ok=True)\n"
    "    with open(path, 'w') as f:\n"
    "        f.write(text)\n"
)


@dataclass(frozen=True)
class Images:
    dnsmasq: str
    config_generator: str
    management_api: str


def _mount(volume: str, path: str, read_only: bool = False) -> dict[str, Any]:
    return {"name": volume, "mountPath": path, "readOnly": read_only}


def _env(**values: str) -> list[dict[str, str]]:
    return [{"name": name, "value": value} for name, value in values.items()]


def _container(name: str, image: str, **spec: Any) -> dict[str, Any]:
    # A dev image's tag names a new image on every run (`run-dev.sh`).
    return {"name": name, "image": image, "imagePullPolicy": "Always", **spec}


def _exec_probe(command: list[str]) -> dict[str, Any]:
    return {"exec": {"command": command}, "periodSeconds": 2}


@dataclass
class _Pod:
    images: Images
    share_processes: bool = True
    volumes: list[str] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)
    init: list[dict[str, Any]] = field(default_factory=list)
    containers: list[dict[str, Any]] = field(default_factory=list)

    def volume(self, name: str) -> str:
        self.volumes.append(name)
        return name

    def container(self, name: str, image: str, **spec: Any) -> None:
        self.containers.append(_container(name, image, **spec))

    def init_container(self, name: str, image: str, **spec: Any) -> None:
        self.init.append(_container(name, image, **spec))

    def generate(self, name: str, static: str, **env: str) -> None:
        self.init_container(
            name,
            self.images.config_generator,
            command=["python", "main.py", "generate"],
            env=_env(STATIC_HOSTS="/mnt/source/static-hosts.yaml", **env),
            volumeMounts=[
                _mount("static-hosts", "/mnt/source", read_only=True),
                _mount(static, "/mnt/target"),
            ],
        )

    def spec(self) -> dict[str, Any]:
        every_volume = [_mount(v, f"/stack/{v}") for v in self.volumes]
        stage = _container(
            "stage",
            self.images.config_generator,
            command=["python", "-c", _STAGE],
            env=_env(STACK_FILES=json.dumps(self.files)),
            volumeMounts=every_volume,
        )
        control = _container(
            "control",
            self.images.config_generator,
            command=["python", "-c", Path(agent.__file__).read_text()],
            args=[str(CONTROL_PORT)],
            restartPolicy="Always",
            startupProbe={
                "httpGet": {"path": "/healthz", "port": CONTROL_PORT},
                "periodSeconds": 1,
                "failureThreshold": 60,
            },
            volumeMounts=every_volume,
        )
        return {
            "restartPolicy": "OnFailure",
            "shareProcessNamespace": self.share_processes,
            "automountServiceAccountToken": False,
            "enableServiceLinks": False,
            "terminationGracePeriodSeconds": 1,
            "initContainers": [stage, control, *self.init],
            "containers": self.containers,
            "volumes": [{"name": v, "emptyDir": {}} for v in self.volumes],
        }


def _dns_replica(
    pod: _Pod,
    prefix: str,
    *,
    data: str,
    dns_port: int,
    serve_port: int,
    held: bool = False,
) -> None:
    conf = pod.volume(f"{prefix}-conf")
    static = pod.volume(f"{prefix}-static")
    generated = pod.volume(f"{prefix}-generated")
    run = pod.volume(f"{prefix}-run")
    pod.files[f"/stack/{conf}/00-setup.conf"] = DNS_CONF + (
        f"port={dns_port}\n" if dns_port != 53 else ""
    )
    pod.generate(
        f"{prefix}-generate",
        static,
        DNS_CONFIG_TARGET="/mnt/target/hosts",
        CNAME_CONFIG_TARGET="/mnt/target/cname.conf",
    )
    mounts = [
        _mount(conf, "/etc/dnsmasq.d", read_only=True),
        _mount(generated, "/etc/dnsmasq-generated.d", read_only=True),
        _mount(static, "/etc/dnsmasq-static-generated.d", read_only=True),
        _mount(run, "/var/run"),
    ]
    held_args = {}
    if held:
        mounts.append(_mount(pod.volume("hold"), "/hold"))
        held_args = {"args": _HELD_DNSMASQ}
    pod.container(
        f"{prefix}-dnsmasq",
        pod.images.dnsmasq,
        env=_env(TINI_SUBREAPER="true"),
        volumeMounts=mounts,
        **held_args,
    )
    pod.container(
        f"{prefix}-serve",
        pod.images.config_generator,
        command=["python", "main.py", "serve"],
        env=_env(
            TINI_SUBREAPER="true",
            DYNAMIC_HOSTS="/mnt/dynamic/reservations/state.yaml",
            DNS_CONFIG_TARGET="/mnt/target/hosts",
            DOMAIN="home",
            PORT=str(serve_port),
        ),
        volumeMounts=[
            _mount(data, "/mnt/dynamic", read_only=True),
            _mount(generated, "/mnt/target"),
            _mount(run, "/var/run"),
        ],
    )


def _dhcp_replica(pod: _Pod, prefix: str, *, data: str, serve_port: int) -> None:
    conf = pod.volume(f"{prefix}-conf")
    static = pod.volume(f"{prefix}-static")
    generated = pod.volume(f"{prefix}-generated")
    run = pod.volume(f"{prefix}-run")
    leases = pod.volume(f"{prefix}-leases")
    pod.files[f"/stack/{conf}/00-setup.conf"] = DHCP_CONF
    pod.files[f"/stack/{leases}/dnsmasq.leases"] = SEEDED_LEASE
    pod.generate(
        f"{prefix}-generate", static, DHCP_CONFIG_TARGET="/mnt/target/dhcp-hosts"
    )
    pod.container(
        f"{prefix}-dnsmasq",
        pod.images.dnsmasq,
        env=_env(
            TINI_SUBREAPER="true",
            DHCP_RENEWAL_ENDPOINT=f"http://localhost:{CONTROL_PORT}/lease-change",
        ),
        securityContext={"capabilities": {"add": ["NET_BIND_SERVICE", "NET_ADMIN"]}},
        readinessProbe=_exec_probe(DHCP_READINESS_COMMAND),
        volumeMounts=[
            _mount(conf, "/etc/dnsmasq.d", read_only=True),
            _mount(generated, "/etc/dnsmasq-generated.d", read_only=True),
            _mount(static, "/etc/dnsmasq-static-generated.d", read_only=True),
            _mount(leases, "/var/lib/dnsmasq"),
            _mount(run, "/var/run"),
        ],
    )
    pod.container(
        f"{prefix}-serve",
        pod.images.config_generator,
        command=["python", "main.py", "serve"],
        env=_env(
            TINI_SUBREAPER="true",
            DYNAMIC_HOSTS="/mnt/dynamic/reservations/state.yaml",
            DHCP_CONFIG_TARGET="/mnt/target/dhcp-hosts",
            DOMAIN="home",
            DHCP_TAG="intranet",
            PORT=str(serve_port),
        ),
        volumeMounts=[
            _mount(data, "/mnt/dynamic", read_only=True),
            _mount(generated, "/mnt/target"),
            _mount(run, "/var/run"),
        ],
    )


@dataclass(frozen=True)
class Stack:
    job: str
    pod: str
    ip: str
    control: Control

    def url(self, port: int, path: str) -> str:
        return f"http://{self.ip}:{port}{path}"


def wait_serving(stack: Stack, port: int, path: str, timeout: float = 60.0) -> None:
    """Until the HTTP service on `port` answers `path` with a 200."""

    def ok() -> bool | None:
        try:
            return requests.get(stack.url(port, path), timeout=2.0).ok or None
        except requests.ConnectionError:
            return None

    wait_for(ok, timeout, f"{path} on :{port} in {stack.pod}")


@dataclass(frozen=True)
class Kind:
    pod: Callable[[Images], dict[str, Any]]
    wait_ready: Callable[[Stack], None]


# ---- bare: the dnsmasq image on its own -------------------------------------


def _bare(images: Images) -> dict[str, Any]:
    pod = _Pod(images)
    for volume in ("static", "generated", "run"):
        pod.volume(volume)
    pod.container(
        "dnsmasq",
        images.dnsmasq,
        env=_env(TINI_SUBREAPER="true"),
        readinessProbe=_exec_probe(DNS_LIVENESS_COMMAND),
        volumeMounts=[
            _mount("static", "/etc/dnsmasq-static-generated.d"),
            _mount("generated", "/etc/dnsmasq-generated.d"),
            _mount("run", "/var/run"),
        ],
    )
    return pod.spec()


def _bare_ready(stack: Stack) -> None:
    dnsquery.wait_up(stack.ip, 53)


# ---- dns: one DNS replica ----------------------------------------------------

DNS_SERVE_PORT = 9000


def _dns(images: Images) -> dict[str, Any]:
    pod = _Pod(images)
    pod.volume("static-hosts")
    pod.files["/stack/static-hosts/static-hosts.yaml"] = STATIC_HOSTS
    data = pod.volume("data")
    _dns_replica(
        pod,
        "dns",
        data=data,
        dns_port=53,
        serve_port=DNS_SERVE_PORT,
        held=True,
    )
    return pod.spec()


def _dns_ready(stack: Stack) -> None:
    dnsquery.wait_up(stack.ip, 53)
    wait_serving(stack, DNS_SERVE_PORT, "/ready")


# ---- reader: `serve` with no dnsmasq, as DHCPApp's pod runs it ---------------

READER_SERVE_PORT = 9000


def _reader(images: Images) -> dict[str, Any]:
    pod = _Pod(images, share_processes=False)
    pod.volume("static-hosts")
    pod.files["/stack/static-hosts/static-hosts.yaml"] = STATIC_HOSTS
    static = pod.volume("static")
    data = pod.volume("data")
    generated = pod.volume("generated")
    pod.generate("generate", static, DHCP_CONFIG_TARGET="/mnt/target/dhcp-hosts")
    # A native sidecar whose startup probe holds back the pod's other
    # containers, as in `dhcp-app-deployment.yaml`.
    pod.init_container(
        "serve",
        images.config_generator,
        command=["python", "main.py", "serve"],
        restartPolicy="Always",
        env=_env(
            DYNAMIC_HOSTS="/mnt/dynamic/reservations/state.yaml",
            DHCP_CONFIG_TARGET="/mnt/target/dhcp-hosts",
            DOMAIN="home",
            DHCP_TAG="intranet",
            DNSMASQ_PID_FILE="",
            PORT=str(READER_SERVE_PORT),
        ),
        startupProbe={
            "httpGet": {"path": "/ready", "port": READER_SERVE_PORT},
            "periodSeconds": 1,
            "failureThreshold": 30,
        },
        volumeMounts=[
            _mount(data, "/mnt/dynamic", read_only=True),
            _mount(generated, "/mnt/target"),
        ],
    )
    # DHCPApp's own container, which that startup probe holds back.
    pod.container("app", images.config_generator, command=["sleep", "infinity"])
    return pod.spec()


def _reader_ready(stack: Stack) -> None:
    wait_serving(stack, READER_SERVE_PORT, "/ready")


# ---- chain: the management API driving two DNS replicas and a DHCP one -------
#
# Pods share no storage, while the management API's reservation file must be
# the file the generators read; so the chain is one pod, each replica's
# dnsmasq and `serve` on ports of their own.

CHAIN_DNS = {"dns0": (53, 9000), "dns1": (5354, 9001)}
CHAIN_DHCP_SERVE_PORT = 9002


def _chain(images: Images) -> dict[str, Any]:
    pod = _Pod(images)
    pod.volume("static-hosts")
    pod.files["/stack/static-hosts/static-hosts.yaml"] = STATIC_HOSTS
    data = pod.volume("data")
    for prefix, (dns_port, serve_port) in CHAIN_DNS.items():
        _dns_replica(pod, prefix, data=data, dns_port=dns_port, serve_port=serve_port)
    _dhcp_replica(pod, "dhcp", data=data, serve_port=CHAIN_DHCP_SERVE_PORT)
    dns0, dns1 = (f"http://localhost:{port}/refresh" for _, port in CHAIN_DNS.values())
    pod.container(
        "management-api",
        images.management_api,
        env=_env(
            PORT=str(MANAGEMENT_PORT),
            RESERVATION_CIDR=RESERVATION_CIDR,
            DATA="/data",
            AUTH_TOKEN=AUTH_TOKEN,
            DNS0_ENDPOINT=dns0,
            DNS1_ENDPOINT=dns1,
            DHCP_ENDPOINT=f"http://localhost:{CHAIN_DHCP_SERVE_PORT}/refresh",
            REFRESH_TIMEOUT_SECONDS="30",
        ),
        volumeMounts=[_mount(data, "/data")],
    )
    return pod.spec()


def _chain_ready(stack: Stack) -> None:
    for dns_port, serve_port in CHAIN_DNS.values():
        dnsquery.wait_up(stack.ip, dns_port)
        wait_serving(stack, serve_port, "/ready")
    wait_serving(stack, CHAIN_DHCP_SERVE_PORT, "/ready")
    wait_serving(stack, MANAGEMENT_PORT, "/healthz")


KINDS = {
    "bare": Kind(_bare, _bare_ready),
    "dns": Kind(_dns, _dns_ready),
    "reader": Kind(_reader, _reader_ready),
    "chain": Kind(_chain, _chain_ready),
}


def _job(name: str, run: str, pod: dict[str, Any]) -> dict[str, Any]:
    labels = {
        "app.kubernetes.io/name": "dnsmasq-integration-tests",
        "app.kubernetes.io/instance": run,
    }
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "labels": labels},
        "spec": {
            "activeDeadlineSeconds": ACTIVE_DEADLINE_SECONDS,
            "ttlSecondsAfterFinished": TTL_SECONDS_AFTER_FINISHED,
            # Under `OnFailure` a container restart counts against this; one
            # test restarts a container on purpose.
            "backoffLimit": 6,
            "template": {"metadata": {"labels": labels}, "spec": pod},
        },
    }


class Launcher:
    """Starts stacks as Jobs, hands them out once ready, and deletes them."""

    def __init__(self, cluster: Cluster, images: Images) -> None:
        self._cluster = cluster
        self._images = images
        # Job names are unique per run, so runs never collide in a namespace.
        self.run = secrets.token_hex(3)
        self._jobs: dict[str, str] = {}
        self._ready: dict[str, Stack] = {}

    def start(self, kind: str) -> None:
        job = f"dnsmasq-it-{self.run}-{kind}"
        self._cluster.create_job(_job(job, self.run, KINDS[kind].pod(self._images)))
        self._jobs[kind] = job

    def ready(self, kind: str) -> Stack:
        if kind not in self._ready:
            job = self._jobs[kind]
            pod = self._cluster.wait_running(job, timeout=180.0)
            stack = Stack(
                job, pod.name, pod.ip, Control(f"http://{pod.ip}:{CONTROL_PORT}")
            )
            stack.control.wait_healthy(timeout=60.0)
            KINDS[kind].wait_ready(stack)
            self._ready[kind] = stack
        return self._ready[kind]

    def delete_all(self) -> None:
        for job in self._jobs.values():
            self._cluster.delete_job(job)
        for job in self._jobs.values():
            self._cluster.wait_deleted(job)
