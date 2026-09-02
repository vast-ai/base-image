#!/bin/bash
# Test: every bundled app's venv resolves the torch it was built against.
# TEST_TIMEOUT=900
source "$(dirname "$0")/../lib.sh"

# WHY THIS IS THE FIRST GATE FOR THIS IMAGE.
#
# aio-studio bundles eight apps that cannot share one torch: three need 2.7.1, one
# needs 2.9.1, four take the base's newest. That is the entire reason it builds on a
# MULTI-torch pytorch base and the entire reason `create_app_venv` exists — it makes a
# per-app venv whose own site-packages shadows a SHARED base venv reached through a
# `_base.pth` line, copying only the torch-ecosystem dist-info so uv does not reinstall
# a second multi-gigabyte torch per app.
#
# That mechanism is clever and quiet when it breaks. A wrong base venv, a stale
# `_base.pth`, or a base image built on the wrong multi family (multi-2130-2121-2110
# carries 2.13/2.12/2.11 and none of 2.9.1/2.7.1) all produce an image that BUILDS,
# ships, and fails the first time a customer launches the affected app. The Dockerfile
# asserts the base carries the three venvs; this asserts the wiring actually resolves
# at RUNTIME, from inside each app's own interpreter, which is the only place the
# `.pth` indirection is real.
#
# It deliberately does NOT start the apps. All nine supervisor programs ship
# autostart=false — the user picks one, because eight heavyweight apps cannot share a
# GPU — so "every app is RUNNING" is not a property this image has, and a test
# asserting it would be wrong about the product rather than finding a defect.

# app venv -> the torch it must resolve. Mirrors `create_app_venv <app> <base>` in the
# Dockerfile; the base venvs are main (newest), torch-2.9.1 and torch-2.7.1.
declare -A EXPECT=(
    [comfyui]=main
    [forge]=main
    [unsloth]=main
    [ace-step]=main
    [ostris]=2.7.1
    [wan2gp]=2.7.1
    [whisper]=2.7.1
    [voicebox]=2.9.1
)

# Resolve what `main` actually is on this base rather than hardcoding it: the newest
# member of the multi family moves (2.10 today), and pinning it here would turn a
# routine base bump into a red cell that is not about the image.
main_ver=$(/venv/main/bin/python -c 'import torch; print(torch.__version__)' 2>/dev/null | cut -d+ -f1)
[[ -n "$main_ver" ]] || test_fail "/venv/main cannot import torch — the base itself is broken, not the app venvs"
echo "  base /venv/main torch: ${main_ver}"

echo ""
echo "  -- per-app venv torch --"

for app in "${!EXPECT[@]}"; do
    want="${EXPECT[$app]}"
    [[ "$want" == "main" ]] && want="$main_ver"

    py="/venv/${app}/bin/python"
    if [[ ! -x "$py" ]]; then
        fail_later "venv-${app}" "/venv/${app} has no python — create_app_venv did not run for this app"
        continue
    fi

    # Import from INSIDE the app venv. Reading the copied dist-info would be faster and
    # would miss the failure that matters: dist-info is copied by create_app_venv, so it
    # can name a version the _base.pth no longer resolves to.
    got=$("$py" -c 'import torch; print(torch.__version__)' 2>/dev/null | cut -d+ -f1)
    if [[ -z "$got" ]]; then
        fail_later "torch-${app}" "/venv/${app} cannot import torch (broken _base.pth or missing base venv)"
        continue
    fi
    if [[ "$got" != "$want" ]]; then
        fail_later "torch-${app}" "/venv/${app} resolves torch ${got}, expected ${want}"
        continue
    fi
    echo "  ${app}: torch ${got}"
done

echo ""
echo "  -- gpu visibility --"

# One representative venv per BASE venv, not all eight: CUDA init is the slow part and
# apps sharing a base venv share the same torch install, so a second probe of the same
# base proves nothing new.
for probe in comfyui:main ostris:torch-2.7.1 voicebox:torch-2.9.1; do
    app="${probe%%:*}"; base="${probe##*:}"
    py="/venv/${app}/bin/python"
    [[ -x "$py" ]] || continue
    if "$py" -c 'import torch,sys; sys.exit(0 if torch.cuda.is_available() else 1)' 2>/dev/null; then
        echo "  ${app} (${base}): torch sees the GPU"
    else
        fail_later "cuda-${app}" "/venv/${app} (${base}) imports torch but torch.cuda.is_available() is False"
    fi
done

report_failures
test_pass "all ${#EXPECT[@]} app venvs resolve their declared torch (main=${main_ver})"
