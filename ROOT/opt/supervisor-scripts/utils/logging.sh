# Load PTY wrapper for progress bar support
. "$(dirname "${BASH_SOURCE[0]}")/pty.sh"
logpath=$1
logfile="${logpath:-/var/log/portal/${PROC_NAME}.log}"
[[ -f "$logfile" ]] && mv "${logfile}" "${logfile}.old"
# Rotate the clean log alongside the portal log (log-tee auto-derives
# /var/log/<name>.log from /var/log/portal/<name>.log)
if [[ -z "$logpath" ]]; then
    cleanlog="/var/log/${PROC_NAME}.log"
    [[ -f "$cleanlog" ]] && mv "${cleanlog}" "${cleanlog}.old"
fi
if command -v log-tee &>/dev/null; then
    exec > >(log-tee "${logfile}")
else
    exec > >(tee -a "${logfile}")
fi
exec 2>&1