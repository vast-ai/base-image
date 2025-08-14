kill_subprocesses() {
    local pid=$1
    local subprocesses=$(pgrep -P "$pid")
    
    for process in $subprocesses; do
        kill_subprocesses "$process"
    done
    
    if [[ -n "$subprocesses" ]]; then
        kill -TERM $subprocesses 2>/dev/null
    fi
}

cleanup() {
    local exit_code=$?
    kill_subprocesses $$
    sleep 2
    pkill -KILL -P $$ 2>/dev/null
    exit $exit_code
}

trap cleanup EXIT INT TERM