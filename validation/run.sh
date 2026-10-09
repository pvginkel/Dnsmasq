#!/bin/sh
# The validation image's entrypoint: the two packages' unit tests and the
# integration tier, each pytest run from its own directory, the only place its
# config applies. Every suite runs, whatever the others did. Then, per suite,
# the log carries
#   ===SUITE_RESULT:<suite>:<passed>:<failed>:<skipped>===
#   ===JUNIT:<suite>.xml===
#   <the suite's JUnit file, base64-encoded>
#   ===JUNIT_END===
# which the Jenkinsfile's Test stage reads back. Exits 1 when a suite failed.
#
# The tier's environment (STACK_NAMESPACE and the three image references) comes
# from the Job; tests/conftest.py names it.

set -u

root=$(cd "$(dirname "$0")/.." && pwd)
results=$(mktemp -d)
status=0

run() {
    echo "=== $1 ==="
    (cd "$root/$2" && pytest -o junit_suite_name="$1" --junitxml="$results/$1.xml") || status=1
}

summary() {
    python - "$1" <<'EOF'
import sys
import xml.etree.ElementTree as ET

suite = ET.parse(sys.argv[1]).getroot().find("testsuite")
tests = int(suite.get("tests"))
failed = int(suite.get("failures")) + int(suite.get("errors"))
skipped = int(suite.get("skipped"))
print(f"{tests - failed - skipped}:{failed}:{skipped}")
EOF
}

run config-generator config-generator
run management-api management-api
run integration-tests tests

# A suite whose pytest never started wrote no JUnit file; its failure is in the
# log above.
for suite in config-generator management-api integration-tests; do
    xml="$results/$suite.xml"
    [ -f "$xml" ] || continue
    echo "===SUITE_RESULT:$suite:$(summary "$xml")==="
    echo "===JUNIT:$suite.xml==="
    base64 "$xml"
    echo "===JUNIT_END==="
done

exit "$status"
