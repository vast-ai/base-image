# Provides the `pty` function for wrapping commands in a pseudo-terminal.
# Sourced automatically by logging.sh so all supervisor scripts can use it.
#
# Usage in supervisor scripts:
#   pty command arg1 arg2 ...
#
# If unbuffer is not available, falls back to direct execution.
# Set DISABLE_PTY=true to bypass unbuffer even when available.
pty() {
    if [[ "${DISABLE_PTY:-false}" == "true" ]] || ! command -v unbuffer &>/dev/null; then
        "$@"
    else
        unbuffer -p "$@"
    fi
}
