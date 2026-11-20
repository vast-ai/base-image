#!/bin/bash
# Test: serverless mode — the pyworker starts, serves on :3000, and REACHES A SCORE.
#
# THIS LIVES IN THE ENGINE SUITE, NOT IN base/, AND THAT IS THE POINT.
# The base image ships pyworker.sh, but that only bootstraps a worker — what
# actually binds :3000 is the inference engine sitting behind it, which the base
# image does not have. As a base/ test this could never hold on a bare image:
# measured live on a driver-610 host, `pyworker: RUNNING` followed by
# `port 3000 not listening after 60s`. The real cost was not the red — it was
# that base-qa could then never set SERVERLESS=true, so this test and its
# sibling 85 had never executed once, anywhere.
#
# 85 stayed in base/: "the non-serverless services are stopped and their ports
# are closed" is a property the base image genuinely owns. This one is not.
#
# The `is_serverless` guard below keeps it dormant until a template turns
# serverless on, so it is inert (not skip-as-pass — there is nothing to assert
# when the mode is off) on today's engine QA cells and activates by itself when
# the serverless templates land. Enforced by linter rule L067.
#
# WHY RUNNING-AND-LISTENING IS NOT THE ASSERTION (ADR 0031 decision 4). Those two
# checks were the whole test, and a worker that binds :3000 and 500s every request
# passes both, as does one routing the engine's traffic to the wrong handler. The
# signal that means the worker actually WORKED is in the SDK: after benchmarking,
# vastai/serverless/server/lib/backend.py writes
#
#     with open(BENCHMARK_INDICATOR_FILE, "w") as f:   # ".has_benchmark", relative
#         f.write(str(max_throughput))                 # to $SERVER_DIR, its cwd
#
# so a parseable score > 0 means the worker drove the engine end to end and got
# answers back. `max_throughput` starts at 0 and only ever rises through `max(...)`,
# which is why zero is a failure rather than a slow box — no threshold is placed on
# the value, because throughput on a rented box of unknown contention is the
# canonical flaky gate (ADR 0029).
#
# FRESHNESS IS PART OF THE ASSERTION, NOT A BONUS. The same file is READ on startup
# to skip re-benchmarking:
#
#     with open(BENCHMARK_INDICATOR_FILE, "r") as f:
#         perf = float(f.readline()); return perf
#
# $WORKSPACE can be a host-bound volume shared across instances, so a leftover
# .has_benchmark makes the worker skip the benchmark entirely — and a test keyed on
# the file merely EXISTING would then certify a run that never happened. That is the
# `.syncing` defect from ADR 0029's audit wearing a different filename.
#
# THE BOOTSTRAP FLOATS, BY DESIGN (ADR 0031 decision 5). pyworker.sh fetches
# vast-ai/pyworker@main at boot and always will. The consequence is that this cell's
# verdict depends on an artifact this repo does not build, so a bootstrap failure is
# reported under its OWN label and the fetched revision is printed — a human must
# read "upstream worker bootstrap failed", never "the engine is broken".
#
# THE ASSERTIONS ARE LAUNCH-PATH AGNOSTIC, AND THAT IS DELIBERATE. There are two
# ways a worker gets onto the box and the earlier version of this file only allowed
# one. Production serverless templates launch
#     onstart: entrypoint.sh & ; curl .../start_server.sh | bash
# so the worker is a child of the ONSTART shell — and base pyworker.sh explicitly
# stands down when it sees start_server.sh referenced in /root/onstart.sh, leaving
# the supervisord program EXITED *by design*. `assert_service_running pyworker`
# would therefore have failed on the exact path every shipped serverless template
# takes. The other path is the supervisor one, where pyworker.sh does the bootstrap
# itself. Which of the two the QA template should take is a live architectural
# question, so this file asserts what is true either way: something is serving on
# :3000, and it reached a score. The supervisord state is REPORTED, never required.
# TEST_TIMEOUT=3600

source "$(dirname "$0")/../lib.sh"

is_serverless || test_skip "not in serverless mode"

# start_server.sh's own defaults, which is where these two paths come from:
#   WORKSPACE_DIR="${WORKSPACE_DIR:-/workspace}"; SERVER_DIR="$WORKSPACE_DIR/vast-pyworker"
# and it `cd "$SERVER_DIR"` before `python3 -m worker`, which is what makes the
# SDK's relative ".has_benchmark" land there.
WORKER_DIR="${WORKSPACE_DIR:-${WORKSPACE:-/workspace}}/vast-pyworker"
SCORE_FILE="${WORKER_DIR}/.has_benchmark"
BENCHMARK_TIMEOUT="${PYWORKER_BENCHMARK_TIMEOUT:-1800}"

