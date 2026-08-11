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
    $_hit || { fail_later "venv-missing" "declared torch venv '${want}' is absent or has no working torch"; continue; }

    # THE NAME IS A CLAIM — check it. A venv called torch-2.12.1 that contains
    # torch 2.11.0 satisfies every other test in pytorch.d: the presence check
    # above passes, and 10/20/30/40 all iterate whatever they find and validate
    # it on its own terms, never against what it is supposed to be. That is the
    # same shape as the absence hole this file was written to close, one level
    # in — a claim nothing verifies.
    #
    # 10-torch-core asserts the version for `main` only (against PYTORCH_VERSION),
    # so the supplementary venvs are exactly the ones with no version check at
    # all. install-torch-venv.sh does verify at build time, but the point of a
    # QA gate is to test the artifact rather than trust the build that made it.
    #
    # `main` is skipped here deliberately: its name encodes no version, and
    # 10-torch-core already checks it against the PYTORCH_VERSION env.
    [[ "$want" == "main" ]] && continue
    _want_ver="${want#torch-}"
    [[ "$_want_ver" == "$want" ]] && continue   # not a torch-X.Y.Z name; nothing claimed
    _actual=$("/venv/${want}/bin/python3" -c "import torch; print(torch.__version__)" 2>/dev/null || echo "")
    if [[ -z "$_actual" ]]; then
        fail_later "venv-version" "could not read torch version from venv '${want}'"
    elif [[ "$_actual" != "${_want_ver}"* ]]; then
        fail_later "venv-version" "venv '${want}' contains torch ${_actual}, not ${_want_ver}"
    else
        echo "  ${want}: torch ${_actual} matches the name"
    fi
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
