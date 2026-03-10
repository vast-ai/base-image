# Load PTY wrapper for progress bar support
. "$(dirname "${BASH_SOURCE[0]}")/pty.sh"
logpath=$1
logfile="${logpath:-/var/log/portal/${PROC_NAME}.log}"
[[ -f "$logfile" ]] && mv "${logfile}" "${logfile}.old"
exec > >(log-tee "${logfile}")
exec 2>&1