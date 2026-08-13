#!/bin/bash
# TEST_TIMEOUT=180
# Test: the provisioner, as actually built into this image.
#
# WHY THIS EXISTS SEPARATELY FROM 12-provisioning.sh
#
# 12 monitors a provisioning run that the template configured. The QA templates
# configure none, so on every gate cell it reaches "no provisioning in progress"
# and passes — the provisioner's own code is never executed by the gate at all.
# That is not a small gap: the provisioner has its own venv, its own pinned
# huggingface CLI, and it is the component that downloads every model a customer
# asks for. Two of this repo's field incidents landed in it.
#
# This file is the opposite shape: it configures nothing, waits for nothing, and
# exercises the shipped code directly.
#
# EVERY INVOCATION RUNS IN A BUILT ENVIRONMENT, NOT A CLEANED ONE, and that is
# the single most load-bearing thing in this file.
#
# The provisioner treats the environment as AUTHORITATIVE over the manifest, in
# two separate mechanisms that are easy to mistake for one:
#
#   _apply_env_overrides   (__main__.py)  8 x PROVISIONER_*  overrides settings
#   apply_env_conventions  (manifest.py)  6 x PROVISIONING_*  INJECTS resources
#
# plus PROVISIONING_SCRIPT, HF_TOKEN, CIVITAI_TOKEN, WORKSPACE, CONTAINER_ID and
# CONTAINER_API_KEY read elsewhere — and `load_manifest` expands $VARS in the
# manifest text, so the reachable surface is "the environment", not a list.
#
# The first version of this file scrubbed six variables it could name. What that
# missed is not academic:
#
#   PROVISIONING_POST_COMMANDS  runs the customer's shell commands, as root,
#                               from a test that runs at boot stage 70 — BEFORE
#                               the real provisioning at stage 75.
#   PROVISIONING_DOWNLOADS      adds the customer's downloads to a manifest that
#                               documents itself as touching no external network,
#                               and defeats the plan count in section 2.
#   PROVISIONER_FAILURE_ACTION  reaches failure.py's `vastai destroy instance`,
#                               so a failure here could DESTROY THE INSTANCE.
#   PROVISIONER_LOG_FILE        writes "Provisioning complete!" into the
#                               customer's /var/log/portal/provisioning.log
#                               (measured: 62 lines, twice).
#   HF_TOKEN / CIVITAI_TOKEN    real outbound token validation, 30.2s of it.
#
# So the direction is inverted: `env -i` and then add back, by NAME and mostly by
# VALUE, the few variables the provisioner needs. A deny-list is only ever as
# complete as its author's memory of a codebase that keeps growing new readers.
# An allowlist is complete in its names by construction — but only in its values
# if the values are pinned too, which is why $PATH is not forwarded (see below).
# PROVISIONER_STATE_DIR is part of the same discipline: without it this test
# shares /.provisioner_state with the real provisioning run and can mark stages
# complete that the real run has not performed.
#
# The discipline covers the FIXTURE as well, not just the provisioner. `curl`
# honours http_proxy and does not auto-bypass loopback, so a template-set proxy
# variable made the readiness probe fail and quietly skipped section 4 into a
# pass — a skip-as-pass the customer could select remotely, in the one section
# that exercises the real download path.
#
# NOTHING HERE TOUCHES THE EXTERNAL NETWORK. A test that downloads a real model
# turns a HuggingFace outage, a rate limit, or a host with no egress into a held
# release tag — the exact class the gate must never produce. The download path is
# exercised against a local HTTP server instead, and the third-party CLI contract
# is checked by whether our ARGUMENTS are rejected, which a transport failure
# never does.
source "$(dirname "$0")/../lib.sh"

