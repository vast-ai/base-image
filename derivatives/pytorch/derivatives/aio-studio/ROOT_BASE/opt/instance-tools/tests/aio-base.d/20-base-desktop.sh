#!/bin/bash
# Test: supervisor is up, and the desktop stack the base exists to provide is installed.
# TEST_TIMEOUT=600
source "$(dirname "$0")/../lib.sh"

# WHAT THIS BASE IS FOR. Two things: the shared torch venvs (10-base-venvs) and the
# desktop infrastructure — Selkies, VirtualGL, Chrome and the supervisor
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
# Selkies backs the Desktop portal entry. It must run, not just exist: the .deb's venv
# runs on the system Python, so a base that moved Python breaks it with the binary
# still on PATH. The GPU-node shim is what lets subset-GPU rentals use NVENC (ADR 0050).
if selkies_version="$(selkies --version 2>&1 | tail -n1)" && [[ $selkies_version == selkies\ * ]]; then
    echo "  selkies: ${selkies_version}"
else
    fail_later "selkies" "selkies does not run (${selkies_version:-not on PATH}); the Desktop portal entry has nothing behind it"
fi
if [[ -r /usr/local/lib/selkies/nvreach.so ]]; then
    echo "  nvreach shim: present"
else
    fail_later "selkies-nvreach" "/usr/local/lib/selkies/nvreach.so is missing; subset-GPU rentals will encode in software"
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