# The supervisord program's state is evidence, not an assertion: EXITED is CORRECT
# on the onstart-curl path, where pyworker.sh deliberately stands down.
sup_state=$(supervisorctl status pyworker 2>/dev/null | awk '{print $2}')
echo "  pyworker (supervisord): ${sup_state:-not-configured}"

# The worker's HTTP handler on :3000 is the assertion, however it was launched.
if wait_for_port 3000 "${PYWORKER_PORT_TIMEOUT:-300}"; then
    echo "  pyworker: port 3000 listening"
else
    test_fail "nothing is listening on :3000 after ${PYWORKER_PORT_TIMEOUT:-300}s — no serverless worker is serving, on either launch path"
fi

# ── Upstream bootstrap, named as upstream ────────────────────────────
echo ""
echo "  -- upstream worker bootstrap --"
if [[ ! -d "$WORKER_DIR" ]]; then
    # Distinct label and distinct wording: this is vast-ai/pyworker@main failing to
    # land, not this image failing to serve.
    test_fail "upstream worker bootstrap failed — ${WORKER_DIR} does not exist (vast-ai/pyworker@main was not fetched)"
fi
worker_rev=$(git -C "$WORKER_DIR" rev-parse --short HEAD 2>/dev/null || echo "unknown")
worker_ref=$(git -C "$WORKER_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
echo "  pyworker: ${WORKER_DIR} @ ${worker_ref} ${worker_rev}"
echo "  (floating by design — this cell's verdict depends on an artifact this repo does not build)"

# ── The score ────────────────────────────────────────────────────────
# The anchor for "this run" is the pyworker process's own start time. /proc/<pid>
# carries it as the directory mtime, which is exact and needs no clock arithmetic
# against boot. A score older than the process that was supposed to produce it was
# produced by a PREVIOUS instance sharing this volume.
echo ""
echo "  -- benchmark score --"
# Anchor on the process that OWNS the :3000 socket, not on the supervisord program.
# It is the right process on both launch paths, and it is the one that would have
# written the score — a supervisord pid would be a wrapper on one path and absent on
# the other.
worker_pid=$(ss -tlnpH 2>/dev/null | awk '$4 ~ /:3000$/' | grep -oP 'pid=\K[0-9]+' | head -1)
started=""
[[ -n "$worker_pid" ]] && started=$(stat -c %Y "/proc/${worker_pid}" 2>/dev/null || true)
if [[ -z "$started" ]]; then
    test_fail "could not identify or time the process listening on :3000 — the score's freshness cannot be decided, and a score whose freshness is unknown certifies nothing"
fi
echo "  worker pid ${worker_pid}, started $(date -d "@${started}" -u +%FT%TZ)"

elapsed=0
last_report=0
while (( elapsed < BENCHMARK_TIMEOUT )); do
    if [[ -f "$SCORE_FILE" ]]; then
        written=$(stat -c %Y "$SCORE_FILE" 2>/dev/null || echo 0)
        (( written >= started )) && break
    fi
    if (( elapsed - last_report >= 60 )); then
        last_report=$elapsed
        echo "  [${elapsed}s] waiting for ${SCORE_FILE} to be written by this run"
    fi
    sleep 10
    elapsed=$((elapsed + 10))
done

if [[ ! -f "$SCORE_FILE" ]]; then
    test_fail "pyworker never wrote ${SCORE_FILE} within ${BENCHMARK_TIMEOUT}s — it bound :3000 but never completed a benchmark"
fi

written=$(stat -c %Y "$SCORE_FILE" 2>/dev/null || echo 0)
if (( written < started )); then
    # The stale-volume case, stated explicitly because the symptom is silence: the
    # worker READ this file, skipped benchmarking, and produced no score of its own.
    test_fail "${SCORE_FILE} predates this worker run ($(date -d "@${written}" -u +%FT%TZ) < $(date -d "@${started}" -u +%FT%TZ)) — the worker read a leftover score from a previous instance on this volume and skipped its own benchmark"
fi

score=$(head -1 "$SCORE_FILE" 2>/dev/null | tr -d '[:space:]')
if ! [[ "$score" =~ ^[0-9]+([.][0-9]+)?([eE][-+]?[0-9]+)?$ ]]; then
    test_fail "${SCORE_FILE} does not contain a parseable throughput: '${score}'"
fi
# `max_throughput` starts at 0 and only rises through max(), so 0 means every
# benchmark run produced nothing. No upper or lower threshold beyond that.
if ! awk -v s="$score" 'BEGIN { exit !(s > 0) }'; then
    test_fail "pyworker benchmark scored ${score} — max_throughput never rose above its 0 initial value, so no benchmark run returned a usable response"
fi

echo "  pyworker: benchmark score ${score} (written $(date -d "@${written}" -u +%FT%TZ), this run)"

test_pass "serverless pyworker verified — serving on :3000 and scored ${score} on this run"
