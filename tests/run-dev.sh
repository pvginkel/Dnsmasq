#!/bin/sh
# The tier from a KubeCoder environment: build the three images from the working
# tree, push them under this environment's own tag, and run the tier against
# them in `development`. Arguments go to pytest.
#
# The tag is `<environment id>-dev`, overwritten by every run: never `latest` or
# a bare number, the series the Jenkins job pins and registry cleanup ranks.

set -eu

cd "$(dirname "$0")/.."

tag="${KUBECODER_ENVIRONMENT_ID:?}-dev"
log=$(mktemp)
trap 'rm -f "$log"' EXIT

build() {
    if ! kaniko --context . --dockerfile "$2/Dockerfile" --destination "registry:5000/$1:$tag" >"$log" 2>&1; then
        cat "$log"
        exit 1
    fi
    echo "registry:5000/$1:$tag"
}

dnsmasq=$(build dnsmasq dnsmasq)
config_generator=$(build dnsmasq-config-generator config-generator)
management_api=$(build dnsmasq-management-api management-api)

cd tests
exec cexec modern-app env \
    STACK_NAMESPACE=development \
    DNSMASQ_IMAGE="$dnsmasq" \
    CONFIG_GENERATOR_IMAGE="$config_generator" \
    MANAGEMENT_API_IMAGE="$management_api" \
    uv run --locked pytest "$@"
