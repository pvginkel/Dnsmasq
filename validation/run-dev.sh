#!/bin/sh
# The Jenkins build's Test stage, from a KubeCoder environment: build the three
# images and the validation image from the working tree, run the validation
# image as a Job in `development`, and check what the Test stage reads back
# from its log: a SUITE_RESULT line and a JUnit file for each of the three
# suites, and the container's exit code.
#
# The pod runs as `claude-code`, since `default` holds no grant in
# `development`. That grant is wider than the Jenkins agent's Role, so this run
# proves the image and its wiring, not the grant.
#
# The tags are `<environment id>-dev`, as tests/run-dev.sh builds them: never
# `latest` or a bare number, the series the Jenkins job pins and registry
# cleanup ranks.

set -eu

cd "$(dirname "$0")/.."

tag="${KUBECODER_ENVIRONMENT_ID:?}-dev"
job="dnsmasq-validation-$(od -An -N3 -tx1 /dev/urandom | tr -d ' \n')"
work=$(mktemp -d)

kube() {
    cexec iac kubectl -n development "$@"
}

cleanup() {
    kube delete job "$job" --cascade=foreground --ignore-not-found >/dev/null
    rm -rf "$work"
}
trap cleanup EXIT

build() {
    if ! kaniko --context . --dockerfile "$2/Dockerfile" --destination "registry:5000/$1:$tag" >"$work/build.log" 2>&1; then
        cat "$work/build.log"
        exit 1
    fi
}

build dnsmasq dnsmasq
build dnsmasq-config-generator config-generator
build dnsmasq-management-api management-api
build dnsmasq-validation validation

kube apply -f - >/dev/null <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: $job
  labels:
    app.kubernetes.io/name: dnsmasq-validation
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 900
  template:
    spec:
      restartPolicy: Never
      serviceAccountName: claude-code
      containers:
        - name: validation
          image: registry:5000/dnsmasq-validation:$tag
          imagePullPolicy: Always
          env:
            - name: STACK_NAMESPACE
              value: development
            - name: DNSMASQ_IMAGE
              value: registry:5000/dnsmasq:$tag
            - name: CONFIG_GENERATOR_IMAGE
              value: registry:5000/dnsmasq-config-generator:$tag
            - name: MANAGEMENT_API_IMAGE
              value: registry:5000/dnsmasq-management-api:$tag
EOF

# Complete or Failed comes once the pod has terminated; activeDeadlineSeconds
# bounds the wait.
finished='{.status.conditions[?(@.type=="Complete")].status}{.status.conditions[?(@.type=="Failed")].status}'
while [ -z "$(kube get job "$job" -o jsonpath="$finished")" ]; do
    sleep 2
done

pod=$(kube get pods -l "job-name=$job" -o jsonpath='{.items[*].metadata.name}')
if [ -z "$pod" ]; then
    echo "Job $job has no pod: $(kube get job "$job" -o jsonpath='{.status.conditions[?(@.type=="Failed")].reason}')"
    exit 1
fi
kube logs "$pod" -c validation >"$work/raw.log"
code=$(kube get pod "$pod" -o jsonpath='{.status.containerStatuses[0].state.terminated.exitCode}')

mkdir "$work/junit"
awk -v dir="$work/junit" '
    /^===JUNIT:.*===$/ {
        fname = $0
        sub(/^===JUNIT:/, "", fname)
        sub(/===$/, "", fname)
        content = ""
        capture = 1
        next
    }
    /^===JUNIT_END===$/ {
        print content | "base64 -d > " dir "/" fname
        close("base64 -d > " dir "/" fname)
        capture = 0
        next
    }
    capture { content = content (content ? "\n" : "") $0 }
    !capture { print }
' "$work/raw.log" >"$work/validation.log"
cat "$work/validation.log"

if [ "$code" != 0 ]; then
    echo "validation exited ${code:-without an exit code}"
    exit 1
fi
for suite in config-generator management-api integration-tests; do
    if ! grep -q "^===SUITE_RESULT:$suite:[0-9]*:0:[0-9]*===\$" "$work/validation.log"; then
        echo "no passing SUITE_RESULT line for $suite"
        exit 1
    fi
    if ! grep -q "<testsuite name=\"$suite\"" "$work/junit/$suite.xml"; then
        echo "no JUnit file for $suite"
        exit 1
    fi
done
