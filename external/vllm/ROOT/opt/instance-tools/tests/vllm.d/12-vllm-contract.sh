#!/bin/bash
# Test: the OpenAI surface this image serves, asserted deterministically (ADR 0031).
#
# 10-vllm-serving.sh answers "did the engine come up and emit a token". This answers
# "is what it serves the thing the template asked for, shaped the way a client
# integrates against". The two are separate files on purpose: 10 is REQUIRED by the
# gate today, and mixing a ramping assertion set into a required test would promote
# every new check to blocking on the day it was written.
#
# ENFORCING BY DEFAULT, with `VLLM_CONTRACT_ENFORCE=false` as the escape hatch —
# the inverse of how this file first shipped. It landed advisory under ADR 0006
# condition 2's ramp, ran clean on real hardware (10 ok, 0 errors), and the one
# violation it raised was a bug in the checker rather than the image. The ramp did
# its job in a single run; keeping it after that would be caution costing coverage.
# See the note at the `enforce=` assignment for the one assertion most likely to
# need the escape hatch.
#
# The one thing that is NEVER advisory is a check that could not DECIDE. A tooling
# failure means the assertion did not run, which is the skip-as-pass shape the QA gate
# exists to close, so exit 2 fails in both modes.
#
# The assertions live in contract_check.py beside this file — structured logic is
# Python here (CLAUDE.md), and that file is unit-tested off-box in
# tools/imagegen/tests/test_vllm_contract_check.py rather than only on a rented GPU.
# It is NOT discovered as a test: runner.sh finds `-name '*.sh' -executable`.
# TEST_TIMEOUT=1800
source "$(dirname "$0")/../lib.sh"

CHECKER="$(dirname "$0")/contract_check.py"

# Inert, not skip-as-pass: with no model configured there is no served surface to make
# claims about. 10-vllm-serving is the file the gate REQUIRES, so an image that lost
# VLLM_MODEL is already red there (L057/L072) rather than quietly green here.
[[ -n "${VLLM_MODEL:-}" ]] || test_skip "VLLM_MODEL not set — no served surface to assert"
[[ -f "$CHECKER" ]] || test_fail "contract_check.py missing beside this test — the assertions cannot run"

VLLM_INTERNAL_PORT="${VLLM_INTERNAL_PORT:-18000}"
# Read the port the template actually launched with, so a template that moves the
# engine does not silently probe an empty socket and report "not healthy".
_declared_port=$(sed -n 's/.*--port[= ]\+\([0-9]\+\).*/\1/p' <<< "${VLLM_ARGS:-}" | head -1)
[[ -n "$_declared_port" ]] && VLLM_INTERNAL_PORT="$_declared_port"

VLLM_API="http://127.0.0.1:${VLLM_INTERNAL_PORT}"
# Sized for a cold start, not for the common case: this file normally runs after
# 10-vllm-serving has already waited out model load, but it must also hold when run
# alone from an SSH session on a box that has just booted.
CONTRACT_READY_TIMEOUT="${VLLM_CONTRACT_READY_TIMEOUT:-1200}"

echo ""
echo "  -- readiness --"
wait_for_url "${VLLM_API}/health" "$CONTRACT_READY_TIMEOUT" \
    || test_fail "vLLM /health not reachable on :${VLLM_INTERNAL_PORT} after ${CONTRACT_READY_TIMEOUT}s"
echo "  vLLM healthy on :${VLLM_INTERNAL_PORT}"

# The checker's chat-template round trip needs the interpreter that has transformers,
# which is vLLM's own venv — /venv/main is what vllm.sh activates before `vllm serve`.
# Falling back to python3 is not a failure: the round trip reports n/a with its reason
# and every other assertion is stdlib-only.
PY=python3
[[ -x /venv/main/bin/python ]] && PY=/venv/main/bin/python
echo "  interpreter: ${PY}"

