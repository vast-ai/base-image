#!/bin/bash
# Test: every bundled app is a real, launchable supervisor program with a route.
# TEST_TIMEOUT=600
source "$(dirname "$0")/../lib.sh"

# WHAT autostart=false LEAVES TESTABLE, AND WHY IT IS WORTH TESTING.
#
# All nine programs ship autostart=false by design — the user picks one, because eight
# heavyweight apps cannot share a GPU. So this cannot assert any of them is RUNNING,
# and `service_running` is the wrong instrument here (see L086: a supervisord STATE is
# not a functional verdict).
#
# What it CAN assert is that each one is a program supervisord actually knows about.
# `supervisorctl status <name>` on an unknown program prints "<name>: ERROR (no such
# process)" and exits non-zero, which is exactly the outcome of a conf that failed to
# parse, a program renamed in one place and not the other, or a launcher script that
# was never COPYed in. Today NOTHING in this image catches any of those: the app never
# starts on boot, so a customer discovers it by clicking a portal button that does
# nothing.
#
# The pairing with PORTAL_CONFIG is the other half. A program with no route is
# unreachable; a route with no program is a dead button. Both are silent.
#
# WHAT THIS FILE DELIBERATELY DOES NOT DO: start any application. Nothing here proves
# ComfyUI serves, that Forge's UI loads, or that an app resolves its own non-torch
# imports — an app can be a known program with a route and a working torch and still
# die on first import. That coverage is MANUAL for now.
#
# When adding it, port the assertions from each application's own standalone image
# rather than writing new ones here: this image bundles the same applications, so the
# assertions should be the same assertions. Several of those images do not have a
# live-GPU gate of their own yet, which is the actual blocker — write it there, then
# bring it here.

wait_for_supervisor 60 || test_fail "supervisord did not become reachable"

# program : portal label. The label is matched case-insensitively against
# PORTAL_CONFIG, which is where this image declares what a user can reach.
declare -A APPS=(
    [comfyui]="ComfyUI"
    [forge]="SD Forge"
    [ai-toolkit]="AI Toolkit"
    [ace-step]="ACE Step"
    [unsloth-studio]="Unsloth Studio"
    [voicebox]="Voicebox"
    [wan2gp]="Wan2GP"
    [whisper-webui]="Whisper WebUI"
    [desktop]="Desktop"
)

portal_has() {
    [[ -n "${PORTAL_CONFIG:-}" ]] && printf '%s' "$PORTAL_CONFIG" | tr '|' '\n' | grep -qiF "$1"
}

echo "  -- supervisor programs --"
for app in "${!APPS[@]}"; do
    # `supervisorctl status` exits non-zero for a STOPPED program too, so the state word
    # is what distinguishes "known but not started" from "supervisord has never heard of
    # it" — not the exit code.
    state=$(supervisorctl status "$app" 2>/dev/null | awk '{print $2}')
    case "$state" in
        STOPPED|RUNNING|STARTING|EXITED|BACKOFF|FATAL)
            echo "  ${app}: known to supervisord (${state})" ;;
        "")
            fail_later "prog-${app}" "supervisorctl does not know the program '${app}' — its conf is missing or failed to parse" ;;
        *)
            fail_later "prog-${app}" "program '${app}' reported an unexpected state word '${state}'" ;;
    esac
done

echo ""
echo "  -- launcher scripts --"
for app in "${!APPS[@]}"; do
    # The conf can parse while the script it points at is absent; supervisord only finds
    # out when someone starts it.
    cmd=$(awk -F= '/^command[[:space:]]*=/{sub(/^[[:space:]]*/,"",$2); print $2; exit}' \
          "/etc/supervisor/conf.d/${app}.conf" 2>/dev/null | awk '{print $1}')
    if [[ -z "$cmd" ]]; then
        fail_later "cmd-${app}" "no command= in /etc/supervisor/conf.d/${app}.conf"
    elif [[ ! -x "$cmd" ]]; then
        fail_later "cmd-${app}" "${app} points at '${cmd}', which is not executable"
    else
        echo "  ${app}: ${cmd}"
    fi
done

echo ""
echo "  -- portal routes --"
for app in "${!APPS[@]}"; do
    label="${APPS[$app]}"
    # Desktop self-removes from PORTAL_CONFIG when selkies-gstreamer is absent (see
    # 05-aio-studio-env.sh), so a missing Desktop route is a legitimate state, not a
    # defect — report it rather than failing on it.
    if portal_has "$label"; then
        echo "  ${label}: routed"
    elif [[ "$app" == "desktop" ]]; then
        echo "  ${label}: not routed (selkies absent — expected on a headless build)"
    else
        fail_later "route-${app}" "no PORTAL_CONFIG entry labelled '${label}' — the app ships but nothing routes to it"
    fi
done

report_failures
test_pass "all ${#APPS[@]} bundled apps are launchable programs with routes"
