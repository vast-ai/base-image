logpath=$1
logfile="${logpath:-/var/log/portal/${PROC_NAME}.log}"
[[ -f "$logfile" ]] && mv "${logfile}" "${logfile}.old"
exec > >(tee -a "${logfile}")
exec 2>&1