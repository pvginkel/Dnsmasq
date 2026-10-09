#!/bin/sh

if [ -n "$DHCP_RENEWAL_ENDPOINT" ]; then
    # --max-time: dnsmasq runs this synchronously for every lease event, so an
    # unbounded curl lets a wedged endpoint stall DHCP. The endpoint is DHCPApp
    # in another pod, reached over its Service.
    curl -s --max-time 5 -d '' "$DHCP_RENEWAL_ENDPOINT" > /dev/null
fi
