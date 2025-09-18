if [[ "${SERVERLESS,,}" = "true" ]]; then
    echo "Skipping ${PROC_NAME} startup (Serverless)"
    sleep 6
    exit 0
fi