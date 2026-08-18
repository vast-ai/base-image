#!/bin/bash
# Test: the image ships exactly the torch venvs it is supposed to ship.
#
# Every other test in pytorch.d DISCOVERS venvs by globbing /venv/*/ and then
# validates whatever it finds. That is the right shape for those tests and the
# wrong shape for this question, because a discovery-based test cannot detect
# ABSENCE: a `multi` image that shipped one of its three torch venvs would find
# one venv, validate it perfectly, and report green. Two thirds of the artifact
# would be missing and every test would pass (ADR 0021 decision 6).
#
# So the expected set has to come from OUTSIDE the image. The QA template
# declares it; this test compares. It costs no GPU time and no extra cell —
# it is the cheapest real coverage in the whole gate.
#
# Unset => skip. Customer instances and non-gated runs have nothing to compare
# against, and inventing an expectation there would fail images that are fine.
source "$(dirname "$0")/../lib.sh"

[[ -n "${EXPECTED_TORCH_VENVS:-}" ]] || \
    test_skip "EXPECTED_TORCH_VENVS not declared (not a gated run)"

# Accept comma- and/or whitespace-separated names, matching the convention
# INSTANCE_TEST_REQUIRE_PASS uses — these are hand-written in templates.
read -ra _expected <<< "${EXPECTED_TORCH_VENVS//,/ }"

_found=()
for venv_dir in /venv/*/; do
    [[ -f "${venv_dir}bin/activate" ]] || continue
    if "${venv_dir}bin/python3" -c "import torch" 2>/dev/null; then
        _found+=("$(basename "$venv_dir")")
    fi
done

echo "  expected: ${_expected[*]}"
echo "  found:    ${_found[*]:-<none>}"

# Missing is the defect this exists for.
for want in "${_expected[@]}"; do
    [[ -z "$want" ]] && continue
    _hit=false
    for got in "${_found[@]}"; do
        [[ "$got" == "$want" ]] && _hit=true && break
    done
    $_hit || fail_later "venv-missing" "declared torch venv '${want}' is absent or has no working torch"
done

# Extra venvs are reported but do NOT fail. An image gaining a venv is a change
# worth seeing in the log, but it is additive and breaks nothing — failing on it
# would make every deliberate addition look like a defect and train people to
# ignore this test.
for got in "${_found[@]}"; do
    _hit=false
    for want in "${_expected[@]}"; do
        [[ "$got" == "$want" ]] && _hit=true && break
    done
    $_hit || echo "  NOTE: undeclared torch venv '${got}' present (not a failure)"
done

report_failures
test_pass "all ${#_expected[@]} declared torch venv(s) present with a working torch"