# No skip guard. Every image that carries this test also builds the provisioner:
# the base Dockerfile does it directly, and external images get it from
# tools/convert-non-vast-image.sh, which runs the same `uv venv` + requirements
# install. The test suite itself arrives the same way — external images
# `COPY --from=base_image_source /ROOT /` from the build context, so they get the
# whole overlay. A missing provisioner is therefore a broken image, not a
# configuration we support, and a skip here would be a skip-as-pass path that can
# never legitimately fire.
PROVISIONER=/opt/instance-tools/bin/provisioner
[[ -x "$PROVISIONER" ]] || test_fail "provisioner not found at ${PROVISIONER} — every image carrying this suite ships it"

_tmp=$(mktemp -d)
_srv_pid=""
cleanup() {
    [[ -n "$_srv_pid" ]] && kill "$_srv_pid" 2>/dev/null
    rm -rf "$_tmp"
}
trap cleanup EXIT
mkdir -p "$_tmp/home" "$_tmp/ws" "$_tmp/state" "$_tmp/hfhome"

# THE allowlist — allowlisted NAMES with PINNED VALUES. Both halves matter.
#
# Forwarding $PATH through verbatim would have been an allowlist in name only:
# the provisioner spawns wget, git, apt-get, bash and vastai by bare name, so a
# template-set PATH=/workspace/bin:… runs attacker-chosen binaries as root at
# boot stage 70. The same actor can do that at stage 75 through the provisioner
# proper, so this is not a privilege boundary — but "complete by construction"
# has to mean the values too, or it is just a shorter deny-list.
#
#   PATH        pinned. The shim prepends its own venv, so this only has to
#               cover the system tools the provisioner shells out to.
#   HOME        uv/pip write caches; without it they fall back to '/' and warn
#   LANG        python's stdio encoding; a C locale mangles log output
#   WORKSPACE   manifest.py defaults dest paths under it — pointed at our temp
#               dir so a relative dest cannot land in the customer's workspace
#   PROVISIONER_STATE_DIR   keeps stage hashes out of the real run's state
#   OMP/MKL/…_NUM_THREADS, TOKIO_WORKER_THREADS
#               forwarded, not pinned. These are the ADR 0014/0025 caps that
#               12-cpu-thread-limits.sh sets on pid-starved high-core hosts, and
#               they are exactly the hosts where an unbounded hf download dies
#               with pthread_create EAGAIN. Production always has them; a
#               self-test that drops them would run the one configuration the
#               fleet never runs, and no CI runner has enough cores to notice.
#
# CONTAINER_ID and CONTAINER_API_KEY are absent ON PURPOSE, not by oversight:
# they are what failure.py needs to call the API, so without them the destroy
# path cannot fire even if some future edit re-enables it.
_PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
_prov() {
    env -i \
        PATH="$_PATH" \
        HOME="$_tmp/home" \
        LANG="${LANG:-C.UTF-8}" \
        WORKSPACE="$_tmp/ws" \
        PROVISIONER_STATE_DIR="$_tmp/state" \
        ${OMP_NUM_THREADS:+OMP_NUM_THREADS="$OMP_NUM_THREADS"} \
        ${MKL_NUM_THREADS:+MKL_NUM_THREADS="$MKL_NUM_THREADS"} \
        ${OPENBLAS_NUM_THREADS:+OPENBLAS_NUM_THREADS="$OPENBLAS_NUM_THREADS"} \
        ${NUMEXPR_NUM_THREADS:+NUMEXPR_NUM_THREADS="$NUMEXPR_NUM_THREADS"} \
        ${TOKIO_WORKER_THREADS:+TOKIO_WORKER_THREADS="$TOKIO_WORKER_THREADS"} \
        "$PROVISIONER" "$@"
}

# ── 1. The venv imports at all ────────────────────────────────────────
#
# The dependency-drift class: a resolver change that removes something an import
# depends on (setuptools dropping pkg_resources, a click major under a pinned
# typer). Those fail while the command object is being constructed, so --help is
# a real check, not a formality.
help_out=$(_prov --help 2>&1)
if [[ $? -ne 0 ]]; then
    fail_later "provisioner-help" "provisioner --help exited non-zero: ${help_out}"
