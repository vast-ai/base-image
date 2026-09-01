#!/bin/bash
# Test: Unsloth Studio serves. The image had NO instance tests and NO QA gate at all
# before this, so every property below was previously unasserted on every build.
# TEST_TIMEOUT=900
source "$(dirname "$0")/../lib.sh"

STUDIO_INTERNAL_PORT=18888        # unsloth-studio.sh binds this on localhost
STUDIO_LOG="/var/log/portal/unsloth-studio.log"

# ── The service ──────────────────────────────────────────────────────
# Readiness through the supervisord SOCKET, never `pgrep`: presence is satisfied the
# instant supervisord forks, and the gap to usable is real and load-dependent (L069).
assert_service_running unsloth-studio

# ── The listener ─────────────────────────────────────────────────────
# The studio builds a frontend on first boot, so this is slow rather than instant.
if ! wait_for_port "${STUDIO_INTERNAL_PORT}" "${STUDIO_READY_TIMEOUT:-600}"; then
    [[ -f "${STUDIO_LOG}" ]] && tail -40 "${STUDIO_LOG}"
    test_fail "Unsloth Studio is not listening on ${STUDIO_INTERNAL_PORT} after ${STUDIO_READY_TIMEOUT:-600}s"
fi
echo "  studio: port ${STUDIO_INTERNAL_PORT} listening"

# ── It answers ───────────────────────────────────────────────────────
# Any HTTP status is progress over a dead socket, but a 5xx is the studio failing to
# render, so accept only the classes that mean the app replied. The studio redirects to
# its login (302) and gates behind auth (401/403), all of which prove it is serving.
# No `|| echo 000`: curl WRITES the -w template and THEN exits non-zero on a connection
# failure, so the fallback appends to it and _code becomes "000000" — which matches
# neither the 000 arm nor any success arm, so a dead socket reported "the app is not
# serving" instead of "connection failed". curl already emits 000 itself; take it.
_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time "${HTTP_CHECK_MAX_TIME:-20}" \
        "http://127.0.0.1:${STUDIO_INTERNAL_PORT}/" 2>/dev/null)
_code="${_code:-000}"
case "${_code}" in
    200|301|302|303|307|308|401|403) echo "  studio: / answered ${_code}" ;;
    000) test_fail "Unsloth Studio did not answer on ${STUDIO_INTERNAL_PORT} (connection failed)" ;;
    *)   test_fail "Unsloth Studio answered / with ${_code} — the app is not serving" ;;
esac

# ── The bind is loopback ─────────────────────────────────────────────
# The studio has its own auth, but it sits behind Caddy like everything else and must
# not publish itself. `ss` reports the LISTENER, which is the only thing that matters —
# a template port entry is not what exposes it.
# listener_is_public lives in BASE's lib.sh, and this image inherits lib.sh from the
# pytorch base it pins — not from this repo. If that base predates the helper, bash
# returns 127 for the missing function, `if` takes the ELSE branch, and the check prints
# "bound to loopback" and PASSES having tested nothing. Assert the helper exists so a
# pin that is too old is a loud red naming the cause, not a silent green.
declare -F listener_is_public >/dev/null 2>&1 || \
    test_fail "this image's lib.sh predates listener_is_public — the bind check cannot run; rebuild base + pytorch and bump this image's PYTORCH_BASE pin"

if listener_is_public "${STUDIO_INTERNAL_PORT}"; then
    listener_local_addr "${STUDIO_INTERNAL_PORT}"
    fail_later "studio-bind" "Unsloth Studio is bound to a PUBLIC interface on ${STUDIO_INTERNAL_PORT}, not loopback"
else
    echo "  studio: bound to loopback ($(listener_local_addr "${STUDIO_INTERNAL_PORT}"))"
fi

# ── The portal fronts it ─────────────────────────────────────────────
# Without an entry the app is unreachable for a user even though it is running, which
# is indistinguishable from "works" to every check above.
if portal_has_entry "unsloth"; then
    echo "  studio: portal entry present"
else
    fail_later "studio-portal" "PORTAL_CONFIG has no Unsloth Studio entry — the app runs but nothing routes to it"
fi

report_failures
test_pass "Unsloth Studio is serving on ${STUDIO_INTERNAL_PORT}, on loopback, behind a portal entry"
