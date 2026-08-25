#!/bin/bash
# Test: boot markers and config files are in expected state.
source "$(dirname "$0")/../lib.sh"

# Provisioning marker should be gone (test 12-provisioning monitors this)
if [[ -f /.provisioning ]]; then
    echo "  WARN: /.provisioning still exists"
fi

# First boot marker — informational, not required
if [[ -f /.first_boot_complete ]]; then
    echo "  /.first_boot_complete present"
fi

# Provisioning outcome markers — informational (12-provisioning validates these)
for marker in /.provisioning_complete /.provisioning_failed; do
    if [[ -f "$marker" ]]; then
        echo "  present: $marker"
    fi
done

# /etc/environment must exist and contain PATH
assert_file_exists /etc/environment
grep -q "PATH=" /etc/environment || test_fail "/etc/environment missing PATH"

# /etc/portal.yaml — not expected in serverless mode
if ! is_serverless; then
    assert_file_exists /etc/portal.yaml
fi

# /etc/Caddyfile — not expected in serverless; may take a few seconds to generate
if ! is_serverless; then
    for _ in $(seq 1 30); do
        [[ -f /etc/Caddyfile ]] && break
        sleep 1
    done
    assert_file_exists /etc/Caddyfile
fi

# ── The runtime-mode decision left a record ──────────────────────────
# ADR 0034. `01-detect-serverless.sh` writes this on EVERY outcome, including the
# negative one, because "detection ran and declined" and "this image predates
# detection" are different facts and only a marker separates them.
#
# The round trip is the point: the marker's own `serverless=` field must agree with
# what the rest of the suite sees through is_serverless(). A disagreement means the
# stage ran and its export did not survive — the partial-application failure that
# would otherwise present as an image half in serverless mode, with the boot flags on
# one value and every service on the other.
_sd_marker=/run/vast-serverless-detect
if [[ ! -f "$_sd_marker" ]]; then
    fail_later "serverless-marker" "$_sd_marker missing — the mode decision left no record, so nothing can say whether serverless was declared, detected, or declined (ADR 0034)"
else
    _sd_recorded=$(sed -n 's/^serverless=//p' "$_sd_marker" | head -1)
    _sd_verdict=$(sed -n 's/^verdict=//p' "$_sd_marker" | head -1)
    echo "  serverless mode: ${_sd_verdict:-<none>} (recorded SERVERLESS=${_sd_recorded:-<none>})"
    if is_serverless; then
        [[ "${_sd_recorded,,}" == "true" ]] || \
            fail_later "serverless-marker" "the suite sees serverless mode but the boot marker recorded SERVERLESS=${_sd_recorded:-<none>} (verdict=${_sd_verdict:-<none>}) — the decision and the running environment disagree"
    else
        [[ "${_sd_recorded,,}" == "true" ]] && \
            fail_later "serverless-marker" "the boot marker recorded SERVERLESS=true (verdict=${_sd_verdict:-<none>}) but the suite is not in serverless mode — the export did not survive to the test environment"
    fi
fi

# ── A boot stage that deliberately gave up ───────────────────────────
# boot_default.sh sources every stage and DISCARDS its exit status, so a stage
# that decided it could not safely continue leaves no trace anywhere — which is
# why the SSH stranding in 35-sync-home-dirs could only be found by reading the
# code. Stages 35 and 37 now record themselves here. Deliberate failures only: a
# blanket "any non-zero source" wrapper would fire on 10-prep-env.sh, whose last
# line is a legitimately-false conditional, and a check that cries wolf is worse
# than none.
if [[ -s /var/log/vast_boot_failures ]]; then
    while IFS= read -r _bf; do
        [[ -n "$_bf" ]] && fail_later "boot-stage" "$_bf"
    done < /var/log/vast_boot_failures
else
    echo "  no boot stage reported a deliberate failure"
fi

# ── /etc/portal.yaml must be readable by the unprivileged account ────
# syncthing.conf is the one base unit that runs `user=user`, and exit_portal.sh
# decides its startup by grepping this file. A root-only file makes that grep
# return EACCES, `! grep` true, and syncthing self-skip PERMANENTLY while logging
# "not in /etc/portal.yaml" — with the portal hiding it, because a skip marker
# reads as "not configured". An atomic-write fix produced exactly that by
# publishing mkstemp's 0600 through os.replace, and it persists on /etc across
# stop/start, so an image rollback does not clear it.
if [[ -f /etc/portal.yaml ]] && id user &>/dev/null; then
    if runuser -u user -- test -r /etc/portal.yaml 2>/dev/null; then
        echo "  /etc/portal.yaml readable by 'user' ($(stat -c %a /etc/portal.yaml))"
    else
        fail_later "portal-yaml-mode" \
            "/etc/portal.yaml is $(stat -c %a /etc/portal.yaml), not readable by 'user' — syncthing will self-skip"
    fi
fi

report_failures

test_pass "boot markers and configs in expected state"
