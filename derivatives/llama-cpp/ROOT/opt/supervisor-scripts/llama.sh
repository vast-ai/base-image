#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "Llama.cpp"

echo "Starting Llama.cpp"

cd "${WORKSPACE}/"
if [[ -n "${LLAMA_MODEL:-}" ]]; then
  llama-server -hf "$LLAMA_MODEL" ${LLAMA_ARGS:---port 18000} 2>&1
else
  echo "Model not specified.  Exiting"
  sleep 6
fi
