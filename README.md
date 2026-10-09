# Dnsmasq

dnsmasq for a homelab: the dnsmasq image, the config generator that renders its DNS and DHCP
configuration, the management API that edits its DHCP reservations, and the tests that cover them.

## Layout

| Directory | What it is |
|---|---|
| `dnsmasq/` | The `dnsmasq` image: alpine's dnsmasq with the generated-config paths in its `dnsmasq.conf`, and `files/dhcp-script.sh`, which POSTs every lease change to `DHCP_RENEWAL_ENDPOINT` (DHCPApp's `/internal/notify-lease-change`). |
| `config-generator/` | The `dnsmasq-config-generator` image, package `config_generator`. `python main.py generate` renders the static config once, as an init container. `python main.py serve`, the default, keeps the dynamic config current: the management API's reservations, re-rendered on its `/refresh`, and, with the Service watch configured, Kubernetes Service records. It SIGHUPs the dnsmasq beside it; with `DNSMASQ_PID_FILE` empty, as DHCPApp runs it, there is none and it only writes the files. |
| `management-api/` | The `dnsmasq-management-api` image, package `management_api`: hands out DHCP reservations from a dedicated `/24`. See its [README](management-api/README.md). |
| `tests/` | The integration tier: what only the built images show. |
| `validation/` | The `dnsmasq-validation` image, which the Jenkins build's `Test` stage runs. |
| `docs/architecture/` | The two apps' architecture models: this repo is the architecture producer `dnsmasq`. |

`config-generator`, `management-api` and `tests` are members of one uv workspace with one
`uv.lock`. Every image builds with the repository root as its context, for example
`kaniko --context . --dockerfile config-generator/Dockerfile`.

## Development

`kc project test` and `kc project lint`, run from the repository root, run every component's
gate; `--project <component>` runs one. The two packages' tests run in process. The integration
tier (`tests/run-dev.sh`) builds the three images from the working tree with kaniko, pushes them
as `registry:5000/<image>:<environment id>-dev`, and runs each stack it needs as a one-pod Job in
the `development` namespace. `validation/run-dev.sh` builds the validation image too and runs it
as a Job in `development`, as the Jenkins build's `Test` stage runs it.

The repository is public: no production address, host name or MAC goes into it, tests and commit
messages included.

## Build and deploy

A push to `main` runs the `Dnsmasq/Dnsmasq` Jenkins job (`Jenkinsfile`). It builds the three
images, tagged with the build number and `latest`, then runs the validation image as a Job: the
two packages' tests and the integration tier, against this build's tags. When they pass, it pins
those tags into `pvginkel/DnsmasqDeploy`'s `config/prd/values.yaml`, which Argo CD syncs to
production. A push to `main` deploys.

`Jenkinsfile.architecture` (job `AaC/Dnsmasq`) validates the architecture models and archives them
for the federated architecture model to collect.
