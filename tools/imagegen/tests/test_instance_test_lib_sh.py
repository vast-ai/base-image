"""Tests for ROOT/opt/instance-tools/tests/lib.sh — the readiness helpers.

WHY THIS FILE EXISTS. `lib.sh` is sourced by every instance test in every image
and it is where the fix for the 2026-08-18 QA failures lives (ADR 0029). It had
NO automated coverage: `wait_for_supervisor` could be replaced by `return 0`,
and `rc <= 3` — which makes "nothing is listening" read as ready, the exact
inversion of the fix — could be reintroduced, with the whole repo still green.

It needs no GPU, no container and no supervisord. A stub `supervisorctl` on PATH
is enough, because what these helpers encode is *when to stop asking*, and that
is decided entirely by exit codes and the clock.
"""
from __future__ import annotations

import itertools
import os
import subprocess
import time
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[3] / "ROOT/opt/instance-tools/tests/lib.sh"

# A supervisorctl that replays a script of exit codes, one per invocation, then
# repeats the last one forever. `$1` of each entry is the exit code and the rest
# is what it prints — which is how the real one reports both state and error.
STUB = r'''#!/bin/bash
n=$(cat "$STATE/n" 2>/dev/null || echo 0)
mapfile -t plan < "$STATE/plan"
i=$(( n < ${#plan[@]} ? n : ${#plan[@]} - 1 ))
echo $(( n + 1 )) > "$STATE/n"
entry="${plan[$i]}"
rc="${entry%% *}"
out="${entry#* }"
[[ "$out" != "$entry" ]] && echo "$out"
exit "$rc"
'''


_SEQ = itertools.count()


def _env(tmp_path, plan: list[str]):
    sandbox = tmp_path / f"run{next(_SEQ)}"
    state = sandbox / "state"
    state.mkdir(parents=True)
    (state / "plan").write_text("\n".join(plan) + "\n")
    bindir = sandbox / "bin"
    bindir.mkdir()
    stub = bindir / "supervisorctl"
    stub.write_text(STUB)
    stub.chmod(0o755)
    env = dict(os.environ, STATE=str(state), PATH=f"{bindir}:{os.environ['PATH']}")
    return env, state


def _run(tmp_path, plan: list[str], script: str, env_extra: dict | None = None):
    env, state = _env(tmp_path, plan)
    env.update(env_extra or {})
    started = time.monotonic()
    p = subprocess.run(["bash", "-c", f'source "{LIB}"\n{script}'],
                       capture_output=True, text=True, env=env, timeout=120)
    return p, time.monotonic() - started, state


# ── wait_for_supervisor ───────────────────────────────────────────────

def test_a_socket_that_arrives_late_is_waited_for(tmp_path):
    """The whole point: exit 4 is "nothing listening yet", not "broken"."""
    p, elapsed, _ = _run(tmp_path, ["4", "4", "3 all good"],
                         'wait_for_supervisor 10 && echo READY')
    assert "READY" in p.stdout
    assert elapsed >= 2, "must actually have waited"


def test_a_socket_that_never_arrives_times_out_and_reports_failure(tmp_path):
    p, elapsed, _ = _run(tmp_path, ["4"], 'wait_for_supervisor 3 || echo GAVEUP')
    assert "GAVEUP" in p.stdout
    assert 3 <= elapsed < 12, f"budget is wall-clock seconds, took {elapsed:.1f}s"


def test_the_budget_is_WALL_CLOCK_not_a_count_of_sleeps(tmp_path):
    """Each poll runs supervisorctl — a Python start, seconds on a loaded host.
    Counting iterations made a declared 60s mean minutes: the same defect class
    (a budget that does not measure what it names) this file exists to fix."""
    p, elapsed, _ = _run(tmp_path, ["4"], 'wait_for_supervisor 3 || true')
    assert elapsed < 12


def test_an_UNCLASSIFIED_error_is_not_treated_as_ready(tmp_path):
    """`rc <= 3` also accepted 1, supervisorctl's generic error — e.g. EACCES on
    the chmod=0700 socket. A readiness primitive must never fail OPEN."""
    p, _, _ = _run(tmp_path, ["1 something went wrong"],
                   'wait_for_supervisor 2 && echo READY || echo NOTREADY')
    assert "NOTREADY" in p.stdout, "exit 1 must not read as a live socket"