echo ""
echo "  -- contract --"
# ENFORCING BY DEFAULT. Every assertion this runs is forced — token arithmetic,
# max_tokens=1, a grammar, a named tool, a status class, a socket address — so a
# violation is a defect in what the server returned, not a sampling accident. The
# ADR 0006 ramp exists to stop unproven assertions blocking a promote; these ran
# clean on real hardware first (10 ok, 0 errors) and the single violation they did
# raise was a bug in the checker, which is what the ramp caught and why the ramp
# was worth having.
#
# This default has NO customer reach, which is what makes enforcing by default the
# easy call rather than a trade. The whole suite only runs when INSTANCE_TEST=true
# (`70-instance-test.sh` returns immediately otherwise), and the only thing that
# sets it is the QA client. A customer instance never starts the runner at all.
#
# `false` therefore exists for a QA TEMPLATE that legitimately diverges, not for a
# customer. The assertion most likely to want it is bind-loopback: `vllm serve`
# binds ALL interfaces when --host is absent (api_server.py:
# `sock_addr = (args.host or "", args.port)`) and vllm.sh injects no host, so a
# template whose VLLM_ARGS omits --host fails here — correctly, since that cell
# would be serving the engine past the Caddy auth gate.
enforce="${VLLM_CONTRACT_ENFORCE:-true}"
report=$(mktemp)
# `--opt=value`, not `--opt value`, for the two operator-supplied strings. argparse
# treats a value beginning with `-` as an option UNLESS it contains a space, so a
# single-token VLLM_ARGS — `--enforce-eager`, which is a legitimate value — is read
# as an unknown FLAG and the checker exits 2 on a usage error before asserting
# anything. The `=` form has no such ambiguity. Found by a unit test, not on a box.
"$PY" "$CHECKER" \
    --base-url="$VLLM_API" \
    --port="$VLLM_INTERNAL_PORT" \
    --model="$VLLM_MODEL" \
    --vllm-args="${VLLM_ARGS:-}" \
    --expect-caps="${VLLM_EXPECT_CAPS:-}" \
    > "$report" 2>&1
rc=$?

# Human lines first: whatever the verdict, the run log carries the full evidence.
grep -vE '^(VIOLATION|ADVISORY|ERROR|NA|CONTRACT-COMPLETE) ' "$report"

# A check that could not decide is never advisory — the same rule base/28 applies to
# a scan that failed to run. Report these before the violations: an image whose
# checker crashed has no meaningful violation list.
while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    check=$(awk '{print $2}' <<< "$line")
    fail_later "contract-error-${check}" "${line#ERROR }"
done < <(grep '^ERROR ' "$report" || true)

violations=$(grep -c '^VIOLATION ' "$report" || true)
if (( violations > 0 )); then
    if [[ "${enforce,,}" == "true" ]]; then
        while IFS= read -r line; do
            [[ -z "$line" ]] && continue
            check=$(awk '{print $2}' <<< "$line")
            fail_later "contract-${check}" "${line#VIOLATION }"
        done < <(grep '^VIOLATION ' "$report")
    else
        echo ""
        echo "  ADVISORY: ${violations} contract violation(s) above are REPORTED, not gating"
        echo "  (VLLM_CONTRACT_ENFORCE=false — ADR 0031 decision 7: two consecutive green"
        echo "   promotes with this block clean, then set it true in the QA template)"
    fi
fi

# The checker prints CONTRACT-COMPLETE as its LAST line and only on an explicit
# exit, so its absence means the interpreter died — a traceback, an OOM, a kill.
# That matters because an unhandled Python exception exits 1, which is also the
# "violations found" code: without this the caller would see rc=1, find no VIOLATION
# lines to report, and pass. A crash must never be quieter than a finding, and this
# is NOT subject to the advisory ramp — a checker that did not run decided nothing.
if ! grep -q '^CONTRACT-COMPLETE ' "$report"; then
    fail_later "contract-checker" "contract_check.py exited ${rc} without reaching its completion marker — it crashed rather than deciding (last lines above)"
elif (( rc == 2 )) && (( ${#FAILURES[@]} == 0 )); then
    fail_later "contract-checker" "contract_check.py exited 2 (could not decide) without reporting a reason"
elif (( rc == 1 )) && (( violations == 0 )); then
    fail_later "contract-checker" "contract_check.py exited 1 (violations) but reported none"
elif (( rc > 2 )); then
    fail_later "contract-checker" "contract_check.py exited ${rc} — an exit code it does not define"
fi

rm -f "$report"

report_failures
if [[ "${enforce,,}" == "true" ]]; then
    test_pass "vLLM API contract verified — enforcing, ${violations} violation(s)"
fi
test_pass "vLLM API contract checks complete — ${violations} violation(s), advisory (VLLM_CONTRACT_ENFORCE=false)"
