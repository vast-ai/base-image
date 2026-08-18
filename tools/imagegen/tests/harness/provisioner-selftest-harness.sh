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

# Refuse to run outside a container. Every step below writes absolute paths
# (/usr/local/bin/vastai, /LEAKED_*, /etc/hosts) and binds privileged ports — in
# CI it is inside `docker run --rm`, but a developer running it directly would
# scribble across their host. /.dockerenv is what actually guards this in CI;
# $VAST_HARNESS_OK is the manual override for a non-Docker runtime, and is
# the belt to that suspenders.
[[ -f /.dockerenv || -n "$VAST_HARNESS_OK" ]] \
    || { echo "refusing to run outside a container (set VAST_HARNESS_OK to override)"; exit 1; }

FAIL=0
check() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1: expected [$2] got [$3]"; FAIL=1; fi; }

export DEBIAN_FRONTEND=noninteractive
# git is needed for the WORKSPACE canary: a PROVISIONING_GIT_REPOS entry with no
# dest is the ONLY thing that resolves a default path under $WORKSPACE (a
# relative DOWNLOAD dest resolves against CWD, not $WORKSPACE — see the workspace
# canary comment below), so exercising it requires a real `git clone`.
{ apt-get update -qq && apt-get install -y -qq wget curl ca-certificates git ; } >/dev/null 2>&1 \
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
# Hostile-but-INERT: a nonexistent dir on PYTHONPATH changes nothing even when
# leaked, so no canary watches it and none could fire on it. Kept only so the
# variable is present in the environment 13 must scrub; it proves nothing on its
# own. (A live PYTHONPATH canary would need a real sitecustomize.py shim and a
# scrub of every python 13 itself spawns — not done, deliberately.)
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
# The download (Phase 6) and git clone (Phase 4) are both served by THIS
# container, so they SUCCEED and the run proceeds to post_commands (Phase 8).
#
# THE DOWNLOAD CANARY uses an ABSOLUTE dest. A relative download dest does NOT
# resolve under $WORKSPACE — it lands in the provisioner's CWD (`/` here), so the
# earlier `leaked-in-workspace/` fixture that claimed to test $WORKSPACE via the
# download path was structurally dead: manifest.py reads WORKSPACE only in
# _repo_dest_from_url, the GIT-repo default-dest helper. So this canary just
# proves a customer download RAN, into a dedicated dir.
export PROVISIONING_DOWNLOADS="http://127.0.0.1:18981/leak.bin|/LEAKED_DOWNLOAD/"
#
# THE WORKSPACE CANARY uses a git repo with NO dest — the one input that actually
# resolves a default path under $WORKSPACE (${WORKSPACE}/{repo_name}). Under a
# leak WORKSPACE=/LEAKED_WORKSPACE, so the clone lands in /LEAKED_WORKSPACE. The
# repo is local and reachable, so the clone succeeds and does not abort Phase 4.
export PROVISIONING_GIT_REPOS="file:///srv-leak/leakrepo"

mkdir -p /var/log/portal /.provisioner_state /LEAKED_WORKSPACE /LEAKED_DOWNLOAD /srv-leak
echo "planted by the harness" > /.provisioner_state/canary

# A reachable download for the download canary. If the env leaks, this lands in
# /LEAKED_DOWNLOAD and the download canary fires.
head -c 64 /dev/urandom > /srv-leak/leak.bin
python3 -m http.server 18981 --bind 127.0.0.1 --directory /srv-leak >/dev/null 2>&1 &
_leak_srv=$!
for _ in $(seq 1 25); do
    curl -sf --noproxy '*' -o /dev/null http://127.0.0.1:18981/leak.bin && break
    sleep 0.2
done

