#!/bin/bash
# TEST_TIMEOUT=300
# Test: comma-separated PROVISIONING_* lists set in a template reach the instance and
# are split into their entries (ADR 0053).
#
# Vast drops any template variable whose value contains ";", so a ";"-separated list
# never arrived. base-qa sets these as real template env vars, so they take the same
# path through the platform as a customer's:
#   PROVISIONING_PIP            a version range (its comma must not split) and a second package
#   PROVISIONING_POST_COMMANDS  two commands, each of which must run on its own
#   PROVISIONING_DOWNLOADS      two url|dest entries
# 12-provisioning runs first and blocks until provisioning has finished.
source "$(dirname "$0")/../lib.sh"

# Predicate, not a self-skip: only base-qa sets these values, and it names this test in
# INSTANCE_TEST_REQUIRE_PASS so it cannot skip its way to green there.
[[ "${PROVISIONING_POST_COMMANDS:-}" == *qa-envlist* ]] \
    || test_skip "no qa-envlist PROVISIONING_* values (base-qa sets them)"

[[ -f /.provisioning_complete ]] || test_fatal "provisioning did not complete; see /var/log/portal/provisioning.log"

# ── Two post commands, each run on its own ───────────────────────────
for f in /tmp/qa-envlist-a /tmp/qa-envlist-b; do
    [[ -e "$f" ]] && echo "  post command ran: $f" \
        || fail_later "post-${f##*-}" "${f} missing: PROVISIONING_POST_COMMANDS was not split into two commands"
done

# ── pip: the range stayed one requirement, the second package installed ──
py=/venv/main/bin/python
[[ -x "$py" ]] || py=python3
six_version=$("$py" -c 'import six; print(six.__version__)' 2>/dev/null)
if [[ -z "$six_version" ]]; then
    fail_later "pip-range" "six is not importable: the 'six>=1.16,<2' entry did not install"
elif [[ "${six_version%%.*}" -ge 2 ]]; then
    fail_later "pip-range" "six ${six_version} installed: the '<2' half of the range was split off"
else
    echo "  pip range honoured: six ${six_version}"
fi
"$py" -c 'import tomli_w' 2>/dev/null && echo "  second pip package installed: tomli-w" \
    || fail_later "pip-second" "tomli_w is not importable: the second PROVISIONING_PIP entry was lost"

# ── Two downloads ────────────────────────────────────────────────────
for f in /tmp/qa-envlist/LICENSE.md /tmp/qa-envlist/README.md; do
    [[ -s "$f" ]] && echo "  downloaded: $f" \
        || fail_later "download-$(basename "$f")" "${f} missing: PROVISIONING_DOWNLOADS was not split into two entries"
done

report_failures
test_pass "comma-separated PROVISIONING_PIP, _POST_COMMANDS and _DOWNLOADS each split and applied"
