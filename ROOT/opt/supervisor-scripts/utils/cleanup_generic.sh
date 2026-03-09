_collect_descendants() {
    local pid=$1
    local children=$(pgrep -P "$pid" 2>/dev/null)
    for child in $children; do
        echo "$child"
        _collect_descendants "$child"
    done
}

cleanup() {
    local exit_code=$?
    # Collect the full process tree before killing anything
    local pids=$(_collect_descendants $$)
    if [[ -n "$pids" ]]; then
        kill -TERM $pids 2>/dev/null
        # Wait up to 5 seconds for processes to exit
        for i in {1..50}; do
            local alive=0
            for pid in $pids; do
                kill -0 "$pid" 2>/dev/null && alive=1 && break
            done
            [[ $alive -eq 0 ]] && break
            sleep 0.1
        done
        # Force-kill any survivors
        kill -KILL $pids 2>/dev/null
    fi
    exit $exit_code
}

trap cleanup EXIT INT TERM