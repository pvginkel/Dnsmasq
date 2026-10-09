# management-api

HTTP API that owns one dedicated IPv4 `/24` and hands out `(hostname, mac, ipv4)` reservations
from it. Its client is Terraform, through the homelab provider's `homelab_dns_reservation`
resource.

Every change is persisted to `${DATA}/reservations/state.yaml` in the shape of the config
generator's `static-hosts.yaml`, because the generators read it as their dynamic hosts file. The
API then POSTs `/refresh` to each config generator in turn — DNS0, DNS1, DHCP, then DHCPApp — and
each re-renders its dynamic config and SIGHUPs its dnsmasq. A refresh that fails is logged and
does not fail the request: the change is already on disk.

## API

The full public contract is in [`dns-reservation-api.md`](dns-reservation-api.md). Summary:

| Method | Path | Purpose |
|---|---|---|
| `PUT` | `/reservations/{hostname}` | Idempotent create-or-update; API allocates the IPv4. |
| `GET` | `/reservations/{hostname}` | Read a single reservation. |
| `DELETE` | `/reservations/{hostname}` | Remove a reservation; releases the IPv4. |
| `GET` | `/reservations` | List all reservations. |
| `GET` | `/healthz` | Unauthenticated liveness probe. |

All endpoints except `/healthz` require `Authorization: Bearer <AUTH_TOKEN>`.

## Configuration

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `PORT` | no | `8080` | HTTP listen port. |
| `RESERVATION_CIDR` | yes | — | CIDR allocations come from. Must be a `/24`. |
| `DATA` | yes | — | Data directory, a CephFS volume in production. State at `${DATA}/reservations/state.yaml`. |
| `AUTH_TOKEN` | yes | — | Single bearer token for all authed endpoints. |
| `DNS0_ENDPOINT` | yes | — | URL of the first DNS replica's `/refresh`. |
| `DNS1_ENDPOINT` | no | — | URL of the second DNS replica's `/refresh`. |
| `DHCP_ENDPOINT` | no | — | URL of the DHCP instance's `/refresh`. |
| `DHCP_APP_ENDPOINT` | no | — | URL of DHCPApp's `/refresh`. It renders the same reservations to report which leases are reserved. |
| `REFRESH_TIMEOUT_SECONDS` | no | `30` | Per-endpoint HTTP timeout for the fan-out. |

An empty variable counts as unset. A missing required variable, a `RESERVATION_CIDR` that is not
an IPv4 `/24` network, or a `state.yaml` that is not a mapping stops the service at startup with
exit code 1.

The refresh calls are sequential, so a `PUT` or `DELETE` whose endpoints all stall takes up to
`REFRESH_TIMEOUT_SECONDS` per configured endpoint.

## Persistence

`state.yaml` is replaced, never edited: the new state is written to `state.yaml.tmp`, the current
file becomes `state.yaml.bak`, and the `.tmp` takes its name. At startup, before the listener
binds, a leftover `.tmp` is deleted and a `.bak` with no `state.yaml` beside it is promoted.

## Image

Python, Flask and Waitress on port 8080, with `tini` as PID 1. The default command is
`python main.py`, which the deploy chart runs. The image builds from the repository root, because
its build stage installs from the workspace's `uv.lock`:

```bash
kaniko --context . --dockerfile management-api/Dockerfile
```

## Development

The package is `management_api`, a member of the repository's uv workspace. Its gate runs from
the repository root:

```bash
kc project test
kc project lint
```

The tests run the app in-process through Flask's test client, with the config generators'
`/refresh` endpoints as fakes in the same process (`tests/fake_refresh.py`). `tests/test_startup.py`
also starts `python main.py` itself, on Waitress.
