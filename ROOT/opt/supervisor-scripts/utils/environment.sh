set -a
. /etc/environment 2>/dev/null
[[ -f ${WORKSPACE}/.env ]] && . ${WORKSPACE}/.env 2>/dev/null
set +a
