"""The delivered run against a local fake box (delivered.py, ADR 0068).

The box is this machine: RemoteSession's ssh is replaced by bash (fakebox.BoxRunner)
and the box's paths point into a temporary directory. What must hold: a delivery that
is not exactly the bundle is refused; a second run is refused; the follow never loses
or repeats output across a failed poll; and every failure is an error row, never a
pass.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess

import pytest

import bundle as bundle_mod
import delivered
import remote
from delivered import Box, Delivery
from fakebox import BoxRunner, FakeAPI, make_repo, supervisord
from remote import RemoteSession

LIB = 'source "$(dirname "$0")/../lib.sh"\n'
PASS = "#!/bin/bash\n" + LIB + "test_pass fine\n"
RUN_ID = "run0001"


@pytest.fixture(autouse=True)
def _client_has_ssh(monkeypatch):
    monkeypatch.setattr(remote.shutil, "which", lambda name: f"/usr/bin/{name}")


@pytest.fixture
def the_bundle(tmp_path):
    repo = make_repo(tmp_path, {"base/10-a.sh": PASS, "base/20-b.sh": PASS})
    return bundle_mod.build(repo, "vastai/base-image:v1",
                            git_state=lambda repo, paths: ("c0ffee", False))


@pytest.fixture
def box(tmp_path):
    proc, pidfile = supervisord(tmp_path, {"PATH": os.environ["PATH"], "HOME": str(tmp_path)})
    yield Box(root=str(tmp_path / "vast-test"), baked=str(tmp_path / "baked-tests"),
              supervisord_pidfile=str(pidfile))
    proc.kill()
    proc.wait()


@pytest.fixture
def session():
    opened = []

    def open_(runner=None):
        s = RemoteSession(FakeAPI(), 5555, runner=runner or BoxRunner(),
                          sleep=lambda s: None, persistent=True).__enter__()
        opened.append(s)
        return s
    yield open_
    for s in opened:
        s.close()


def _delivery(session, the_bundle, box, **kw):
    kw.setdefault("poll_interval", 0.05)
    return Delivery(session, the_bundle, RUN_ID, box=box, **kw)


def _raised(follow):
    """The failed step a follow ended on (events() turns it into the error row)."""
    with pytest.raises(delivered.DeliveryFailure) as caught:
        list(follow)
    return caught.value


def _failure(events):
    """The one failed step a run ended on."""
    rows = [e["_delivery"] for e in events if "_delivery" in e]
    assert len(rows) == 1 and "_delivery" in events[-1], events
    assert not any(e.get("_event") == "result" for e in events)
    return rows[0]


def test_a_run_is_delivered_verified_run_and_reported(session, the_bundle, box):
    os.makedirs(os.path.join(box.baked, "base"))
    d = _delivery(session(), the_bundle, box)
    events = list(d.events(60))

    output = [e["_data"] for e in events if e.get("_event") == "output"]
    assert "─── Running: base/10-a ───" in output and "  ALL TESTS PASSED" in output
    result = events[-1]
    assert result["_event"] == "result" and result["state"] == "passed"
    assert [(t["name"], t["state"]) for t in result["tests"]] == [
        ("base/10-a", "passed"), ("base/20-b", "passed")]

    assert not os.path.exists(box.baked)
    assert d.report == {"bundle_digest": the_bundle.digest, "client_commit": "c0ffee",
                        "client_dirty": False, "baked_copy": "removed",
                        "environment_source": "supervisord", "layer_state": "unstamped"}
    assert stat.S_IMODE(os.stat(box.root).st_mode) == 0o700


def test_an_image_with_no_baked_copy_is_recorded_as_such(session, the_bundle, box):
    d = _delivery(session(), the_bundle, box)
    d.remove_baked()
    assert d.report["baked_copy"] == "absent"


def test_a_file_that_differs_from_the_manifest_is_refused(session, the_bundle, box):
    the_bundle.files["base/10-a.sh"] = (b"#!/bin/bash\nexit 0\n", 0o755)
    row = _failure(list(_delivery(session(), the_bundle, box).events(60)))
    assert row["name"] == "delivery/transfer" and row["state"] == "error"
    assert "base/10-a.sh" in row["detail"]


def test_a_file_the_manifest_does_not_list_is_refused(session, the_bundle, box):
    the_bundle.files["base/99-planted.sh"] = (b"#!/bin/bash\nexit 0\n", 0o755)
    row = _failure(list(_delivery(session(), the_bundle, box).events(60)))
    assert row["name"] == "delivery/transfer" and "base/99-planted.sh" in row["detail"]


def test_a_manifest_that_is_not_the_bundles_is_refused(session, the_bundle, box):
    # a file and the manifest changed together: every sum on the box agrees
    planted = b"#!/bin/bash\nexit 0\n"
    the_bundle.files["base/10-a.sh"] = (planted, 0o755)
    original = hashlib.sha256(PASS.encode()).hexdigest().encode()
    assert original in the_bundle.manifest
    the_bundle.manifest = the_bundle.manifest.replace(
        original, hashlib.sha256(planted).hexdigest().encode(), 1)   # 10-a's line
    row = _failure(list(_delivery(session(), the_bundle, box).events(60)))
    assert row["name"] == "delivery/transfer" and "digest" in row["detail"]


def test_a_run_descriptor_from_another_run_is_refused(session, the_bundle, box, monkeypatch):
    tar = type(the_bundle).tar
    monkeypatch.setattr(type(the_bundle), "tar", lambda self, run_id: tar(self, "another-run"))
    row = _failure(list(_delivery(session(), the_bundle, box).events(60)))
    assert row["name"] == "delivery/transfer" and "run descriptor" in row["detail"]


def test_a_truncated_archive_is_refused(session, the_bundle, box, monkeypatch):
    whole = the_bundle.tar(RUN_ID)
    monkeypatch.setattr(type(the_bundle), "tar", lambda self, run_id: whole[:len(whole) // 2])
    row = _failure(list(_delivery(session(), the_bundle, box).events(60)))
    assert row["name"] == "delivery/transfer"
    assert not os.path.exists(os.path.join(box.root, RUN_ID, "started"))


def test_a_second_run_on_a_box_is_refused_as_busy(session, the_bundle, box):
    os.makedirs(box.root)
    lock = os.path.join(box.root, "lock")
    holder = subprocess.Popen(["flock", lock, "sleep", "60"])
    try:
        while subprocess.run(["flock", "-n", lock, "true"]).returncode == 0:
            pass                                    # until the holder has it
        row = _failure(list(_delivery(session(), the_bundle, box).events(60)))
    finally:
        holder.kill()
        holder.wait()
    assert row["name"] == "delivery/busy" and row["state"] == "error"
    assert not os.path.exists(os.path.join(box.root, RUN_ID, "started"))


def test_a_box_without_a_tool_the_driver_needs_is_refused(session, the_bundle, box, tmp_path):
    tools = tmp_path / "bin"
    tools.mkdir()
    for tool in delivered.TOOLS:
        if tool != "flock":
            os.symlink(shutil.which(tool), tools / tool)
    row = _failure(list(_delivery(session(BoxRunner(path=str(tools))), the_bundle,
                                  box).events(60)))
    assert row["name"] == "delivery/tools" and "flock" in row["detail"]
    assert not os.path.exists(box.root)


def test_supervisord_never_starting_is_an_environment_error(session, the_bundle, box, tmp_path):
    nowhere = Box(root=box.root, baked=box.baked, supervisord_wait=1,
                  supervisord_pidfile=str(tmp_path / "no-such.pid"))
    events = list(_delivery(session(), the_bundle, nowhere).events(60))
    row = _failure(events)
    assert (row["name"], row["state"], row["final_state"]) == (
        "onbox/environment", "error", "failed")
    assert any("supervisord did not start" in e.get("_data", "") for e in events)


# ── the follower, against a run directory written by the test ──────────────

@pytest.fixture
def run_dir(box):
    path = os.path.join(box.root, RUN_ID)
    os.makedirs(path)

    def write(name, text, mode="w"):
        with open(os.path.join(path, name), mode) as f:
            f.write(text)
    write("pid", f"{os.getpid()}\n")               # a driver that is alive
    write("started", "")
    write("output.log", "")
    return write


def _finish(write, state="passed"):
    write("results.json", json.dumps({"state": state, "tests": [
        {"name": "base/10-a", "state": state, "duration_s": 1}]}))
    write("done", f"{RUN_ID} {state}\n")


def test_the_follow_resumes_at_its_offset_after_a_failed_poll(session, the_bundle, box, run_dir):
    runner = BoxRunner()
    follow = _delivery(session(runner), the_bundle, box, sleep=lambda s: None).follow(60)
    run_dir("output.log", "first\n", "a")
    assert next(follow) == {"_event": "output", "_data": "first"}

    runner.down = 2
    run_dir("output.log", "second\n", "a")
    _finish(run_dir)
    rest = list(follow)
    assert [e.get("_data") for e in rest[:-1]] == ["second"]         # once, and not "first"
    assert rest[-1]["_event"] == "result"
    asked = [c[-1] for c in runner.commands if "emit 0 output.log" in c[-1]]
    assert sum("emit 0 output.log 6" in c for c in asked) >= 3      # two failed, one served


def test_the_follow_never_emits_a_partial_line(session, the_bundle, box, run_dir):
    steps = iter([lambda: run_dir("output.log", " of a line\nnext", "a"),
                  lambda: (run_dir("output.log", "\n", "a"), _finish(run_dir))])
    run_dir("output.log", "the start")
    follow = _delivery(session(), the_bundle, box,
                       sleep=lambda s: next(steps, lambda: None)()).follow(60)
    lines = [e["_data"] for e in follow if e.get("_event") == "output"]
    assert lines == ["the start of a line", "next"]


def test_a_log_named_by_the_launcher_is_followed_as_log_events(session, the_bundle, box,
                                                              run_dir, tmp_path):
    engine = tmp_path / "engine.log"
    engine.write_text("loading 10%\rloading 90%\nready\n")
    _finish(run_dir)
    events = list(_delivery(session(), the_bundle, box, logs=[str(engine)]).follow(60))
    assert [e for e in events if e.get("_event") == "log"] == [
        {"_event": "log", "src": "engine.log", "line": "loading 90%", "overwrite": True},
        {"_event": "log", "src": "engine.log", "line": "ready"}]


def test_a_done_from_another_run_does_not_end_this_one(session, the_bundle, box, run_dir):
    run_dir("done", "someone-else passed\n")
    polls = []

    def sleep(_):
        polls.append(1)
        if len(polls) == 2:
            _finish(run_dir, "failed")
    events = list(_delivery(session(), the_bundle, box, sleep=sleep).follow(60))
    assert len(polls) >= 2
    assert events[-1]["_event"] == "result" and events[-1]["state"] == "failed"


@pytest.mark.parametrize("started, name", [(True, "delivery/driver-died"),
                                           (False, "delivery/start")])
def test_a_driver_that_ends_without_its_marker_is_an_error(session, the_bundle, box, run_dir,
                                                           started, name):
    gone = subprocess.Popen(["true"])
    gone.wait()
    run_dir("pid", f"{gone.pid}\n")
    run_dir("driver.err", "driver.sh: line 7: something broke\n")
    if not started:
        os.remove(os.path.join(box.root, RUN_ID, "started"))
    failure = _raised(_delivery(session(), the_bundle, box).follow(60))
    assert failure.name == name and "something broke" in failure.detail


def test_a_driver_that_never_shows_up_is_a_start_error(session, the_bundle, box, run_dir):
    os.remove(os.path.join(box.root, RUN_ID, "pid"))
    os.remove(os.path.join(box.root, RUN_ID, "started"))
    now = [0.0]
    d = _delivery(session(), the_bundle, box, clock=lambda: now[0],
                  sleep=lambda s: now.__setitem__(0, now[0] + 10))
    assert _raised(d.follow(3600)).name == "delivery/start"
    assert 60 <= now[0] < 120                       # the start wait, not the run's timeout


def test_polls_failing_without_a_break_end_as_a_stream_error(session, the_bundle, box, run_dir):
    runner = BoxRunner()
    s = session(runner)
    assert s.connect()[0]
    runner.down = 10 ** 6
    now = [0.0]
    d = _delivery(s, the_bundle, box, clock=lambda: now[0],
                  sleep=lambda secs: now.__setitem__(0, now[0] + 30))
    assert _raised(d.follow(7200)).name == "delivery/stream"
    assert delivered.STREAM_FAILURE_LIMIT <= now[0] < delivered.STREAM_FAILURE_LIMIT + 60


def test_a_finished_run_whose_results_cannot_be_read_is_not_a_result(session, the_bundle, box,
                                                                    run_dir):
    run_dir("done", f"{RUN_ID} passed\n")           # and no results.json
    failure = _raised(_delivery(session(), the_bundle, box, sleep=lambda s: None).follow(60))
    assert failure.name == "delivery/stream"


def test_the_instance_stopping_ends_the_follow(session, the_bundle, box, run_dir):
    events = list(_delivery(session(), the_bundle, box, stop=lambda: True).follow(60))
    assert events == [{"_error": "the instance stopped during the delivered run"}]


@pytest.mark.parametrize("reply", [
    b"@@ 0 10\nshort",                                        # cut inside a chunk
    b"@@ 0 6\nfirst\n@@ status 1 1 1\n",                      # cut before the last line
    b"@@ 0 6\nfirst\n@@ status 1 1 1\n@@ done run0001\n",     # a marker half written
    b"first\n@@ running\n",                                   # not a reply at all
])
def test_a_reply_cut_short_is_not_used(reply):
    assert delivered.parse_poll(reply) is None
