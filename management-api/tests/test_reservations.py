"""The reservation API's contract (`dns-reservation-api.md`): create, read,
update, delete, list, validation, auth and allocation."""

from harness import ManagementApi


def _assert_fanned_out(api: ManagementApi, times: int = 1) -> None:
    """The fan-out runs inside the request, so it is complete on response."""
    assert api.hit_counts() == dict.fromkeys(api.fakes, times)


# ---- PUT new ---------------------------------------------------------------


def test_put_new_hostname_returns_201(api: ManagementApi) -> None:
    r = api.put("host-a", "02:00:00:00:00:01")
    assert r.status_code == 201
    body = r.get_json()
    assert body["hostname"] == "host-a"
    assert body["mac"] == "02:00:00:00:00:01"
    # In the configured /24 and neither its network nor its broadcast address.
    assert body["ipv4"].startswith("192.0.2.")
    last_octet = int(body["ipv4"].rsplit(".", 1)[1])
    assert 1 <= last_octet <= 254
    _assert_fanned_out(api)


def test_put_normalises_mac_to_uppercase(api: ManagementApi) -> None:
    r = api.put("host-a", "02:ab:cd:ef:00:01")
    assert r.status_code == 201
    assert r.get_json()["mac"] == "02:AB:CD:EF:00:01"
    r2 = api.get("host-a")
    assert r2.status_code == 200
    assert r2.get_json()["mac"] == "02:AB:CD:EF:00:01"


# ---- PUT no-op / update ----------------------------------------------------


def test_put_same_hostname_same_mac_is_noop(api: ManagementApi) -> None:
    r1 = api.put("host-a", "02:AB:CD:EF:00:01")
    assert r1.status_code == 201
    ipv4 = r1.get_json()["ipv4"]
    _assert_fanned_out(api)
    api.reset_fakes()

    # The MAC differs only in case, which normalisation erases.
    r2 = api.put("host-a", "02:ab:cd:ef:00:01")
    assert r2.status_code == 200
    assert r2.get_json()["ipv4"] == ipv4
    _assert_fanned_out(api, times=0)


def test_put_same_hostname_new_mac_keeps_ipv4(api: ManagementApi) -> None:
    ipv4 = api.put("host-a", "02:00:00:00:00:01").get_json()["ipv4"]
    api.reset_fakes()

    r2 = api.put("host-a", "02:00:00:00:00:99")
    assert r2.status_code == 200
    body = r2.get_json()
    assert body["ipv4"] == ipv4
    assert body["mac"] == "02:00:00:00:00:99"
    _assert_fanned_out(api)


# ---- PUT conflict ----------------------------------------------------------


def test_put_new_hostname_with_conflicting_mac_409(api: ManagementApi) -> None:
    api.put("host-a", "02:AB:CD:EF:00:01")
    api.reset_fakes()

    # The same MAC in the other case: the check compares the normalised MAC.
    r = api.put("host-b", "02:ab:cd:ef:00:01")
    assert r.status_code == 409
    assert r.get_json() == {
        "error": "mac_conflict",
        "message": "MAC 02:AB:CD:EF:00:01 is already reserved for host-a.",
    }
    assert api.get("host-b").status_code == 404
    _assert_fanned_out(api, times=0)


def test_put_existing_hostname_with_conflicting_mac_409(api: ManagementApi) -> None:
    api.put("host-a", "02:AB:CD:EF:00:01")
    api.put("host-b", "02:00:00:00:00:02")
    api.reset_fakes()

    # The same MAC in the other case: the check compares the normalised MAC.
    r = api.put("host-b", "02:ab:cd:ef:00:01")
    assert r.status_code == 409
    assert r.get_json()["error"] == "mac_conflict"
    assert api.get("host-b").get_json()["mac"] == "02:00:00:00:00:02"
    _assert_fanned_out(api, times=0)


# ---- PUT validation --------------------------------------------------------


def test_put_invalid_mac_400(api: ManagementApi) -> None:
    r = api.put("host-a", "not-a-mac")
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_mac"


def test_put_non_string_mac_400(api: ManagementApi) -> None:
    r = api.client.put("/reservations/host-a", headers=api.headers, json={"mac": 5})
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_mac"


def test_put_invalid_hostname_400(api: ManagementApi) -> None:
    r = api.put("BadHost!", "02:00:00:00:00:01")
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_hostname"


def test_put_missing_body_400(api: ManagementApi) -> None:
    r = api.client.put("/reservations/host-a", headers=api.headers)
    assert r.status_code == 400
    assert r.get_json()["error"] == "bad_request"


