# Provides the `pty` function for wrapping commands in a pseudo-terminal.
# Sourced automatically by logging.sh so all supervisor scripts can use it.
#
# Usage in supervisor scripts:
#   pty command arg1 arg2 ...
#
# If unbuffer is not available, falls back to direct execution.
pty() {
    if command -v unbuffer &>/dev/null; then
        unbuffer -p "$@"
    else
        "$@"
    fi
}