# A local git repo for the WORKSPACE canary. Cloned into ${WORKSPACE}/leakrepo
# when PROVISIONING_GIT_REPOS leaks; a successful clone leaves files there.
git config --global user.email harness@localhost
git config --global user.name harness
git config --global init.defaultBranch main
git config --global --add safe.directory '*'
git init -q /srv-leak/leakrepo
( cd /srv-leak/leakrepo && echo leaked > file && git add -A && git commit -qm init ) >/dev/null 2>&1

# A hostile binary on a directory PREPENDED to the harness's own PATH, to pin the
# PATH allowlist in 13 (`_PATH`). If 13 forwards $PATH verbatim instead of
# pinning it, the provisioner resolves `wget` from here and runs attacker-chosen
# code as root at boot stage 70. The shim touches a marker and then EXECs the
# real wget, so the download still succeeds and later phases are not shadowed.
mkdir -p /hostile-bin
_real_wget=$(command -v wget)
printf '#!/bin/bash\ntouch /LEAKED_PATH_HIJACK\nexec %s "$@"\n' "$_real_wget" > /hostile-bin/wget
chmod +x /hostile-bin/wget
export PATH=/hostile-bin:$PATH

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

# A `git` shim on the PINNED path, for the same reason but a sharper one.
# `_workspace_touched` watches /LEAKED_WORKSPACE, so it only fires when WORKSPACE
# leaks AND a git-repo variable leaks. Leak PROVISIONING_GIT_REPOS alone and the
# clone lands in the WORKSPACE fallback (/workspace, per Dockerfile ENV) — a root
# clone of an attacker-named repo into the customer's workspace, reported as
# "ok workspace untouched". This shim fires on the ACT of cloning, whatever the
# destination, so the canary no longer depends on the variable it is guarding.
# 13 never runs git on its own path, so it is silent on a green run.
printf '#!/bin/bash\ntouch /LEAKED_GIT\nexec /usr/bin/git "$@"\n' > /usr/local/bin/git
chmod +x /usr/local/bin/git

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

# Each canary is written as a REUSABLE EXPRESSION, evaluated once here and once
# in the positive-control block below, so a control cannot pass while the canary
# it is meant to prove is dead. A control that writes a file and then asserts on
# that file directly (rather than through the canary's own predicate) is the
# decoration this fixture exists to remove.
_post_command_leaked()  { [[ -e /LEAKED_POST_COMMAND ]]; }
_download_leaked()      { [[ -n "$(ls -A /LEAKED_DOWNLOAD 2>/dev/null)" ]]; }
_log_leaked()           { [[ -s /var/log/portal/provisioning.log ]]; }
_state_touched()        { [[ "$(ls /.provisioner_state 2>/dev/null | tr '\n' ' ' | xargs)" != "canary" ]]; }
_workspace_touched()    { [[ -n "$(ls -A /LEAKED_WORKSPACE 2>/dev/null)" ]]; }
_egress_leaked()        { [[ -e /LEAKED_EGRESS ]]; }
_destroy_invoked()      { [[ -e /LEAKED_DESTROY ]]; }
_git_ran()              { [[ -e /LEAKED_GIT ]]; }
_path_hijacked()        { [[ -e /LEAKED_PATH_HIJACK ]]; }
yn() { "$1" && echo YES || echo no; }