def test_success_is_memoised_so_every_helper_does_not_re_probe(tmp_path):
    p, _, state = _run(tmp_path, ["3 ok"],
                       'wait_for_supervisor 5; wait_for_supervisor 5; '
                       'wait_for_supervisor 5; echo DONE')
    assert "DONE" in p.stdout
    assert int((state / "n").read_text()) == 1, "memo must prevent re-probing"


def test_failure_is_memoised_so_a_dead_socket_is_not_re_proved_six_times(tmp_path):
    """67-service-functionality makes six service_running calls with no gate of
    its own. Re-proving a dead socket each time cost ~360s of rented GPU to
    rediscover a fact already known."""
    p, elapsed, _ = _run(tmp_path, ["4"],
                         'for i in 1 2 3 4 5 6; do service_running foo || true; done; echo DONE',
                         {"SUPERVISOR_READY_TIMEOUT": "3"})
    assert "DONE" in p.stdout
    # One budget total, not six. Without the failure memo this is 6x3s.
    assert elapsed < 10, f"six calls re-paid the budget: {elapsed:.1f}s"


def test_a_LONGER_budget_may_still_try_after_a_shorter_one_failed(tmp_path):
    """The failure memo records how long was waited, so it cannot poison a later
    caller that was willing to wait longer."""
    p, _, state = _run(tmp_path, ["4", "4", "4", "3 ok"],
                       'wait_for_supervisor 1 || echo SHORT-GAVEUP; '
                       'wait_for_supervisor 10 && echo LONG-READY')
    assert "SHORT-GAVEUP" in p.stdout
    assert "LONG-READY" in p.stdout


# ── assert_service_running ────────────────────────────────────────────

def test_it_waits_through_STARTING(tmp_path):
    """Every long-running conf.d program has startsecs=5, so STARTING is the
    normal state for the first five seconds and a single-shot read called it
    'not running'."""
    p, _, _ = _run(tmp_path, ["3 ok", "0 foo STARTING", "0 foo STARTING", "0 foo RUNNING pid 1"],
                   'assert_service_running foo 15 && echo UP')
    assert "UP" in p.stdout


def test_FATAL_short_circuits_instead_of_sleeping_out_the_budget(tmp_path):
    p, elapsed, _ = _run(tmp_path, ["3 ok", "0 foo FATAL"],
                         'assert_service_running foo 30 || true')
    assert "FATAL" in p.stdout
    assert elapsed < 10, "terminal state must not wait"


def test_an_unknown_program_name_short_circuits(tmp_path):
    """`supervisorctl status typo` prints `typo: ERROR (no such process)`, so the
    status word is ERROR. Waiting 60s cannot turn a typo into a service."""
    p, elapsed, _ = _run(tmp_path, ["3 ok", "0 typo ERROR (no such process)"],
                         'assert_service_running typo 30 || true')
    assert "does not know a program" in p.stdout
    assert elapsed < 10


def test_the_declared_ceiling_covers_BOTH_waits_not_one_each(tmp_path):
    """The socket wait and the RUNNING wait carried independent counters of the
    same size, so a documented 60s ceiling was really 2x60 — measured at 9.06s
    for a declared budget of 5."""
    p, elapsed, _ = _run(tmp_path, ["4"], 'assert_service_running foo 4 || true')
    assert elapsed < 8, f"total exceeded the declared budget: {elapsed:.1f}s"


def test_a_dead_socket_names_the_socket_not_the_service(tmp_path):
    p, _, _ = _run(tmp_path, ["4"], 'assert_service_running foo 2 || true')
    assert "RPC socket never came up" in p.stdout


# ── budgets are levers ────────────────────────────────────────────────

@pytest.mark.parametrize("var,default", [
    ("SUPERVISOR_READY_TIMEOUT", "60"), ("PORTAL_READY_TIMEOUT", "120"),
    ("CADDY_READY_TIMEOUT", "120"), ("HTTP_CHECK_MAX_TIME", "20"),
])
def test_every_budget_can_be_overridden_from_the_environment(tmp_path, var, default):
    """The suite ships inside the image: baked, a wrong budget costs a rebuild
    and re-promote of every image; behind a variable it is a template edit."""
    p, _, _ = _run(tmp_path, ["3 ok"], f'echo "${var}"')
    assert p.stdout.strip() == default
    p, _, _ = _run(tmp_path, ["3 ok"], f'echo "${var}"', {var: "7"})
    assert p.stdout.strip() == "7"