elif grep -q "Traceback (most recent call last)" <<< "$help_out"; then
    fail_later "provisioner-import" "provisioner --help printed a traceback — the venv is broken: ${help_out}"
else
    echo "  provisioner --help: ok"
fi

# ── 2. A manifest covering each download class parses and plans ───────
cat > "$_tmp/m.yaml" <<YAML
version: 1
settings:
  log_file: ${_tmp}/prov.log
downloads:
  - url: https://huggingface.co/org/repo/resolve/main/weights.safetensors
    dest: /tmp/selftest-hf-file/
  - url: https://huggingface.co/org/repo
    dest: /tmp/selftest-hf-repo/
  - url: https://example.invalid/thing.bin
    dest: /tmp/selftest-generic/
YAML
dry_out=$(_prov --dry-run "$_tmp/m.yaml" 2>&1)
dry_rc=$?
if [[ $dry_rc -ne 0 ]]; then
    fail_later "provisioner-dry-run" "--dry-run over a valid manifest exited ${dry_rc}: ${dry_out}"
else
    # Count the per-download marker, NOT "DRY RUN" — the provisioner also logs a
    # `=== DRY RUN MODE ===` banner, so 3 downloads matched 4 lines and the old
    # `-lt 3` guard passed with an entire URL class silently dropped. It even
    # printed "planned 4 download(s)" for 3 downloads.
    # Anchored to the two DOWNLOAD classes. `[DRY RUN] Would download` alone also
    # matches "Would download and run PROVISIONING_SCRIPT" (__main__.py Phase 9) —
    # the same off-by-one as the `=== DRY RUN MODE ===` banner it replaced, one
    # line further along.
    planned=$(grep -cE '\[DRY RUN\] Would download (HF|wget) ' <<< "$dry_out")
    if [[ "$planned" -ne 3 ]]; then
        fail_later "provisioner-plan" \
            "--dry-run planned ${planned} downloads, expected exactly 3 — a URL class \
stopped being recognised or one is being planned twice"
    else
        echo "  --dry-run planned ${planned} download(s) across hf-file, hf-repo and generic"
    fi
fi

# ── A loopback HTTP server, used by both checks below ─────────────────
#
# Serves the download payload for section 4, and stands in for the HuggingFace
# endpoint in section 3. Pointing hf at a CLOSED port instead costs 24 SECONDS
# per run — the client retries a connection refusal with backoff — where a real
# server answering 404 fails in under a second. Measured: 23962ms vs 654ms, with
# identical usage-error discrimination.
mkdir -p "$_tmp/srv" "$_tmp/out"
head -c 4096 /dev/urandom > "$_tmp/srv/payload.bin"
# THE FIXTURE AND ITS PROBE GET THE SAME TREATMENT AS THE PROVISIONER, and the
# probe is the more important of the two. `curl` honours http_proxy/ALL_PROXY
# and does NOT auto-bypass loopback, so a template-set proxy variable — which
# the customer controls — makes this probe fail (rc=7, measured), _srv_up stays
# false, and section 4 degrades to a WARN and then a PASS. That is a
# remotely-selectable skip-as-pass in the one section that exercises the real
# download path, reached without the provisioner ever being involved.
# `--noproxy '*'` is belt and braces on top of the scrubbed environment.
#
# The server gets it too: `python3 -m http.server` would otherwise inherit
# PYTHONPATH/PYTHONSTARTUP from the same untrusted environment.
_env() { env -i PATH="$_PATH" HOME="$_tmp/home" LANG="${LANG:-C.UTF-8}" "$@"; }

