#!/bin/bash
# Test: instance identity and API connectivity.
# Queries the Vast API for instance metadata and saves it for later tests.
source "$(dirname "$0")/../lib.sh"

METADATA_FILE="/tmp/instance-test-metadata.json"

# CONTAINER_ID and CONTAINER_API_KEY must always be set
[[ -n "${CONTAINER_ID:-}" ]] || test_fail "CONTAINER_ID is not set — instance has no identity"
[[ -n "${CONTAINER_API_KEY:-}" ]] || test_fail "CONTAINER_API_KEY is not set — cannot authenticate with API"

echo "  CONTAINER_ID=${CONTAINER_ID}"

# vastai CLI must exist
command -v vastai &>/dev/null || test_fail "vastai command not found"

# Query the API for instance metadata
raw=$(vastai show instance "$CONTAINER_ID" --api-key "$CONTAINER_API_KEY" --raw 2>&1)
if [[ $? -ne 0 ]] || [[ -z "$raw" ]]; then
    test_fail "vastai show instance failed: ${raw}"
fi

# Validate it's JSON
if ! echo "$raw" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
    test_fail "vastai returned invalid JSON"
fi

# Save full metadata for other tests
echo "$raw" > "$METADATA_FILE"

# Extract and display key fields
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

# Verify the API reports this instance as running
state=$(echo "$raw" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cur_state',''))")
[[ "$state" == "running" ]] || test_fail "API reports instance state as '${state}', expected 'running'"

test_pass "instance metadata retrieved (id: ${CONTAINER_ID})"
