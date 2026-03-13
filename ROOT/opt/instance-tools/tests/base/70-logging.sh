#!/bin/bash
# Test: logging infrastructure.
#
# log-tee writes colored output to /var/log/portal/ (for the portal web UI)
# and a clean (ANSI-stripped) copy to /var/log/ (for CLI/log aggregation).
source "$(dirname "$0")/../lib.sh"

is_serverless && test_skip "logging not applicable in serverless mode"

# log-tee (required for IMAGE_TYPE=vast, optional for external)
if command -v log-tee &>/dev/null; then
    echo "  log-tee: present"
else
    if is_vast_image; then
        test_fail "log-tee not found (required for IMAGE_TYPE=vast)"
    fi
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

# Every portal log should have a clean copy in /var/log/
missing_clean=0
for logfile in /var/log/portal/*.log; do
    [[ -f "$logfile" ]] || continue
    name=$(basename "$logfile")
    if [[ -f "/var/log/${name}" ]]; then
        # Clean copy must not contain ANSI escape codes
        if grep -Pq '\x1b\[' "/var/log/${name}" 2>/dev/null; then
            test_fail "ANSI escape codes in clean log /var/log/${name}"
        fi
    else
        echo "  WARN: /var/log/portal/${name} has no clean copy at /var/log/${name}"
        missing_clean=$((missing_clean + 1))
    fi
done

if [[ "$missing_clean" -eq 0 ]]; then
    echo "  all portal logs have clean copies in /var/log/"
fi

test_pass "logging infrastructure verified"
