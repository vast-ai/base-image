#!/bin/bash
if [[ ! -f /.launch && "${PORTAL_CONFIG,,}" != *"jupyter"* ]]; then
  export PORTAL_CONFIG="${PORTAL_CONFIG}|localhost:8080:18080:/:Jupyter"
fi

echo "PORTAL_CONFIG=\"$PORTAL_CONFIG\"" >> /etc/environment