def test_put_missing_mac_field_400(api: ManagementApi) -> None:
    r = api.client.put(
        "/reservations/host-a", headers=api.headers, json={"not_mac": "x"}
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "bad_request"


# ---- GET -------------------------------------------------------------------


def test_get_existing_returns_200(api: ManagementApi) -> None:
    ipv4 = api.put("host-a", "02:00:00:00:00:01").get_json()["ipv4"]
    r = api.get("host-a")
    assert r.status_code == 200
    assert r.get_json() == {
        "hostname": "host-a",
        "mac": "02:00:00:00:00:01",
        "ipv4": ipv4,
    }


def test_get_missing_returns_404(api: ManagementApi) -> None:
    r = api.get("host-x")
    assert r.status_code == 404
    assert r.get_json()["error"] == "not_found"


def test_get_and_delete_check_the_hostname(api: ManagementApi) -> None:
    assert api.get("Bad_Host").get_json()["error"] == "invalid_hostname"
    assert api.delete("Bad_Host").get_json()["error"] == "invalid_hostname"


# ---- DELETE ----------------------------------------------------------------


def test_delete_existing_returns_204_and_frees_ip(api: ManagementApi) -> None:
    ipv4 = api.put("host-a", "02:00:00:00:00:01").get_json()["ipv4"]
    api.reset_fakes()

    r2 = api.delete("host-a")
    assert r2.status_code == 204
    _assert_fanned_out(api)

    assert api.get("host-a").status_code == 404

    # The freed address was the lowest allocated, so the next new hostname
    # takes it.
    r4 = api.put("host-b", "02:00:00:00:00:02")
    assert r4.status_code == 201
    assert r4.get_json()["ipv4"] == ipv4


def test_delete_missing_returns_404(api: ManagementApi) -> None:
    r = api.delete("host-x")
    assert r.status_code == 404
    assert r.get_json()["error"] == "not_found"
    _assert_fanned_out(api, times=0)


# ---- list ------------------------------------------------------------------


def test_list_returns_all_reservations(api: ManagementApi) -> None:
    api.put("bravo", "02:00:00:00:00:02")
    api.put("alpha", "02:00:00:00:00:01")

    r = api.list()
    assert r.status_code == 200
    assert r.get_json() == {
        "reservations": [
            {"hostname": "alpha", "mac": "02:00:00:00:00:01", "ipv4": "192.0.2.2"},
            {"hostname": "bravo", "mac": "02:00:00:00:00:02", "ipv4": "192.0.2.1"},
        ]
    }


# ---- auth ------------------------------------------------------------------


def test_healthz_no_auth_required(api: ManagementApi) -> None:
    r = api.client.get("/healthz")
    assert r.status_code == 200
    assert r.get_json() == {"status": "ok"}


def test_get_without_token_returns_401(api: ManagementApi) -> None:
    r = api.client.get("/reservations/host-a")
    assert r.status_code == 401
    assert r.get_json()["error"] == "unauthorized"


def test_put_with_wrong_token_returns_401(api: ManagementApi) -> None:
    r = api.client.put(
        "/reservations/host-a",
        headers={"Authorization": "Bearer wrong"},
        json={"mac": "02:00:00:00:00:01"},
    )
    assert r.status_code == 401
    assert r.get_json()["error"] == "unauthorized"
    assert api.get("host-a").status_code == 404


def test_delete_without_token_returns_401(api: ManagementApi) -> None:
    api.put("host-a", "02:00:00:00:00:01")
    r = api.client.delete("/reservations/host-a")
    assert r.status_code == 401
    assert api.get("host-a").status_code == 200


def test_list_without_token_returns_401(api: ManagementApi) -> None:
    r = api.client.get("/reservations")
    assert r.status_code == 401


# ---- allocation order ------------------------------------------------------


def test_allocation_order_is_lowest_free_ascending(api: ManagementApi) -> None:
    addresses = []
    for i in range(5):
        r = api.put(f"host-{i}", f"02:00:00:00:00:{i:02x}")
        assert r.status_code == 201
        addresses.append(r.get_json()["ipv4"])
    assert addresses == [
        "192.0.2.1",
        "192.0.2.2",
        "192.0.2.3",
        "192.0.2.4",
        "192.0.2.5",
    ]


def test_freed_ip_is_reused_at_lowest_free(api: ManagementApi) -> None:
    api.put("alpha", "02:00:00:00:00:01")  # .1
    api.put("bravo", "02:00:00:00:00:02")  # .2
    api.put("charlie", "02:00:00:00:00:03")  # .3

    api.delete("bravo")

    r = api.put("delta", "02:00:00:00:00:04")
    assert r.status_code == 201
    assert r.get_json()["ipv4"] == "192.0.2.2"


# ---- IP exhaustion ---------------------------------------------------------


def test_ip_exhaustion_returns_409(api: ManagementApi) -> None:
    # Fill .1 .. .254 — 254 hostnames.
    for i in range(1, 255):
        r = api.put(f"h{i:03d}", f"02:00:00:00:{(i >> 8) & 0xFF:02x}:{i & 0xFF:02x}")
        assert r.status_code == 201, f"PUT {i} failed: {r.status_code} {r.text}"
    assert api.list().get_json()["reservations"][-1]["ipv4"] == "192.0.2.254"

    r = api.put("overflow", "02:ff:00:00:00:00")
    assert r.status_code == 409
    assert r.get_json() == {
        "error": "ipv4_exhausted",
        "message": "No free addresses left in 192.0.2.0/24.",
    }

    # An existing hostname needs no new address, so it can still change MAC.
    r = api.put("h001", "02:ff:00:00:00:01")
    assert r.status_code == 200
    assert r.get_json()["ipv4"] == "192.0.2.1"
