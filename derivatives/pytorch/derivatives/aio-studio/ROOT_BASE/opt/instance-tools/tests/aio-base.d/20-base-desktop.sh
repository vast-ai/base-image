#!/bin/bash
# Test: supervisor is up, and the desktop stack the base exists to provide is installed.
# TEST_TIMEOUT=600
source "$(dirname "$0")/../lib.sh"

# WHAT THIS BASE IS FOR. Two things: the shared torch venvs (10-base-venvs) and the
# desktop infrastructure — selkies-gstreamer, VirtualGL, Chrome and the supervisor
# program that ties them together. The desktop is the reason this base is large enough
# to be worth caching separately, so a base that built without it is the wrong artifact
# even though `docker build` succeeded.
#
# Like its sibling this ships in ROOT_BASE and therefore runs on both layers.

wait_for_supervisor 60 || test_fail "supervisord did not become reachable — nothing on this image can be launched"
echo "  supervisord: reachable"

echo ""
echo "  -- desktop program --"
# autostart=false by design (a desktop nobody asked for costs a GPU), so the assertion
# is that supervisord KNOWS it, not that it is running. An unknown program means the
# conf is missing or failed to parse, which presents to a user as a Desktop portal
# button that does nothing.
state=$(supervisorctl status desktop 2>/dev/null | awk '{print $2}')
case "$state" in
    STOPPED|RUNNING|STARTING|EXITED|BACKOFF|FATAL)
        echo "  desktop: known to supervisord (${state})" ;;
    "")
        fail_later "desktop-prog" "supervisorctl does not know the program 'desktop' — /etc/supervisor/conf.d/desktop.conf is missing or failed to parse" ;;
    *)
        fail_later "desktop-prog" "program 'desktop' reported an unexpected state word '${state}'" ;;
esac

for f in /opt/supervisor-scripts/desktop.sh \
         /opt/supervisor-scripts/vgl-desktop-patcher.sh \
         /opt/supervisor-scripts/nvidia-display-drivers.sh; do
    if [[ -x "$f" ]]; then
        echo "  $(basename "$f"): executable"
    else
        fail_later "script-$(basename "$f")" "${f} is missing or not executable — the desktop cannot start"
    fi
done

echo ""
echo "  -- desktop stack --"
# selkies is what 05-aio-studio-env.sh gates the Desktop PORTAL entry on: absent, the
# entry is silently stripped and the Desktop button disappears with no error anywhere.
# That makes its absence exactly the kind of failure a gate should catch.
if command -v selkies-gstreamer >/dev/null 2>&1; then
    echo "  selkies-gstreamer: on PATH"
else
    fail_later "selkies" "selkies-gstreamer is not on PATH — 05-aio-studio-env.sh will strip the Desktop portal entry and the button will simply vanish"
fi

# VirtualGL is what gives the desktop GPU-accelerated GL; without it the desktop starts
# and renders on the CPU, which looks like "the desktop is slow" rather than a defect.
if command -v vglrun >/dev/null 2>&1; then
    echo "  virtualgl: vglrun on PATH"
else
    fail_later "virtualgl" "vglrun is not on PATH — the desktop would fall back to software GL"
fi

if command -v google-chrome >/dev/null 2>&1; then
    echo "  google-chrome: on PATH"
else
    fail_later "chrome" "google-chrome is not on PATH"
fi

echo ""
echo "  -- dbus / polkit config --"
# The desktop needs these to start a session at all; they are plain COPYed files, so
# their absence means the overlay did not land rather than a package failure.
for f in /etc/dbus-1/container-system.conf /etc/dbus-1/container-session.conf; do
    [[ -f "$f" ]] && echo "  $(basename "$f"): present" \
        || fail_later "cfg-$(basename "$f")" "${f} is missing — the desktop session cannot start"
done

report_failures
test_pass "supervisor is up and the desktop stack is installed"
