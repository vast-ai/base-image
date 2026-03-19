#!/bin/bash
# Test: users, permissions, and SSH configuration.
source "$(dirname "$0")/../lib.sh"

# 'user' account exists with UID 1001 and GID 0 (root group)
assert_user_exists user 1001
user_gid=$(id -g user 2>/dev/null) || test_fail "cannot get GID for user"
[[ "$user_gid" == "0" ]] || test_fail "user GID expected 0, got $user_gid"

# Sudoers file
if [[ -f /etc/sudoers.d/user ]]; then
    assert_file_mode /etc/sudoers.d/user 440
    grep -q "NOPASSWD" /etc/sudoers.d/user || test_fail "/etc/sudoers.d/user missing NOPASSWD"
else
    echo "  WARN: /etc/sudoers.d/user not found"
fi

# Home directory
assert_dir_exists /home/user

# Bashrc entrypoint markers (skip-if-absent — external images may not have them)
for rc in /root/.bashrc /home/user/.bashrc; do
    if [[ -f "$rc" ]]; then
        echo "  present: $rc"
    else
        echo "  absent (ok): $rc"
    fi
done

# SSH — soft checks (warn only, platform-managed)
if pidof sshd &>/dev/null; then
    echo "  sshd running"
    if ss -tln | grep -q ":22 "; then
        echo "  port 22 listening"
    else
        echo "  WARN: sshd running but port 22 not listening"
    fi
else
    echo "  WARN: sshd not running (may be platform-managed)"
fi

# propagate_ssh_keys.sh (skip if absent)
if [[ -x /opt/instance-tools/bin/propagate_ssh_keys.sh ]]; then
    echo "  propagate_ssh_keys.sh executable"
elif [[ -f /opt/instance-tools/bin/propagate_ssh_keys.sh ]]; then
    echo "  WARN: propagate_ssh_keys.sh exists but not executable"
fi

test_pass "users and permissions verified"
