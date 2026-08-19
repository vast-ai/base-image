#!/bin/bash
# Test: instance portal HTTP endpoint responds.
source "$(dirname "$0")/../lib.sh"

# Portal is not expected in serverless mode
is_serverless && test_skip "portal not expected in serverless mode"

# PORTAL_READY_TIMEOUT (default 120s, not 30s) because this is not waiting for
# one process to bind a port.
#
# The portal itself is fast: measured cold in the shipped image, `fastapi run`
# answered :11111 in 1.9s on 4 cores and 2.9s at --cpus=0.5. What 30s did not
# cover is the CHAIN in front of it. instance_portal.sh sources exit_portal.sh,
# which blocks in a `while [ ! -f /etc/portal.yaml ]` loop; that file is created
# by caddy.sh, and only AFTER caddy_config_manager.py has finished — which calls
# `caddy hash-password` (bcrypt cost 14, see http_check in lib.sh) once per
# proxied app. So the portal cannot come up until Caddy's config generation has.
#
# Measured on a QA host on 2026-08-18: this test gave up at 30s, and the portal
# was serving before the next probe 53s later — 67-service-functionality passed
# on the very same endpoints, on the same instance. The image was healthy; the
# budget was wrong, and it blocked every tag in the batch. A caddy restart on
# that box took 43s, which is the scale the chain can reach when the CPU share
# collapses. 120s covers the observed case with margin, is still bounded, and is
# a lever rather than a baked constant — see Readiness budgets in lib.sh (ADR 0029).
wait_for_url "http://127.0.0.1:11111/" "$PORTAL_READY_TIMEOUT" \
    || test_fail "portal not responding on port 11111 after ${PORTAL_READY_TIMEOUT}s"

# Check that the portal returns HTML
body=$(curl -sf http://127.0.0.1:11111/ 2>/dev/null)
[[ "$body" == *"<html"* ]] || test_fail "portal response does not contain HTML"

# Check /get-applications returns valid JSON
config=$(curl -sf http://127.0.0.1:11111/get-applications 2>/dev/null) || test_fail "/get-applications request failed"
# Verify it is valid JSON
echo "$config" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null \
    || test_fail "/get-applications did not return valid JSON"

test_pass "portal responds on :11111 with valid HTML and JSON apps"
