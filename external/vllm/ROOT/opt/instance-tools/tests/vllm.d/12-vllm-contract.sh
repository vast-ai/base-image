#!/bin/bash
# Test: the OpenAI surface this image serves, asserted deterministically (ADR 0031).
#
# 10-vllm-serving.sh answers "did the engine come up and emit a token". This answers
# "is what it serves the thing the template asked for, shaped the way a client
# integrates against". The two are separate files on purpose: 10 is REQUIRED by the
# gate today, and mixing a ramping assertion set into a required test would promote
# every new check to blocking on the day it was written.
#
# ADVISORY BY DEFAULT, and that is ADR 0006 condition 2's ramp, not timidity. Every
# assertion here lands reporting-only and may be promoted after two consecutive green
# promotes with its advisory block clean; `VLLM_CONTRACT_ENFORCE=true` is the single
# lever that flips it, exactly as EXPOSURE_ENFORCE does for base/28. On a gate where
# every red costs a redraw rental (ADR 0029), the ramp is the budget control.
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
enforce="${VLLM_CONTRACT_ENFORCE:-false}"
report=$(mktemp)
"$PY" "$CHECKER" \
    --base-url "$VLLM_API" \
    --port "$VLLM_INTERNAL_PORT" \
    --model "$VLLM_MODEL" \
    --vllm-args "${VLLM_ARGS:-}" \
    --expect-caps "${VLLM_EXPECT_CAPS:-}" \
    > "$report" 2>&1
rc=$?

# Human lines first: whatever the verdict, the run log carries the full evidence.
grep -vE '^(VIOLATION|ADVISORY|ERROR|NA) ' "$report"

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

rm -f "$report"

# rc 2 (could-not-decide) has already been turned into fail_later entries above; this
# catches the case where the checker died before printing any ERROR line at all —
# an interpreter that could not start, or a traceback. Silence is the failure mode
# that matters, so it is asserted rather than assumed.
if (( rc == 2 )) && (( ${#FAILURES[@]} == 0 )); then
    fail_later "contract-checker" "contract_check.py exited 2 (could not decide) without reporting a reason"
elif (( rc > 2 )); then
    fail_later "contract-checker" "contract_check.py exited ${rc} — it did not run to completion"
fi

report_failures
if [[ "${enforce,,}" == "true" ]]; then
    test_pass "vLLM API contract verified — enforcing, ${violations} violation(s)"
fi
test_pass "vLLM API contract checks complete — ${violations} violation(s), advisory (VLLM_CONTRACT_ENFORCE=false)"
