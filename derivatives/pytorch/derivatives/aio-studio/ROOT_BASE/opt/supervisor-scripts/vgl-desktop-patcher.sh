#!/bin/bash
#
# vgl-desktop-patcher — add vglrun to .desktop Exec= lines so menu-launched
# apps get full VirtualGL acceleration (including dlopen interception).
#
# Runs as a supervisor service: patches existing files, then watches for new
# ones via inotify.  On stop (SIGTERM/SIGINT) the patches are reversed.
# Exits cleanly (0) when no GPU is detected or DISABLE_VGL=true.

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"

APPS_DIR="/usr/share/applications"

# ── DISABLE_VGL check ────────────────────────────────────────────────────────
if [[ "${DISABLE_VGL,,}" == "true" ]]; then
    echo "DISABLE_VGL is set — VGL desktop patching disabled"
    sleep 6
    exit 0
fi

# ── GPU check ────────────────────────────────────────────────────────────────
if ! nvidia-smi --query-gpu=uuid --format=csv,noheader 2>/dev/null | head -n1 | grep -q . \
   && ! ls -A /dev/dri 2>/dev/null | grep -q .; then
    echo "No GPU detected — skipping desktop VGL patching"
    exit 0
fi

# ── Patch a single .desktop file ─────────────────────────────────────────────
patch_desktop() {
    local file="$1"
    [[ -f "$file" ]] || return 0
    # Only patch if file has Exec= lines that don't already contain vglrun
    if grep -qP '^Exec\s*=' "$file" && ! grep -qP '^Exec\s*=.*vglrun' "$file"; then
        sudo sed -i -E \
            '/^Exec\s*=.*vglrun/! s/^(Exec\s*=\s*)(.*)/\1vglrun \2/' \
            "$file"
        echo "Patched: $file"
    fi
}

# ── Reverse all patches ──────────────────────────────────────────────────────
unpatch_all() {
    echo "Reversing VGL patches in ${APPS_DIR}..."
    for f in "${APPS_DIR}"/*.desktop; do
        [[ -f "$f" ]] || continue
        if grep -qP '^Exec\s*=.*vglrun ' "$f"; then
            sudo sed -i -E \
                's/^(Exec\s*=\s*)vglrun /\1/' \
                "$f"
            echo "Unpatched: $f"
        fi
    done
    echo "VGL patches reversed"
}

trap unpatch_all EXIT

# ── Patch all existing .desktop files ─────────────────────────────────────────
echo "Patching existing .desktop files in ${APPS_DIR}..."
for f in "${APPS_DIR}"/*.desktop; do
    patch_desktop "$f"
done
echo "Initial patching complete"

# ── Watch for newly installed .desktop files ──────────────────────────────────
if command -v inotifywait &>/dev/null; then
    echo "Watching ${APPS_DIR} for new .desktop files..."
    inotifywait -m -e create -e moved_to --format '%w%f' "$APPS_DIR" | \
    while read -r new_file; do
        [[ "$new_file" == *.desktop ]] && patch_desktop "$new_file"
    done
else
    echo "inotifywait not found — skipping watch for new .desktop files"
fi
