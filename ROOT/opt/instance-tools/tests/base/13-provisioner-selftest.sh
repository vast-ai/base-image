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

# ── 1. The venv imports at all ────────────────────────────────────────
#
# The dependency-drift class: a resolver change that removes something an import
# depends on (setuptools dropping pkg_resources, a click major under a pinned
# typer). Those fail while the command object is being constructed, so --help is
# a real check, not a formality.
help_out=$("$PROVISIONER" --help 2>&1)
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
dry_out=$("$PROVISIONER" --dry-run "$_tmp/m.yaml" 2>&1)
dry_rc=$?
if [[ $dry_rc -ne 0 ]]; then
    fail_later "provisioner-dry-run" "--dry-run over a valid manifest exited ${dry_rc}: ${dry_out}"
else
    # Count the per-download marker, NOT "DRY RUN" — the provisioner also logs a
    # `=== DRY RUN MODE ===` banner, so 3 downloads matched 4 lines and the old
    # `-lt 3` guard passed with an entire URL class silently dropped. It even
    # printed "planned 4 download(s)" for 3 downloads.
    planned=$(grep -c '\[DRY RUN\] Would download' <<< "$dry_out")
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
# NO SUBSHELL. `( ... ) &` makes $! the subshell, not python, so the cleanup trap
# killed the wrapper and left the server holding the port for the life of the
# container — a stray listener in an image whose neighbouring test (28) exists to
# find stray listeners. Worse, on a re-run in the same container the bind failed,
# the probe hit the PREVIOUS run's server over a deleted docroot, and section 4
# degraded to a WARN: a skip-as-pass inside the file written to close skip-as-pass.
python3 -m http.server 18973 --bind 127.0.0.1 --directory "$_tmp/srv" >/dev/null 2>&1 &
_srv_pid=$!

_srv_up=false
for _ in $(seq 1 30); do
    if curl -sf -o /dev/null "http://127.0.0.1:18973/payload.bin"; then _srv_up=true; break; fi
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
        hf_out=$(HF_ENDPOINT=http://127.0.0.1:18973 "${_cmd[@]}" 2>&1)
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
    dl_out=$("$PROVISIONER" "$_tmp/dl.yaml" 2>&1)
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

test_pass "provisioner verified (imports, manifest planning, hf argv contract, real download)"
