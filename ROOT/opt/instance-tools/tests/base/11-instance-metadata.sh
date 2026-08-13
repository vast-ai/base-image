#!/bin/bash
# Test: instance identity, and a best-effort metadata cache for later tests.
#
# WHAT THIS ASSERTS, AND WHAT IT DELIBERATELY DOES NOT.
#
# Hard assertions are IMAGE properties only: the identity variables the platform
# injects are present, and the vastai CLI we ship is installed and runnable.
#
# The API round-trip is NOT an assertion. `vastai show instance` reaches
# console.vast.ai from inside a rented box, and this repo has three separate
# records of that call being unreliable — ADR 0005 condition 6 (single-key,
# 429-prone QA account), qa-gate.yml's note that concurrent QA *does* 429 that
# account (observed live, at max-parallel 4), and base-qa's own reliability2
# floor, added because hosts with no outbound egress were being rented.
#
# Under the promotion gate a failing test blocks, and a block is deliberately
# not retried — so hard-failing here would convert a platform rate limit into a
# held customer-facing -auto tag needing a second production approval to clear.
# That is the precise failure this suite's own rule forbids: fail on a bad
# IMAGE, never on a bad host. So the round-trip retries briefly, then warns.
#
# This file was ALSO not executable until 2026-08-13 and had therefore never run
# once since it was written — which is why /tmp/instance-test-metadata.json has
# never existed and lib.sh's instance_field() has always returned empty. Enabling
# it and hard-failing on the API in the same change would have traded a silent
# no-op for a fleet-wide flake.
source "$(dirname "$0")/../lib.sh"

METADATA_FILE="/tmp/instance-test-metadata.json"

# ── Image-side facts: these are ours, so these are hard ───────────────

[[ -n "${CONTAINER_ID:-}" ]] || test_fail "CONTAINER_ID is not set — instance has no identity"
[[ -n "${CONTAINER_API_KEY:-}" ]] || test_fail "CONTAINER_API_KEY is not set — cannot authenticate with API"
echo "  CONTAINER_ID=${CONTAINER_ID}"

command -v vastai &>/dev/null || test_fail "vastai command not found"
# The CLI must at least be runnable — this catches the dependency-drift class
# (a resolver change breaking an import) without depending on the network.
vastai --help >/dev/null 2>&1 || test_fail "vastai is installed but will not run (--help failed)"

# ── Platform-side: best effort, never a failure ───────────────────────

raw=""
for attempt in 1 2 3; do
    raw=$(vastai show instance "$CONTAINER_ID" --api-key "$CONTAINER_API_KEY" --raw 2>&1)
    if [[ $? -eq 0 && -n "$raw" ]] && echo "$raw" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
        break
    fi
    raw=""
    [[ "$attempt" -lt 3 ]] && sleep 5
done

if [[ -z "$raw" ]]; then
    echo "  WARN: could not retrieve instance metadata from the API after 3 attempts"
    echo "        (rate limit, egress or console availability — not an image fault)."
    echo "        Tests calling instance_field() will see empty values."
    test_pass "instance identity verified (API metadata unavailable)"
fi

echo "$raw" > "$METADATA_FILE"

python3 -c "
import json, sys
d = json.load(sys.stdin)
fields = [
    ('status', 'cur_state'),
    ('gpu', 'gpu_name'),
    ('num_gpus', 'num_gpus'),
    ('driver', 'driver_version'),
    ('cuda_max', 'cuda_max_good'),
    ('cpu', 'cpu_name'),
    ('ram_mb', 'cpu_ram'),
    ('disk_gb', 'disk_space'),
    ('image', 'image_uuid'),
    ('external', 'external'),
]
for label, key in fields:
    val = d.get(key, 'N/A')
    print(f'  {label}: {val}')
" <<< "$raw"

# A state other than 'running' is the platform's report about itself, not a
# statement about the image — worth seeing, never worth holding a release for.
state=$(echo "$raw" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cur_state',''))")
[[ "$state" == "running" ]] || echo "  WARN: API reports instance state as '${state}', expected 'running'"

test_pass "instance metadata retrieved (id: ${CONTAINER_ID})"
