#!/bin/bash
# Test: logging infrastructure.
source "$(dirname "$0")/../lib.sh"

is_serverless && test_skip "logging not applicable in serverless mode"

# log-tee (skip if absent — external images may not have it)
if command -v log-tee &>/dev/null; then
    echo "  log-tee: present"
else
    echo "  absent (ok): log-tee"
fi

# /var/log/portal/ permissions
assert_dir_exists /var/log/portal
assert_file_mode /var/log/portal 1777

# At least one .log file in /var/log/portal/
log_count=$(find /var/log/portal/ -name '*.log' -type f 2>/dev/null | wc -l)
if [[ "$log_count" -gt 0 ]]; then
    echo "  log files in /var/log/portal/: ${log_count}"
else
    echo "  WARN: no .log files found in /var/log/portal/"
fi

# Check /var/log/ for ANSI escape codes in log files (sign of unbuffered color output)
ansi_files=0
for logfile in /var/log/portal/*.log; do
    [[ -f "$logfile" ]] || continue
    if grep -Pq '\x1b\[' "$logfile" 2>/dev/null; then
        echo "  WARN: ANSI escape codes in $(basename "$logfile")"
        ansi_files=$((ansi_files + 1))
    fi
done
if [[ "$ansi_files" -eq 0 ]]; then
    echo "  log files clean of ANSI escape codes"
fi

test_pass "logging infrastructure verified"
