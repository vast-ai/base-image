#!/bin/bash
# Test: StableDAW serving — the FastAPI backend + built SPA on one loopback port.
#
# stabledaw.sh launches /opt/stabledaw-serve.py, which serves the React SPA at /
# and the REST API under /api/* from one uvicorn process bound to 127.0.0.1.
# The generic networking test already asserts the loopback bind (no 0.0.0.0);
# this test covers the piece unique to this image: that the API answers AND the
# launcher's StaticFiles mount actually serves the SPA index — i.e. a frontend
# build/mount gap can't ship as a silently UI-less "healthy" service.
#
# The model stack loads lazily on first generation, so the server binds and
# /api/health answers without a GPU; generation itself needs one. Keep this
# check GPU-agnostic.
source "$(dirname "$0")/../lib.sh"

STABLEDAW_PORT="${STABLEDAW_PORT:-18600}"
BASE="http://127.0.0.1:${STABLEDAW_PORT}"
HEALTH_TIMEOUT="${STABLEDAW_HEALTH_TIMEOUT:-600}"

service_running stabledaw || test_skip "stabledaw service not running"

echo "  -- waiting for stabledaw on ${BASE} --"
wait_for_port "$STABLEDAW_PORT" "$HEALTH_TIMEOUT" \
    || test_fail "stabledaw not listening on ${STABLEDAW_PORT} within ${HEALTH_TIMEOUT}s"

# REST API on the loopback bind.
wait_for_url "${BASE}/api/health" "$HEALTH_TIMEOUT" \
    || test_fail "stabledaw /api/health did not return success within ${HEALTH_TIMEOUT}s"
echo "  API healthy: ${BASE}/api/health"

# SPA served at / by the launcher's StaticFiles mount (index.html). A bare API
# with no UI mount would not return 200 here.
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${BASE}/" 2>/dev/null)
[[ "$code" == "200" ]] \
    || test_fail "SPA index not served at ${BASE}/ (HTTP ${code}) — frontend dist missing or not mounted"
echo "  SPA index served at ${BASE}/ (HTTP 200)"

test_pass "StableDAW serving verified (API /api/health + SPA / on loopback ${STABLEDAW_PORT})"