echo "=== what it did NOT do ==="
# PROVISIONING_POST_COMMANDS: arbitrary root commands, at boot stage 70, before
# the real provisioning at stage 75 has had a chance to run them itself.
check "no customer post_command ran"  "no" "$(yn _post_command_leaked)"
# PROVISIONING_DOWNLOADS: a customer download reaching the real download path.
check "no customer download ran"      "no" "$(yn _download_leaked)"
# PROVISIONER_LOG_FILE: the file the Instance Portal surfaces and 12 tails.
check "customer log untouched"        "no" "$(yn _log_leaked)"
# STATE_DIR: marking a stage complete here makes the real run skip it.
check "product state untouched"       "no" "$(yn _state_touched)"
# WORKSPACE: a git repo with no dest must not clone into the customer's workspace.
check "workspace untouched"           "no" "$(yn _workspace_touched)"
# Independent of WORKSPACE: fires on the clone itself, wherever it lands.
check "no customer git clone ran"     "no" "$(yn _git_ran)"
# HF_TOKEN/CIVITAI_TOKEN reaching auth.py means real outbound requests.
check "no connection to huggingface.co or civitai.com" "no" "$(yn _egress_leaked)"
# PROVISIONER_FAILURE_ACTION=destroy must not survive into the run. Asserted on
# the CAPABILITY (did anything invoke vastai?), not on a log line that only
# appears on a path this run does not take.
check "vastai destroy never invoked"  "no" "$(yn _destroy_invoked)"
# PATH: the pinned _PATH must keep the provisioner off the hostile-bin shim.
check "no hostile-PATH binary ran"    "no" "$(yn _path_hijacked)"

# POSITIVE CONTROLS. A canary that cannot fire reports "ok" forever; three of
# the seven here did exactly that until a mutation showed it. Each control below
# makes the watched-for thing happen and then evaluates THE CANARY'S OWN
# EXPRESSION — not a file written directly — so it proves that expression can go
# YES, which is the only thing that makes the "no" above meaningful.
echo "=== the canaries themselves work ==="
touch /LEAKED_POST_COMMAND
check "post_command canary can fire"  "YES" "$(yn _post_command_leaked)"
rm -f /LEAKED_POST_COMMAND

# A real fetch of the leak URL into the watched dir — proving BOTH that the
# fixture URL serves (a dead server would kill the canary a different way) and
# that the canary's own expression fires on the result.
curl -sf --noproxy '*' -o /LEAKED_DOWNLOAD/control-probe http://127.0.0.1:18981/leak.bin
check "download canary can fire (via the leak URL)" "YES" "$(yn _download_leaked)"
rm -f /LEAKED_DOWNLOAD/control-probe

echo "control line" >> /var/log/portal/provisioning.log
check "log canary can fire"           "YES" "$(yn _log_leaked)"
: > /var/log/portal/provisioning.log

echo "abc" > /.provisioner_state/control.hash
check "state canary can fire"         "YES" "$(yn _state_touched)"
rm -f /.provisioner_state/control.hash

# A real clone of the fixture repo into the watched workspace — proving BOTH
# that the repo is clonable (an unclonable fixture would kill the canary a
# different way: Phase 4 aborts and shadows everything after it) and that the
# canary's own expression fires on the result.
git clone -q file:///srv-leak/leakrepo /LEAKED_WORKSPACE/control-clone 2>/dev/null
check "workspace canary can fire (via a real clone)" "YES" "$(yn _workspace_touched)"
# The workspace control above also clones, which would leave the marker set and
# make this control vacuous — clear it so the probe proves the shim, not history.
rm -f /LEAKED_GIT
git clone -q file:///srv-leak/leakrepo /tmp/gitprobe >/dev/null 2>&1
check "git canary can fire"           "YES" "$(yn _git_ran)"
rm -rf /tmp/gitprobe /LEAKED_GIT
rm -rf /LEAKED_WORKSPACE/control-clone

vastai destroy instance 999 >/dev/null 2>&1
check "destroy canary can fire"       "YES" "$(yn _destroy_invoked)"
rm -f /LEAKED_DESTROY

# The PATH shim, exercised through the same marker the canary reads.
( PATH=/hostile-bin:$PATH wget --version >/dev/null 2>&1 )
check "PATH-hijack canary can fire"   "YES" "$(yn _path_hijacked)"
rm -f /LEAKED_PATH_HIJACK

# The trap has to have been ARMED, or "no connection" proves nothing.
echo "=== the trap itself works ==="
(exec 3<>/dev/tcp/127.0.0.1/443) 2>/dev/null
sleep 0.5
check "egress canary can fire"        "YES" "$(yn _egress_leaked)"
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