# NO SUBSHELL, AND THEREFORE NOT VIA _env EITHER. `( ... ) &` makes $! the
# subshell rather than python, so the cleanup trap kills the wrapper and leaves
# the server holding the port for the life of the container — a stray listener
# in an image whose neighbouring test (28) exists to find stray listeners, and
# on any re-run the bind then fails, the probe finds nothing, and section 4
# degrades to a WARN and a PASS.
#
# `_env` is a shell FUNCTION, and `func args &` is a subshell too — so wrapping
# the server in it reintroduced exactly that bug, which is how it was found:
# running this file twice in one container skipped the download the second time.
# `env` as a COMMAND execs, keeping the pid, so it is spelled out here instead.
env -i PATH="$_PATH" HOME="$_tmp/home" LANG="${LANG:-C.UTF-8}" \
    python3 -m http.server 18973 --bind 127.0.0.1 --directory "$_tmp/srv" >/dev/null 2>&1 &
_srv_pid=$!

_srv_up=false
for _ in $(seq 1 30); do
    if _env curl -sf --noproxy '*' -o /dev/null "http://127.0.0.1:18973/payload.bin"; then
        _srv_up=true; break
    fi
    sleep 0.2
done

# ── 3. The huggingface CLI contract ───────────────────────────────────
#
# We invoke `hf` by argv, and its flags have moved under us before: 1.18.0 made
# --local-dir together with --cache-dir a hard error and broke every single-file
# download in the field. This runs the real argv against the real CLI and fails
# ONLY if the arguments are rejected as arguments.
#
# Host-independent by construction: no egress produces a transport error, which
# carries none of these markers, so a box with no internet passes. Verified in
# both directions in provisioner/tests/test_hf_cli_contract.py, which is the CI
# half of the same check.
_venv_py=/opt/instance-tools/provisioner/venv/bin/python
_hf=/opt/instance-tools/provisioner/venv/bin/hf
if [[ -x "$_hf" && -x "$_venv_py" ]]; then
    # Ask the SHIPPED module what argv it builds, rather than hardcoding it here.
    # Hardcoding was the first version of this check and it passed a mutation
    # that re-added --cache-dir — it tested hf's flags, not our use of them,
    # which is the half that actually broke in the field.
    _argv=$(PYTHONPATH=/opt/instance-tools/lib "$_venv_py" - <<'PYEOF' 2>/dev/null
import json, subprocess, tempfile
from provisioner.downloaders import huggingface as hf
from provisioner.schema import RetrySettings

seen = []
def fake(cmd, **_):
    seen.append(list(cmd))
    raise subprocess.CalledProcessError(1, cmd)
hf.run_cmd = fake
with tempfile.TemporaryDirectory() as d:
    try:
        hf._download_file("vast-nonexistent-org/vast-nonexistent-repo", "main",
                          "f.bin", d + "/f.bin",
                          RetrySettings(max_attempts=1, initial_delay=0,
                                        backoff_multiplier=1))
    except Exception:
        pass
print(json.dumps(seen[0]) if seen else "")
PYEOF
)
    if [[ -z "$_argv" ]]; then
        fail_later "hf-argv-capture" \
            "could not determine the argv the provisioner builds — the downloader module \
did not run in its own venv, which is itself a broken-image signal"
    else
        readarray -t _cmd < <(printf '%s' "$_argv" | "$_venv_py" -c \
            'import json,sys; [print(a) for a in json.load(sys.stdin)]')
        # argv[0] is the bare name "hf", which is only on PATH when the venv is
        # activated. Substitute the venv binary explicitly — otherwise this runs
        # nothing, "command not found" carries no usage markers, and the check
        # passes on every image including a broken one. (It did, until this line:
        # the mutation that re-added --cache-dir went green here while the CI half
        # caught it.)
        _cmd[0]="$_hf"
        # Same allowlist discipline as _prov, for the same reason and one more.
        # A customer's HF_TOKEN would otherwise be sent to the loopback fixture,
        # and HF_HUB_OFFLINE / HF_HOME / HF_HUB_DISABLE_XET / the proxy variables
        # all change what the CLI does — so an inherited environment could make
        # this contract check pass or fail for reasons that have nothing to do
        # with the argv it exists to check.
        hf_out=$(_env HF_ENDPOINT=http://127.0.0.1:18973 HF_HOME="$_tmp/hfhome" \
            ${TOKIO_WORKER_THREADS:+TOKIO_WORKER_THREADS="$TOKIO_WORKER_THREADS"} \
            no_proxy='*' NO_PROXY='*' "${_cmd[@]}" 2>&1)
        if grep -q "command not found\|No such file or directory" <<< "$hf_out"; then
            fail_later "hf-cli-missing" "could not execute the provisioner's hf: ${hf_out}"
        fi
        if grep -qiE "no such option|unrecognized arguments|cannot use both|got unexpected extra argument|^usage:" <<< "$hf_out"; then
            fail_later "hf-cli-contract" \
                "the hf CLI rejected the arguments the provisioner builds — the flag \
contract moved under us: ${hf_out}"
        else
            echo "  hf CLI accepts the provisioner's own argv (${#_cmd[@]} args)"
        fi
    fi
