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
export PROVISIONING_SCRIPT="https://example.invalid/leak.sh"
export HF_TOKEN=hf_would_be_validated_against_huggingface_co
export CIVITAI_TOKEN=ct_would_be_validated_against_civitai_com
export WORKSPACE=/LEAKED_WORKSPACE
# Shadows the interpreter of the http.server fixture if it is not scrubbed.
export PYTHONPATH=/nonexistent-leaked-pythonpath
#
# NOTE WHAT IS *NOT* HERE: the proxy variables. They belong to a second run
# below, because in this one they would shadow everything after Phase 6 — see
# the comment there. Putting every hostile value in one run is what produced the
# dead canaries this fixture exists to fix, and it nearly did so again.

# ORDERING IS PART OF THE FIXTURE, and getting it wrong made three canaries
# unfireable while the harness still reported them "ok".
#
# The first version set PROVISIONING_APT=leaked-package. Under a leak that
# aborts the run at Phase 3 — about five phases BEFORE post_commands — so
# /LEAKED_POST_COMMAND could never appear, and the harness printed
# "ok  no customer post_command ran" while the log said
# "env convention: added 1 post commands". The loudest canary shadowed the
# worst one. So: nothing here may fail the run before the later phases execute.
#
#   - the download is served by THIS container, so it succeeds and Phase 6
#     completes (https://example.invalid could never have resolved, which is
#     why /LEAKED_DOWNLOAD was unreachable rather than merely unlikely)
#   - the dest is RELATIVE, which is the only way $WORKSPACE is consulted at
#     all; both manifests in test 13 use absolute dests, so an absolute one
#     here made the workspace canary structurally dead
#   - APT/PIP/GIT are gone entirely: each aborts the run early and none of them
#     tells us anything the survivors do not
export PROVISIONING_DOWNLOADS="http://127.0.0.1:18981/leak.bin|leaked-in-workspace/"

mkdir -p /var/log/portal /.provisioner_state /LEAKED_WORKSPACE /srv-leak
echo "planted by the harness" > /.provisioner_state/canary

# A reachable download for the canary above. If the env leaks, this lands in
# /LEAKED_WORKSPACE and BOTH the download and workspace canaries fire.
head -c 64 /dev/urandom > /srv-leak/leak.bin
python3 -m http.server 18981 --bind 127.0.0.1 --directory /srv-leak >/dev/null 2>&1 &
_leak_srv=$!
for _ in $(seq 1 25); do
    curl -sf --noproxy '*' -o /dev/null http://127.0.0.1:18981/leak.bin && break
    sleep 0.2
done

# A fake `vastai`, so the destroy path leaves EVIDENCE instead of being inferred
# from a log line. `on_failure=destroy: destroying instance` is only logged when
# a run fails, and the run under test succeeds — so the old grep printed "ok"
# whether or not PROVISIONER_FAILURE_ACTION leaked. That is the same mistake the
# HF-token checks made, kept after those were fixed.
# Placed in /usr/local/bin, NOT on a directory added to $PATH here: test 13 pins
# its own PATH, so a shim reachable only via the inherited PATH could never be
# invoked and the canary would be dead in a new way. /usr/local/bin is on the
# pinned PATH, so this fires if the destroy path is reached at all.
printf '#!/bin/bash\ntouch /LEAKED_DESTROY\nexit 0\n' > /usr/local/bin/vastai
chmod +x /usr/local/bin/vastai

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
sleep 0.5                 # let the accept land before clearing
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
check "no customer download ran"      "no" \
      "$(compgen -G '/LEAKED_WORKSPACE/leaked-in-workspace/*' >/dev/null && echo YES || echo no)"
# PROVISIONER_LOG_FILE: the file the Instance Portal surfaces and 12 tails.
check "customer log untouched"        "no" "$([[ -s /var/log/portal/provisioning.log ]] && echo YES || echo no)"
# STATE_DIR: marking a stage complete here makes the real run skip it.
check "product state untouched"       "canary" "$(ls /.provisioner_state | tr '\n' ' ' | xargs)"
# WORKSPACE: a relative dest must not resolve into the customer's workspace.
check "workspace untouched"           "" "$(ls -A /LEAKED_WORKSPACE)"
# HF_TOKEN/CIVITAI_TOKEN reaching auth.py means real outbound requests.
check "no connection to huggingface.co or civitai.com" "no" \
      "$([[ -e /LEAKED_EGRESS ]] && echo YES || echo no)"
# PROVISIONER_FAILURE_ACTION=destroy must not survive into the run. Asserted on
# the CAPABILITY (did anything invoke vastai?), not on a log line that only
# appears on a path this run does not take.
check "vastai destroy never invoked"  "no" "$([[ -e /LEAKED_DESTROY ]] && echo YES || echo no)"

# POSITIVE CONTROLS. A canary that cannot fire reports "ok" forever; three of
# the seven here did exactly that until a mutation showed it. Each negative
# check above is worth only as much as a demonstration that the thing it watches
# for is reachable at all.
echo "=== the canaries themselves work ==="
vastai destroy instance 999 >/dev/null 2>&1
check "a destroy WOULD have been recorded" "YES" \
      "$([[ -e /LEAKED_DESTROY ]] && echo YES || echo no)"
rm -f /LEAKED_DESTROY
curl -sf --noproxy '*' -o /LEAKED_WORKSPACE/probe.bin http://127.0.0.1:18981/leak.bin
check "the leak URL is reachable"          "YES" \
      "$([[ -s /LEAKED_WORKSPACE/probe.bin ]] && echo YES || echo no)"
rm -f /LEAKED_WORKSPACE/probe.bin

# The trap has to have been ARMED, or "no connection" proves nothing.
echo "=== the trap itself works ==="
(exec 3<>/dev/tcp/127.0.0.1/443) 2>/dev/null
sleep 0.5
check "a connection WOULD have been recorded" "YES" \
      "$([[ -e /LEAKED_EGRESS ]] && echo YES || echo no)"
kill "$_trap_pid" 2>/dev/null

# ── A SECOND RUN, for the proxy escape only ───────────────────────────
#
# `curl` honours http_proxy and does NOT auto-bypass loopback, so a template-set
# proxy — which the customer controls — made test 13's readiness probe fail,
# _srv_up stay false, and section 4 skip itself into a PASS. A remotely
# selectable skip-as-pass, in the only section that exercises the real download
# path.
#
# It gets its own run because these variables cannot coexist with the canaries
# above: under an environment leak the provisioner's own wget inherits them too,
# every loopback download fails, and Phase 6 aborts ~2 phases before
# post_commands — so /LEAKED_POST_COMMAND could never appear and the harness
# would report "ok" for the worst outcome it is meant to catch. That is exactly
# the shadowing that made three canaries dead the first time round.
echo "=== a hostile proxy does not skip the download section ==="
export http_proxy=http://127.0.0.1:9
export https_proxy=http://127.0.0.1:9
export ALL_PROXY=http://127.0.0.1:9
proxy_out=$(bash /opt/instance-tools/tests/base/13-provisioner-selftest.sh 2>&1)
proxy_rc=$?
check "still exits 0"                  "0"   "$proxy_rc"
check "download still ran"             "yes" \
      "$(grep -q 'content verified' <<< "$proxy_out" && echo yes || echo no)"
check "did not degrade to a WARN skip" "no"  \
      "$(grep -q 'fixture did not come up' <<< "$proxy_out" && echo YES || echo no)"
unset http_proxy https_proxy ALL_PROXY

[[ $FAIL -eq 0 ]] && echo "ALL SCENARIOS OK" || echo "SCENARIOS FAILED"
exit $FAIL
