#!/bin/bash
# TEST_TIMEOUT=300
# Test: comma-separated PROVISIONING_* lists set in a template reach the instance and
# are split into their entries (ADR 0053).
#
# Vast drops any template variable whose value contains ";", so a ";"-separated list
# never arrived. base-qa sets these as real template env vars, so they take the same
# path through the platform as a customer's:
#   PROVISIONING_PIP            a version range (its commas must not split), extras, a third package
#   PROVISIONING_POST_COMMANDS  two commands, each of which must run on its own
#   PROVISIONING_DOWNLOADS      two url|dest entries
# The provisioning log prints each list as parsed, so this compares those lines with the
# expected split, then checks the commands ran and the files arrived.
# 12-provisioning runs first and blocks until provisioning has finished.
source "$(dirname "$0")/../lib.sh"

# Predicate, not a self-skip: only base-qa sets these values, and it names this test in
# INSTANCE_TEST_REQUIRE_PASS so it cannot skip its way to green there.
[[ "${PROVISIONING_POST_COMMANDS:-}" == *qa-envlist* ]] \
    || test_skip "no qa-envlist PROVISIONING_* values (base-qa sets them)"

PROV_LOG=/var/log/portal/provisioning.log
[[ -f /.provisioning_complete ]] || test_fatal "provisioning did not complete; see ${PROV_LOG}"

# ── Each list parsed into exactly the expected entries ───────────────
expect_parsed() {
    local name=$1 line=$2
    if grep -qF -- "$line" "$PROV_LOG"; then
        echo "  parsed as expected: \$${name}"
    else
        fail_later "parse-${name}" "no '${line}' in ${PROV_LOG}; it logged: $(grep -F "from \$${name}:" "$PROV_LOG" | tail -1)"
    fi
}
expect_parsed PROVISIONING_PIP \
    "added 3 pip packages from \$PROVISIONING_PIP: ['packaging>=20,!=21.0,<999', 'huggingface-hub[cli]', 'wheel']"
expect_parsed PROVISIONING_POST_COMMANDS \
    "added 2 post commands from \$PROVISIONING_POST_COMMANDS: ['touch /tmp/qa-envlist-a', 'touch /tmp/qa-envlist-b']"
expect_parsed PROVISIONING_DOWNLOADS "added 2 downloads from \$PROVISIONING_DOWNLOADS: "

# ── Two post commands, each run on its own ───────────────────────────
for f in /tmp/qa-envlist-a /tmp/qa-envlist-b; do
    [[ -e "$f" ]] && echo "  post command ran: $f" \
        || fail_later "post-${f##*-}" "${f} missing: PROVISIONING_POST_COMMANDS was not split into two commands"
done

# ── Two downloads ────────────────────────────────────────────────────
for f in /tmp/qa-envlist/LICENSE.md /tmp/qa-envlist/README.md; do
    [[ -s "$f" ]] && echo "  downloaded: $f" \
        || fail_later "download-$(basename "$f")" "${f} missing: PROVISIONING_DOWNLOADS was not split into two entries"
done

report_failures
test_pass "comma-separated PROVISIONING_PIP, _POST_COMMANDS and _DOWNLOADS each split and applied"
