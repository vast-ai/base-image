#!/bin/bash
# Test: launch env values reach the instance literally (ADR 0052).
#
# 10-prep-env.sh writes the launch environment to /etc/environment, and the boot
# shell (so supervisord), every supervisor script (so the services) and every login
# shell load it. It used to write NAME="value" unescaped, so loading it evaluated
# the value: a generated password `p4$sW0rd` became `p4`, `say "hi there"` became
# empty, backticks ran, and a newline split one variable into two.
#
# base-qa exports the QA_ENV_* probes below from its onstart, which runs before the
# image boots and so reaches 10-prep-env.sh exactly as a template env var would,
# without the platform's env character filter. The values here must match that
# onstart. Read back from three places, each loaded a different way:
#   - a fresh shell loading the file (login shells, supervisor scripts)
#   - supervisord (the boot shell's environment after it loaded the file)
#   - caddy (a service started by a supervisor script that loaded the file)
source "$(dirname "$0")/../lib.sh"

# Predicate, not a self-skip: only base-qa sets the probes, and it names this test
# in INSTANCE_TEST_REQUIRE_PASS so it cannot skip its way to green there.
grep -q '^QA_ENV_PROBE=' /etc/environment 2>/dev/null \
    || test_skip "no QA_ENV_* probes in /etc/environment (base-qa exports them from onstart)"

canary=/tmp/qa-env-canary
declare -A want=(
    [QA_ENV_SECRET]='p4$sW0rd'
    [QA_ENV_SEMI]='Server=db;Database=app'
    [QA_ENV_QUOTE]='say "hi there"'
    [QA_ENV_SQUOTE]="it's"
    [QA_ENV_BACKSLASH]='a\b\'
    [QA_ENV_TICK]='`touch /tmp/qa-env-canary`'
    [QA_ENV_SUBST]='$(touch /tmp/qa-env-canary)'
    [QA_ENV_NL]=$'line1\nQA_ENV_FAKE=injected'
)

# check WHERE NAME GOT
check() {
    [[ "$3" == "${want[$2]}" ]] \
        || fail_later "$1-$2" "got $(printf '%q' "$3"), want $(printf '%q' "${want[$2]}")"
}

# environ_value PID NAME: print NAME's value from the process's initial
# environment; return 1 if it has none.
environ_value() {
    local entry
    while IFS= read -r -d '' entry; do
        [[ "$entry" == "$2="* ]] && { printf '%s' "${entry#*=}"; return 0; }
    done < "/proc/$1/environ"
    return 1
}

# ── The file itself: one line per variable, no injected variable ─────
[[ $(grep -c '^QA_ENV_NL=' /etc/environment) == 1 ]] \
    || fail_later "file-lines" "QA_ENV_NL is not exactly one line in /etc/environment"
grep -q '^QA_ENV_FAKE=' /etc/environment \
    && fail_later "file-inject" "a newline in QA_ENV_NL created a QA_ENV_FAKE line"

# ── A fresh shell loading the file ───────────────────────────────────
for name in "${!want[@]}"; do
    got=$(env -i bash -c 'set -a; . /etc/environment 2>/dev/null; set +a; printf "%s" "${!1}"' _ "$name")
    check fresh-shell "$name" "$got"
done

# ── supervisord and caddy ────────────────────────────────────────────
wait_for_supervisor "$SUPERVISOR_READY_TIMEOUT" >/dev/null \
    || fail_later "supervisord" "supervisord RPC socket never came up"
sup_pid=$(pgrep -o -f 'supervisord .*-c /etc/supervisor/supervisord.conf')
deadline=$(( SECONDS + CADDY_READY_TIMEOUT ))
until caddy_pid=$(pgrep -o -f '(^|/)caddy run') || (( SECONDS >= deadline )); do sleep 2; done
for proc in "supervisord:${sup_pid}" "caddy:${caddy_pid}"; do
    label=${proc%%:*} pid=${proc#*:}
    if [[ -z "$pid" || ! -r "/proc/${pid}/environ" ]]; then
        fail_later "$label" "no readable ${label} process"
        continue
    fi
    for name in "${!want[@]}"; do
        got=$(environ_value "$pid" "$name") \
            || { fail_later "${label}-${name}" "${name} missing from ${label}'s environment"; continue; }
        check "$label" "$name" "$got"
    done
    environ_value "$pid" QA_ENV_FAKE >/dev/null \
        && fail_later "${label}-inject" "QA_ENV_FAKE present in ${label}'s environment"
done

# ── Nothing in a value ran ───────────────────────────────────────────
[[ -e "$canary" ]] && fail_later "canary" "${canary} exists: a QA_ENV_* value was run as a command"

report_failures
test_pass "${#want[@]} probe values literal in /etc/environment, a fresh shell, supervisord and caddy"
