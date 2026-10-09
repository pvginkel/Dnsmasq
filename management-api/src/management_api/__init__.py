"""dnsmasq's management API.

It owns one dedicated /24 and hands out `(hostname, MAC, IPv4)` reservations
from it: the client names the host and its MAC, the API picks the address. The
reservation set is persisted as `${DATA}/reservations/state.yaml`, the file the
config generators read as their dynamic hosts. After every change the API
POSTs `/refresh` to each generator, which re-renders and SIGHUPs its dnsmasq.
"""
