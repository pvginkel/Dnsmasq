# DNS reservation API

Specification for dnsmasq's management API, which adds, looks up, and removes DNS+DHCP reservations within an IPv4 range owned exclusively by this service. Covers the contract only; persistence layout, dnsmasq config rendering, and reload mechanics are implementation choices left to the service.

Its client is Terraform, through the homelab provider's `homelab_dns_reservation` resource; this spec is the contract between them.

## Scope

- Manages **dynamic** reservations: entries created and destroyed in lockstep with infrastructure provisioning.
- Owns a single dedicated IPv4 range (e.g. `192.0.2.0/24`) configured at deploy time. The API **allocates** an address from that range for each new hostname; clients do not pick the IP.
- Each reservation is a triple: hostname, MAC, IPv4. IPv6 and DNS-only entries are not supported here.
- Operator-curated static reservations (printers, IoT, network gear) are handled by a separate component and live outside this CIDR. This API does not see, validate against, or merge with the static set; the config generator renders both side by side for dnsmasq.

## Resource model

A reservation is identified by **hostname**, which is the URL key. MAC is a client-supplied attribute. IPv4 is **allocated by the API** from the configured CIDR and bound to the hostname for its lifetime — updating a hostname's MAC keeps the same IP; only `DELETE` releases it.

Why hostname as the ID:
- Already unique by DNS contract.
- Stable across MAC rotations (the MAC can change; the hostname and IP do not).
- The natural label for humans and for Terraform resource names.

## Endpoints

All paths under `/reservations`. JSON request and response bodies; UTF-8.

### `PUT /reservations/{hostname}`

Idempotent create-or-update. Body:

```json
{ "mac": "02:00:00:00:01:01" }
```

Behaviour:
- New hostname → API allocates the lowest free address from the configured CIDR and stores the reservation.
- Existing hostname, same MAC → no-op; returns the current reservation.
- Existing hostname, different MAC → MAC is updated; **the IPv4 is preserved**.

Responses:

- `201 Created` — reservation did not exist; created.
- `200 OK` — reservation existed and was updated, or already in the requested state.
- `400 Bad Request` — malformed body, invalid MAC, invalid hostname.
- `401 Unauthorized` — missing or invalid token.
- `409 Conflict` — see error codes.

Body of `200`/`201`:

```json
{ "hostname": "vm01", "mac": "02:00:00:00:01:01", "ipv4": "192.0.2.27" }
```

### `GET /reservations/{hostname}`

Read a single reservation.

- `200 OK` with reservation body.
- `400 Bad Request` — invalid hostname.
- `404 Not Found` — no reservation under this hostname.
- `401 Unauthorized`.

### `DELETE /reservations/{hostname}`

Remove a reservation. The IPv4 is released back to the allocation pool and may be reused on subsequent `PUT`s.

- `204 No Content` — removed.
- `400 Bad Request` — invalid hostname.
- `404 Not Found` — no reservation under this hostname.
- `401 Unauthorized`.

### `GET /reservations`

List all reservations. Provided for human inspection only; **Terraform does not call this** (see *Out of scope*).

- `200 OK`:
  ```json
  { "reservations": [ { "hostname": "...", "mac": "...", "ipv4": "..." }, ... ] }
  ```
  No pagination — fleet is small.
- `401 Unauthorized`.

### `GET /healthz`

Unauthenticated liveness probe. `200 OK` with `{"status":"ok"}` whenever the API serves traffic.

## Conflict semantics

- A `PUT` re-asserting an existing reservation's current MAC is fine (idempotent, returns `200`).
- A `PUT` whose MAC is held by a **different** hostname → `409 mac_conflict`.
- A `PUT` for a new hostname when every address in the configured CIDR is already taken → `409 ipv4_exhausted`. Updates to existing hostnames continue to succeed since they don't consume a new address.

The MAC check is "different hostname" — updating a reservation's own MAC in place is allowed.

There is no client-facing IPv4 conflict: the API picks the IP, so duplicate-IP collisions cannot arise from client input.

## Errors

Error responses share a JSON envelope:

```json
{ "error": "mac_conflict", "message": "MAC 02:00:00:00:01:01 is already reserved for vm02." }
```

| Code | Status | Meaning |
|---|---|---|
| `bad_request` | 400 | Malformed body or missing `mac` field. |
| `invalid_mac` | 400 | MAC fails format check. |
| `invalid_hostname` | 400 | Hostname (URL segment) fails format check. |
| `unauthorized` | 401 | Missing or invalid bearer token. |
| `not_found` | 404 | (`GET`/`DELETE`) no reservation under this hostname. |
| `mac_conflict` | 409 | MAC reserved by another hostname. |
| `ipv4_exhausted` | 409 | No free addresses left in the configured CIDR. |
| `internal` | 500 | Anything else; persistence-store failures, unexpected errors. |

## Validation

- **MAC**: matches `^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$`. Stored and returned uppercase; the API normalizes on input.
- **Hostname**: matches `^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$`. Lowercase only. No FQDN — the domain is appended by dnsmasq.
- **IPv4**: never accepted from the client. Always allocated by the API from the configured CIDR.

## Allocation

- The configured range is a single `/24`.
- Allocatable host addresses are `<network>.1` through `<network>.254`. `.0` (network) and `.255` (broadcast) are not handed out.
- Allocation order is deterministic: the lowest free address in ascending order. New reservations therefore fill the range from the bottom up; freed addresses are reused on the next `PUT` that needs one.

## Auth

Bearer token: `Authorization: Bearer <token>`. Single shared secret to start; one token, all endpoints. The token is delivered to the API via Helm/Secret. Rotation is a Helm-side operation and out of scope for this spec.

`/healthz` is the only unauthenticated endpoint.

## Persistence

The API persists reservations across restarts. Format and storage are implementation choices, with three contractual guarantees:

- A restart must not drop or corrupt entries; an uncommitted in-flight write is the only allowable loss.
- A clean shutdown must flush before exit.
- Concurrent writes are serialized. Single-writer is acceptable; this is not a hot path.

## Reload

After `PUT` or `DELETE` returns 2xx, the change is durable in the persistent store. The API additionally pushes the change to the config generators, which reload dnsmasq, before responding; under normal conditions this completes in well under a second. The API does **not** gate its 2xx response on propagation succeeding — a `2xx` always means "durable", not "applied". If propagation fails (transient network issue, dnsmasq unhealthy), it is logged and dnsmasq will converge on its own at the next successful operation or pod restart.

## Out of scope

- **No bulk / list-then-reconcile endpoint.** Each Terraform resource manages its own entry by id. A "set the full set of reservations to this list" endpoint would invite a future client that nukes entries created by other tools, and is explicitly rejected by this design.
- **No client-supplied IPv4.** The API owns the range and the allocation policy. Callers that need a specific address belong elsewhere.
- **No IPv6.** Managed VMs are IPv4-only on `vmbr0`.
- **No DNS-only reservations.** A reservation is always (hostname, MAC, IPv4). Hostnames that need only an A record (no DHCP) are handled by the operator-curated static config, outside this API.
- **No CNAME, SRV, TXT, or other record types.**
- **No reservation history or audit log.** If needed later, it lives behind a different endpoint.
- **No API versioning.** Single internal client. If a breaking change is ever needed, version the path then.
