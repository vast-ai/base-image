#!/bin/bash
# Run the REAL base/13-provisioner-selftest.sh against the REAL provisioner venv,
# in a HOSTILE environment, and then check what it left behind.
#
# 13's whole safety argument is that it builds its environment with `env -i`
# rather than cleaning the one it inherits. That argument is only worth anything
# if something exercises it, because the failure mode is silent: a variable the
# author forgot is not a test failure, it is a test that quietly does something
# else — writes the customer's provisioning log, runs the customer's
# post_commands as root at boot stage 70, validates real tokens against
# huggingface.co, or (via PROVISIONER_FAILURE_ACTION) destroys the instance it
# is running on.
#
# So every variable the provisioner is known to read is exported here with a
# value whose use would be VISIBLE, and the assertions at the end are about
# side effects, not about the test's own verdict.
set +e

FAIL=0
check() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1: expected [$2] got [$3]"; FAIL=1; fi; }

export DEBIAN_FRONTEND=noninteractive
{ apt-get update -qq && apt-get install -y -qq wget curl ca-certificates ; } >/dev/null 2>&1 \
    || { echo "HARNESS SETUP FAILED"; exit 99; }

# The provisioner's own venv, built the way the Dockerfile builds it.
python3 -m venv /opt/instance-tools/provisioner/venv >/dev/null 2>&1
/opt/instance-tools/provisioner/venv/bin/pip -q install \
    -r /opt/instance-tools/lib/provisioner/requirements.txt >/dev/null 2>&1 \
    || { echo "HARNESS SETUP FAILED"; exit 99; }
[[ -x /opt/instance-tools/provisioner/venv/bin/hf ]] \
    || { echo "HARNESS SETUP FAILED"; exit 99; }

# ── The hostile environment ───────────────────────────────────────────
# Everything below is a variable the provisioner reads. If 13 leaks any of them,
# one of the checks at the bottom changes.
export CONTAINER_ID=999
export CONTAINER_API_KEY=would-authenticate-a-destroy
export PROVISIONER_FAILURE_ACTION=destroy          # reaches `vastai destroy instance`
export PROVISIONER_LOG_FILE=/var/log/portal/provisioning.log
export PROVISIONER_RETRY_MAX=9
export PROVISIONER_RETRY_DELAY=30
export PROVISIONER_WEBHOOK_URL=http://127.0.0.1:1/hook
export PROVISIONER_VENV=/venv/main
export PROVISIONING_POST_COMMANDS="touch /LEAKED_POST_COMMAND"
export PROVISIONING_DOWNLOADS="https://example.invalid/leak.bin|/LEAKED_DOWNLOAD"
export PROVISIONING_APT="leaked-package"
export PROVISIONING_PIP="leaked-package"
export PROVISIONING_GIT_REPOS="https://example.invalid/leak.git"
export PROVISIONING_SCRIPT="https://example.invalid/leak.sh"
export HF_TOKEN=hf_would_be_validated_against_huggingface_co
export CIVITAI_TOKEN=ct_would_be_validated_against_civitai_com
export WORKSPACE=/LEAKED_WORKSPACE

mkdir -p /var/log/portal /.provisioner_state /LEAKED_WORKSPACE
echo "planted by the harness" > /.provisioner_state/canary

# ── An egress trap for the two token-validation endpoints ─────────────
#
# auth.py hardcodes https://huggingface.co/api/whoami-v2 and
# https://civitai.com/api/v1/models, so a leaked HF_TOKEN or CIVITAI_TOKEN is a
# real outbound request from a QA cell — 30.2s of it, and a rate limit or an
# outage on either becomes a held release tag.
#
# Asserting on the provisioner's log does not work: 13 points log_file into its
# own temp dir and deletes it, so on the PASSING path there is nothing to grep,
# and a check that only has evidence when the test fails is not a check. Point
# both hostnames at ourselves and record the CONNECTION instead. The TLS
# handshake fails immediately afterwards, which does not matter — the connect is
# the evidence. Done after pip, so the venv build still reaches PyPI.
echo "127.0.0.1 huggingface.co civitai.com" >> /etc/hosts
python3 - <<'PY' &
import socket
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", 443))
s.listen(8)
while True:
    c, _ = s.accept()
    open("/LEAKED_EGRESS", "a").write("connect\n")
    c.close()
PY
_trap_pid=$!
for _ in $(seq 1 25); do
    (exec 3<>/dev/tcp/127.0.0.1/443) 2>/dev/null && break
    sleep 0.2
done
rm -f /LEAKED_EGRESS      # our own probe above counts as a connect
[[ -n "$_trap_pid" ]] || { echo "HARNESS SETUP FAILED"; exit 99; }

echo "=== running base/13-provisioner-selftest.sh ==="
out=$(bash /opt/instance-tools/tests/base/13-provisioner-selftest.sh 2>&1)
rc=$?
echo "$out" | sed 's/^/    | /'

echo "=== the test itself ==="
check "exits 0"            "0" "$rc"
check "reports a real download" "yes" \
      "$(grep -q 'content verified' <<< "$out" && echo yes || echo no)"

echo "=== what it did NOT do ==="
# PROVISIONING_POST_COMMANDS: arbitrary root commands, at boot stage 70, before
# the real provisioning at stage 75 has had a chance to run them itself.
check "no customer post_command ran"  "no" "$([[ -e /LEAKED_POST_COMMAND ]] && echo YES || echo no)"
check "no customer download ran"      "no" "$([[ -e /LEAKED_DOWNLOAD ]] && echo YES || echo no)"
# PROVISIONER_LOG_FILE: the file the Instance Portal surfaces and 12 tails.
check "customer log untouched"        "no" "$([[ -s /var/log/portal/provisioning.log ]] && echo YES || echo no)"
# STATE_DIR: marking a stage complete here makes the real run skip it.
check "product state untouched"       "canary" "$(ls /.provisioner_state | tr '\n' ' ' | xargs)"
# WORKSPACE: a relative dest must not resolve into the customer's workspace.
check "workspace untouched"           "" "$(ls -A /LEAKED_WORKSPACE)"
# HF_TOKEN/CIVITAI_TOKEN reaching auth.py means real outbound requests.
check "no connection to huggingface.co or civitai.com" "no" \
      "$([[ -e /LEAKED_EGRESS ]] && echo YES || echo no)"
# PROVISIONER_FAILURE_ACTION=destroy must not survive into the run.
check "destroy action not inherited"  "yes" \
      "$(grep -q 'on_failure=destroy' <<< "$out" && echo no || echo yes)"

# The trap has to have been ARMED, or "no connection" proves nothing.
echo "=== the trap itself works ==="
(exec 3<>/dev/tcp/127.0.0.1/443) 2>/dev/null
sleep 0.5
check "a connection WOULD have been recorded" "YES" \
      "$([[ -e /LEAKED_EGRESS ]] && echo YES || echo no)"
kill "$_trap_pid" 2>/dev/null

[[ $FAIL -eq 0 ]] && echo "ALL SCENARIOS OK" || echo "SCENARIOS FAILED"
exit $FAIL