else
    # Not "absent (ok)": the provisioner venv always installs huggingface_hub[cli],
    # in both installers. A missing hf is a broken image for the same reason a
    # missing provisioner is.
    fail_later "hf-cli-missing" \
        "the provisioner venv has no hf CLI at ${_hf} — every image builds it with \
huggingface_hub[cli]"
fi

# ── 4. A real download, end to end, over loopback ─────────────────────
#
# Exercises what mocks cannot: subprocess_runner's PTY handling, FileLock, the
# dedup/state machinery, and the move into place. The payload is served by this
# instance to itself, so it depends on nothing outside the container.
if [[ "$_srv_up" != true ]]; then
    # Our own fixture failed to start. Not an image defect — say so rather than
    # blaming the provisioner for it.
    echo "  WARN: local HTTP fixture did not come up; skipping the end-to-end download"
    _download_verified=false
else
    # TWO retry budgets, and bounding only the inner one was not enough.
    # settings.retry governs the download; on_failure governs the whole
    # provisioner run and defaults to max_retries=3 with retry_delay=30, so a
    # genuine failure took ~60s, not "under a second" — three sections into a
    # file with TEST_TIMEOUT=180.
    #
    # log_file also matters: it defaults to /var/log/portal/provisioning.log, the
    # file the Instance Portal surfaces and 12-provisioning tails. A self-test has
    # no business writing "Provisioning complete!" into a customer's log.
    cat > "$_tmp/dl.yaml" <<YAML
version: 1
settings:
  log_file: ${_tmp}/prov.log
  retry:
    max_attempts: 1
    initial_delay: 0
on_failure:
  max_retries: 0
downloads:
  - url: http://127.0.0.1:18973/payload.bin
    dest: ${_tmp}/out/payload.bin
YAML
    dl_out=$(_prov "$_tmp/dl.yaml" 2>&1)
    dl_rc=$?
    if [[ $dl_rc -ne 0 ]]; then
        fail_later "provisioner-download" "a loopback download failed (rc=${dl_rc}): ${dl_out}"
    elif [[ ! -s "$_tmp/out/payload.bin" ]]; then
        fail_later "provisioner-download-dest" \
            "the provisioner reported success but ${_tmp}/out/payload.bin is missing or empty"
    elif ! cmp -s "$_tmp/srv/payload.bin" "$_tmp/out/payload.bin"; then
        fail_later "provisioner-download-content" \
            "downloaded file does not match the source — truncated or corrupted in transit"
    else
        echo "  end-to-end download: 4096 bytes, content verified"
    fi
fi

report_failures

# The summary must describe what actually ran. Claiming "real download" after the
# fixture failed to start is the same lie as a test reporting libraries verified
# when it found none.
if [[ "${_download_verified:-true}" == true ]]; then
    test_pass "provisioner verified (imports, manifest planning, hf argv contract, real download)"
else
    test_pass "provisioner verified (imports, manifest planning, hf argv contract; download SKIPPED — fixture unavailable)"
fi
