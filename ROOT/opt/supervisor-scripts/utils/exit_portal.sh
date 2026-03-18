if [ -z "$1" ]; then
    echo "Error: No application name provided"
    exit 1
fi

search_term="$1"

# Portal config is not relevant in serverless mode — skip the check entirely
if [[ "${SERVERLESS,,}" = "true" ]]; then
    return 0 2>/dev/null || true
fi

# User can configure startup by removing the reference in /etc/portal.yaml - So wait for that file and check it
while [ ! -f "$(realpath -q /etc/portal.yaml 2>/dev/null)" ]; do
    echo "Waiting for /etc/portal.yaml before starting ${PROC_NAME}..."
    sleep 1
done

# Check for $search_term in the portal config
if ! grep -qiE "^[^#].*${search_term}" /etc/portal.yaml; then
    echo "Skipping ${PROC_NAME} startup (not in /etc/portal.yaml)"
    if [[ -n "${PROC_NAME}" ]]; then
        mkdir -p /tmp/supervisor-skip
        echo "${search_term}" > "/tmp/supervisor-skip/${PROC_NAME}"
    fi
    sleep 6
    exit 0
fi
# Clear skip marker if process is configured
[[ -n "${PROC_NAME}" ]] && rm -f "/tmp/supervisor-skip/${PROC_NAME}"