def test_the_supervisor_budget_lever_actually_reaches_the_wait(tmp_path):
    """A lever nothing reads is not a lever."""
    p, elapsed, _ = _run(tmp_path, ["4"], 'wait_for_supervisor || echo GAVEUP',
                         {"SUPERVISOR_READY_TIMEOUT": "2"})
    assert "GAVEUP" in p.stdout
    assert elapsed < 10, "the default 60 was used instead of the override"


# ── runner.sh phase gate ──────────────────────────────────────────────
#
# base/ is the platform contract and costs ~60s. The derivative *.d/ suites are
# model downloads and inference — tens of minutes. ADR 0029 redraws on ANY
# failure, so once base is red the cell is already lost: running the tail can
# only spend a rented GPU to reach a conclusion already reached, and every
# assertion it makes is measuring a degraded platform anyway.

RUNNER = Path(__file__).resolve().parents[3] / "ROOT/opt/instance-tools/tests/runner.sh"


def _suite(tmp_path, base_tests: dict, d_tests: dict):
    """Build a throwaway tests tree and run runner.sh --manual against it."""
    root = tmp_path / "tests"
    (root / "base").mkdir(parents=True)
    (root / "demo.d").mkdir(parents=True)
    (root / "lib.sh").write_text(LIB.read_text())
    r = root / "runner.sh"
    r.write_text(RUNNER.read_text())
    r.chmod(0o755)
    for name, body in base_tests.items():
        f = root / "base" / name
        f.write_text('#!/bin/bash\nsource "$(dirname "$0")/../lib.sh"\n' + body)
        f.chmod(0o755)
    for name, body in d_tests.items():
        f = root / "demo.d" / name
        f.write_text('#!/bin/bash\nsource "$(dirname "$0")/../lib.sh"\n' + body)
        f.chmod(0o755)
    env = dict(os.environ,
               INSTANCE_TEST_LOG=str(tmp_path / "out.log"),
               INSTANCE_TEST_RESULTS=str(tmp_path / "results.json"))
    p = subprocess.run(["bash", str(r), "--manual"], capture_output=True, text=True,
                       timeout=120, cwd=str(root), env=env)
    return p.stdout + p.stderr


def test_a_base_failure_stops_the_derivative_phase(tmp_path):
    out = _suite(tmp_path,
                 {"10-ok.sh": 'test_pass ok\n', "26-bad.sh": 'test_fail "caddy flake"\n'},
                 {"10-expensive.sh": 'echo EXPENSIVE-RAN\ntest_pass ok\n'})
    assert "DERIVATIVE PHASE NOT ATTEMPTED" in out
    assert "EXPENSIVE-RAN" not in out, "the expensive tail ran despite a red base"


def test_the_whole_base_phase_still_runs_after_a_failure(tmp_path):
    """The cut is at the PHASE boundary, not the failing test. Aborting on the
    first red would have destroyed the evidence behind ADR 0029's amendment:
    seeing 10, 20 and 26 fail TOGETHER is what found the shared root cause."""
    out = _suite(tmp_path,
                 {"10-bad.sh": 'test_fail "first"\n',
                  "20-bad.sh": 'test_fail "collateral"\n',
                  "67-ok.sh": 'echo LATE-BASE-RAN\ntest_pass ok\n'},
                 {"10-expensive.sh": 'echo EXPENSIVE-RAN\ntest_pass ok\n'})
    assert "collateral" in out, "a later base test must still run and report"
    assert "LATE-BASE-RAN" in out, "the exonerating base test must still run"
    assert "EXPENSIVE-RAN" not in out


def test_a_green_base_runs_the_derivative_phase_normally(tmp_path):
    out = _suite(tmp_path,
                 {"10-ok.sh": 'test_pass ok\n'},
                 {"10-expensive.sh": 'echo EXPENSIVE-RAN\ntest_pass ok\n'})
    assert "EXPENSIVE-RAN" in out
    assert "DERIVATIVE PHASE NOT ATTEMPTED" not in out


def test_the_report_says_NOT_ATTEMPTED_rather_than_letting_skip_read_as_pass(tmp_path):
    """A wall of `skipped` reads as benign. The required-pass gate already
    treats a skipped required test as a failure, so the machinery is safe — this
    is about the human who reads the summary and infers nothing happened."""
    out = _suite(tmp_path,
                 {"26-bad.sh": 'test_fail "caddy flake"\n'},
                 {"10-expensive.sh": 'test_pass ok\n'})
    assert "were never started" in out or "never started" in out
    assert "NOT passing and NOT skipped-by-design" in out
