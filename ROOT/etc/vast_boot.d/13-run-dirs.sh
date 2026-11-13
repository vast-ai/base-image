#!/bin/bash
# 13-run-dirs.sh — recreate the directories the images need under /run.
#
# The Dockerfile creates /run/user/1001 (the session user's runtime dir) and
# /run/dbus at build time. Under runc /run is part of the container's overlay and
# both are still there. A runtime may mount a fresh tmpfs on /run instead (gVisor
# can), which hides them. So /run is treated as volatile: this stage puts them back
# before anything that needs them, and is a no-op when they are already right.
#
# Non-recursive and never through a symlink: only the named directory itself is
# created or corrected, a symlink at that path (or at its parent) is refused rather
# than followed, and nothing inside it is touched. Images that need more under
# /run create it themselves at runtime.
#
# SOURCED by boot_default.sh: never `exit` here, it would stop the container.

# Ensure <path> is a directory with <mode> and <uid>:<gid>. Echo nothing when it
# already is; refuse (return 1, with a message) if <path> or its parent is a
# symlink or <path> is something other than a directory.
_vast_run_dir() {
    local path="$1" mode="$2" uid="$3" gid="$4" parent
    parent=$(dirname "$path")
    if [[ -L "$path" || -L "$parent" ]]; then
        echo "[run-dirs] refusing ${path}: it or its parent is a symlink"
        return 1
    fi
    if [[ -e "$path" && ! -d "$path" ]]; then
        echo "[run-dirs] refusing ${path}: exists and is not a directory"
        return 1
    fi
    if [[ -d "$path" ]] && [[ "$(stat -c '%a %u %g' "$path" 2>/dev/null)" == "${mode#0} ${uid} ${gid}" ]]; then
        return 0
    fi
    install -d -m "$mode" -o "$uid" -g "$gid" "$path" \
        && echo "[run-dirs] ensured ${path} (mode ${mode}, owner ${uid}:${gid})"
}

# Skip the boot action when sourced lib-only (the test loads functions this way).
[[ -n "${_VAST_RUN_DIRS_LIB_ONLY:-}" ]] && return 0

_vast_run_dir /run/user/1001 0700 1001 0
_vast_run_dir /run/dbus 0755 0 0
