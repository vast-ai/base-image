#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "ACE Step"

. /venv/ace-step/bin/activate

while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 5
done

export ACESTEP_LM_MODEL_PATH=${ACESTEP_LM_MODEL_PATH:=acestep-5Hz-lm-4B}

# Start ACE Step API in background
echo "Starting ACE Step API..."
cd "${WORKSPACE}/ACE-Step-1.5"
acestep-api --port 8001 &
API_PID=$!

# Wait for ACE Step API to be ready
until (curl -s -o /dev/null -w '%{http_code}' http://localhost:8001/docs || echo "000") | grep -q 200; do
    if ! kill -0 $API_PID 2>/dev/null; then
        echo "ACE Step API process died unexpectedly"
        exit 1
    fi
    echo "Waiting for ACE Step API..."
    sleep 5
done
echo "ACE Step API is up!"

# Start ACE Step UI (foreground)
echo "Starting ACE Step UI"
cd "${WORKSPACE}/ace-step-ui"
. /opt/nvm/nvm.sh
# The UI is TWO processes and start.sh only guarantees one of them. It runs
#   cd server && npm run dev &      <- the Node backend, port ${PORT:-3001}
#   npm run dev &                   <- the vite frontend, port ${FRONTEND_PORT:-3000}
# Both are BACKGROUNDED, so start.sh's `set -e` cannot see either fail. When the
# backend died (better-sqlite3 aborting under Node 24, fixed in the Dockerfile), the
# frontend still bound 3000 and served a UI whose every /api/* call was refused —
# reaching the user as a 500 on "create user", three layers from the cause, with
# supervisor reporting the service RUNNING throughout.
#
# So wait for the BACKEND the frontend proxies to, not just the frontend. Backgrounded
# because start.sh runs in the foreground and owns this shell.
(
    _ui_deadline=$(( SECONDS + ${ACE_STEP_UI_READY_TIMEOUT:-180} ))
    _ui_port="${PORT:-3001}"
    until curl -sf -o /dev/null --max-time 5 "http://127.0.0.1:${_ui_port}/" \
       || curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:${_ui_port}/" | grep -qE '^[2345]'; do
        if (( SECONDS >= _ui_deadline )); then
            echo "FATAL: ACE Step UI backend never bound ${_ui_port} — the UI will serve but every /api call is refused"
            echo "  (check the log above for a better-sqlite3 / node assertion)"
            # Kill the process group so supervisor sees the failure and restarts,
            # rather than leaving a half-working UI up.
            kill -TERM -$$ 2>/dev/null || kill -TERM $$ 2>/dev/null
            exit 1
        fi
        sleep 3
    done
    echo "ACE Step UI backend is up on ${_ui_port}"
) &

ACESTEP_PATH="${WORKSPACE}/ACE-Step-1.5/" pty ./start.sh
