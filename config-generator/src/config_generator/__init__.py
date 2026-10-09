"""dnsmasq's config generator.

Two commands, one image:

    python main.py generate   # one-shot: render the static config, then exit
    python main.py serve      # long-running: render the dynamic config on demand

`generate` runs as an init container. It reads the operator-curated
`static-hosts.yaml` and writes the static addn-hosts / dhcp-hostsfile / cname
config into the shared volume before dnsmasq boots. The static set only
changes on redeploy, so there is nothing to reload.

`serve` runs as a long-lived sidecar. It owns the *dynamic* config: Kubernetes
Service records (watched directly) and reservation records (pushed by the
management API via POST /refresh). On any change it rewrites the dynamic files
and SIGHUPs dnsmasq.

Not every consumer of the rendered config is dnsmasq: DHCPApp reads the same
files to report which leases are reservations, and runs its own `serve` in its
own pod. DNSMASQ_PID_FILE is the empty string there — the files are still
written, and there is nothing to signal.
"""
