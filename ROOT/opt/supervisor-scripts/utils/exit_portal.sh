if [ -z "$1" ]; then
    echo "Error: No application name provided"
    exit 1
fi

search_term="$1"

# User can configure startup by removing the reference in /etc.portal.yaml - So wait for that file and check it
while [ ! -f "$(realpath -q /etc/portal.yaml 2>/dev/null)" ]; do
    echo "Waiting for /etc/portal.yaml before starting ${PROC_NAME}..."
    sleep 1
done

# Check for $search_term in the portal config
if ! grep -qiE "^[^#].*${search_term}" /etc/portal.yaml; then
    echo "Skipping ${PROC_NAME} startup (not in /etc/portal.yaml)"
    sleep 6
    exit 0
fi