logfile=$1
exec > >(tee -a "${logfile:-/var/log/portal/${PROC_NAME}.log}")
exec 2>&1