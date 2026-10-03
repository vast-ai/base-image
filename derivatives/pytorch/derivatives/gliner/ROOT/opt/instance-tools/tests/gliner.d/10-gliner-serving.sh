#!/bin/bash
# Test: the GLiNER API serves real inference on the GPU.
#
# A green `docker build` cannot see any of the properties below: the model is
# downloaded at first boot, not baked, so "the image built" and "the API answers"
# are separated by a HuggingFace fetch that can stall or 401.
# TEST_TIMEOUT=900
source "$(dirname "$0")/../lib.sh"

GLINER_INTERNAL_PORT=18000        # gliner.sh binds this on localhost
GLINER_LOG="/var/log/portal/gliner.log"
API_KEY="${GLINER_API_KEY:-gliner-qa-key}"

# ── The service ──────────────────────────────────────────────────────
# Readiness through the supervisord SOCKET, never `pgrep`: presence is satisfied the
# instant supervisord forks, and the gap to usable is a model download here (L069).
assert_service_running gliner

# ── The listener ─────────────────────────────────────────────────────
# First boot downloads ~774MB of weights before uvicorn binds, so this is slow.
if ! wait_for_port "${GLINER_INTERNAL_PORT}" "${GLINER_READY_TIMEOUT:-600}"; then
    [[ -f "${GLINER_LOG}" ]] && tail -40 "${GLINER_LOG}"
    test_fail "GLiNER is not listening on ${GLINER_INTERNAL_PORT} after ${GLINER_READY_TIMEOUT:-600}s"
fi
echo "  gliner: port ${GLINER_INTERNAL_PORT} listening"

# ── It reports a GPU ─────────────────────────────────────────────────
# The whole point of the image. The server falls back to CPU when torch cannot see a
# device and still serves correctly, so a CPU regression is invisible to any check that
# only asks whether the API answers -- this is the ADR 0016 failure class.
_health=$(curl -s --max-time "${HTTP_CHECK_MAX_TIME:-20}" \
          "http://127.0.0.1:${GLINER_INTERNAL_PORT}/health" 2>/dev/null)
[[ -n "${_health}" ]] || test_fail "GLiNER /health returned nothing (connection failed)"

echo "${_health}" | grep -q '"gpu_available":true' || {
    echo "  health: ${_health}"
    test_fail "GLiNER reports gpu_available=false -- the model fell back to CPU"
}
echo "  gliner: /health reports GPU ($(echo "${_health}" | sed -n 's/.*"gpu_name":"\([^"]*\)".*/\1/p'))"

# ── It extracts ──────────────────────────────────────────────────────
# Assert on a returned entity, not on HTTP 200. The endpoint returns 200 with an empty
# entity map when the model loads but scores nothing, which is a real regression class
# and indistinguishable from success at the status-code level.
_out=$(curl -s --max-time "${HTTP_CHECK_MAX_TIME:-60}" \
       -X POST "http://127.0.0.1:${GLINER_INTERNAL_PORT}/extract" \
       -H "Content-Type: application/json" \
       -H "Authorization: Bearer ${API_KEY}" \
       -d '{"text":"Apple CEO Tim Cook announced iPhone 15 in Cupertino for $999.","labels":["person","company","product","location","price"],"threshold":0.3}' 2>/dev/null)

echo "${_out}" | grep -q '"Tim Cook"' || {
    echo "  response: ${_out}"
    test_fail "GLiNER /extract did not return the expected person entity"
}
echo "  gliner: /extract returned the expected entities"

# ── Auth is enforced ─────────────────────────────────────────────────
# The server leaves the API OPEN when GLINER_API_KEY is unset. A regression that
# dropped the key would publish an unauthenticated extraction endpoint, so assert the
# rejection rather than trusting the template to have set it.
_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time "${HTTP_CHECK_MAX_TIME:-20}" \
        -X POST "http://127.0.0.1:${GLINER_INTERNAL_PORT}/extract" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer definitely-not-the-key" \
        -d '{"text":"x","labels":["person"]}' 2>/dev/null)
_code="${_code:-000}"
case "${_code}" in
    401) echo "  gliner: rejects a wrong bearer token (401)" ;;
    000) test_fail "GLiNER did not answer the auth probe (connection failed)" ;;
    *)   fail_later "gliner-auth" "GLiNER answered a WRONG bearer token with ${_code}, not 401 -- the endpoint is unauthenticated" ;;
esac

# ── The bind is loopback ─────────────────────────────────────────────
# This endpoint has a bearer token but must still sit behind Caddy like everything
# else. `ss` reports the LISTENER, which is the only thing that matters -- a template
# port entry is not what exposes it.
# listener_is_public lives in BASE's lib.sh and is inherited from the pinned pytorch
# base, not this repo. Without this guard a missing function returns 127, `if` takes
# the ELSE branch, and the check PASSES having tested nothing.
declare -F listener_is_public >/dev/null 2>&1 || \
    test_fail "this image's lib.sh predates listener_is_public -- the bind check cannot run; rebuild base + pytorch and bump this image's PYTORCH_BASE pin"

if listener_is_public "${GLINER_INTERNAL_PORT}"; then
    listener_local_addr "${GLINER_INTERNAL_PORT}"
    fail_later "gliner-bind" "GLiNER is bound to a PUBLIC interface on ${GLINER_INTERNAL_PORT}, not loopback"
else
    echo "  gliner: bound to loopback ($(listener_local_addr "${GLINER_INTERNAL_PORT}"))"
fi

test_pass
