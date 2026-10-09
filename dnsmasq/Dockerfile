# Built from the repository root, as the workspace's images are:
#   kaniko --context . --dockerfile dnsmasq/Dockerfile

FROM alpine

RUN apk add --no-cache dnsmasq tini curl && \
    \
    sed -i "s/local-service/#local-service/" "/etc/dnsmasq.conf" && \
    echo "addn-hosts=/etc/dnsmasq-static-generated.d/hosts" >> "/etc/dnsmasq.conf" && \
    echo "dhcp-hostsfile=/etc/dnsmasq-static-generated.d/dhcp-hosts" >> "/etc/dnsmasq.conf" && \
    echo "conf-dir=/etc/dnsmasq-static-generated.d/,*.conf" >> "/etc/dnsmasq.conf" && \
    echo "addn-hosts=/etc/dnsmasq-generated.d/hosts" >> "/etc/dnsmasq.conf" && \
    echo "dhcp-hostsfile=/etc/dnsmasq-generated.d/dhcp-hosts" >> "/etc/dnsmasq.conf" && \
    echo "dhcp-script=/scripts/dhcp-script.sh" >> "/etc/dnsmasq.conf" && \
    echo "script-on-renewal" >> "/etc/dnsmasq.conf" && \
    \
    mkdir /etc/dnsmasq-generated.d && \
    mkdir /etc/dnsmasq-static-generated.d

EXPOSE 53/tcp 53/udp 67/udp

COPY dnsmasq/files/dhcp-script.sh /scripts/

ENTRYPOINT ["tini", "--"]

CMD ["dnsmasq", "-k"]
