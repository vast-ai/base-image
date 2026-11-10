"""Ephemeral SSH to a test instance (remote.py).

What must hold: the key exists for one run only — attached to this instance only,
detached and deleted afterwards whatever happens, setup failures included — and
every failure comes back as a reason the caller reports as an error, never as an
exception and never as a pass.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

import remote
from remote import RemoteSession

INSTANCE = {"public_ipaddr": "203.0.113.7", "ports": {"22/tcp": [{"HostPort": "40022"}]}}


class FakeAPI:
    def __init__(self, attach=None, instance=INSTANCE):
        self.calls = []
        self.attach = attach if attach is not None else {
            "success": True, "key": json.dumps({"id": 991, "public_key": "ssh-ed25519 AAA"})}
        self.instance = instance

    def _request_safe(self, method, path, body=None, query=None):
        self.calls.append((method, path, body))
        return self.attach if method == "POST" else {"success": True}

    def get_instance(self, instance_id, safe=False):
        return self.instance


class FakeRunner:
    """subprocess.run stand-in: ssh-keygen writes a real key pair file; ssh answers
    from a script of results, one per call."""

    def __init__(self, ssh_results=None):
        self.ssh_results = list(ssh_results or [])
        self.ssh_calls = []

    def __call__(self, cmd, **kw):
        class R:
            pass
        r = R()
        if cmd[0] == "ssh-keygen":
            path = cmd[cmd.index("-f") + 1]
            open(path, "w").write("PRIVATE")
            open(path + ".pub", "w").write("ssh-ed25519 AAAAPUBLIC vast-qa\n")
            r.returncode, r.stdout, r.stderr = 0, "", ""
            return r
        self.ssh_calls.append((cmd, kw.get("input")))
        code, out, err = self.ssh_results.pop(0) if self.ssh_results else (0, "", "")
        r.returncode, r.stdout, r.stderr = code, out, err
        return r


@pytest.fixture(autouse=True)
def _tools(monkeypatch):
    monkeypatch.setattr(remote.shutil, "which", lambda name: f"/usr/bin/{name}")


def test_the_public_key_is_attached_to_this_instance_and_detached_after():
    api, runner = FakeAPI(), FakeRunner([(0, "", ""), (0, "hello\n", "")])
    with RemoteSession(api, 5555, runner=runner, sleep=lambda s: None) as s:
        key_dir = s.dir
        ok, out = s.run("print('hello')")
    assert ok and out == "hello\n"
    post = [c for c in api.calls if c[0] == "POST"][0]
    assert post[1] == "/api/v0/instances/5555/ssh/"
    assert post[2] == {"ssh_key": "ssh-ed25519 AAAAPUBLIC vast-qa"}         # public half only
    assert ("DELETE", "/api/v0/instances/5555/ssh/991/", None) in api.calls
    assert not os.path.exists(key_dir), "the key pair outlived the session"


def test_cleanup_happens_even_when_the_caller_raises():
    api, runner = FakeAPI(), FakeRunner([(0, "", "")])
    with pytest.raises(RuntimeError):
        with RemoteSession(api, 5555, runner=runner, sleep=lambda s: None) as s:
            key_dir = s.dir
            raise RuntimeError("boom")
    assert any(c[0] == "DELETE" for c in api.calls)
    assert not os.path.exists(key_dir)


def test_ssh_uses_the_ephemeral_key_and_the_direct_endpoint():
    api, runner = FakeAPI(), FakeRunner([(0, "", ""), (0, "x", "")])
    with RemoteSession(api, 5555, runner=runner, sleep=lambda s: None) as s:
        s.run("print(1)", python="python3")
        key = os.path.join(s.dir, "id")
    cmd, stdin = runner.ssh_calls[-1]
    assert cmd[cmd.index("-i") + 1] == key and cmd[cmd.index("-p") + 1] == "40022"
    assert "root@203.0.113.7" in cmd and cmd[-1] == "python3 -" and stdin == "print(1)"
    assert "BatchMode=yes" in cmd and "IdentitiesOnly=yes" in cmd


def test_it_retries_while_the_machine_pulls_the_key():
    api = FakeAPI()
    runner = FakeRunner([(255, "", "Permission denied (publickey)"),
                         (255, "", "Permission denied (publickey)"), (0, "", ""), (0, "ok", "")])
    slept = []
    with RemoteSession(api, 5555, runner=runner, sleep=slept.append) as s:
        ok, out = s.run("x")
    assert ok and out == "ok" and len(slept) == 2


def test_a_key_that_is_never_accepted_is_a_reason_not_an_exception():
    runner = FakeRunner([(255, "", "Permission denied (publickey)")] * 3)
    with RemoteSession(FakeAPI(), 5555, runner=runner, sleep=lambda s: None, attempts=3) as s:
        ok, why = s.run("x")
    assert not ok and "not accepted after 3 attempts" in why and "Permission denied" in why


def test_a_failed_attach_is_a_reason_and_nothing_is_detached():
    api = FakeAPI(attach={"success": False, "msg": "Instance 5555 not found."})
    with RemoteSession(api, 5555, runner=FakeRunner(), sleep=lambda s: None) as s:
        ok, why = s.run("x")
    assert not ok and "could not attach" in why
    assert not any(c[0] == "DELETE" for c in api.calls)


def test_no_ssh_endpoint_is_a_reason():
    api = FakeAPI(instance={"public_ipaddr": "203.0.113.7", "ports": {}})
    with RemoteSession(api, 5555, runner=FakeRunner(), sleep=lambda s: None) as s:
        ok, why = s.run("x")
    assert not ok and "no SSH endpoint" in why
    assert any(c[0] == "DELETE" for c in api.calls)        # attached, so still detached


def test_missing_ssh_tools_is_a_reason(monkeypatch):
    monkeypatch.setattr(remote.shutil, "which", lambda name: None)
    api = FakeAPI()
    with RemoteSession(api, 5555, runner=FakeRunner()) as s:
        ok, why = s.run("x")
    assert not ok and "not installed" in why and api.calls == []


def test_a_failing_remote_script_returns_its_stderr():
    runner = FakeRunner([(0, "", ""), (1, "", "Traceback ... ImportError")])
    with RemoteSession(FakeAPI(), 5555, runner=runner, sleep=lambda s: None) as s:
        ok, why = s.run("x")
    assert not ok and "ImportError" in why


def test_the_proxy_is_used_when_there_is_no_direct_port():
    api = FakeAPI(instance={"public_ipaddr": "", "ports": {}, "ssh_host": "ssh.example.com",
                            "ssh_port": 12345})
    runner = FakeRunner([(0, "", ""), (0, "y", "")])
    with RemoteSession(api, 5555, runner=runner, sleep=lambda s: None) as s:
        s.run("x")
    cmd, _ = runner.ssh_calls[-1]
    assert "root@ssh.example.com" in cmd and cmd[cmd.index("-p") + 1] == "12345"


def test_a_timeout_is_a_reason():
    class Slow(FakeRunner):
        def __call__(self, cmd, **kw):
            if cmd[0] == "ssh" and cmd[-1] != "true":
                raise subprocess.TimeoutExpired(cmd, 1)
            return super().__call__(cmd, **kw)

    with RemoteSession(FakeAPI(), 5555, runner=Slow([(0, "", "")]), sleep=lambda s: None) as s:
        ok, why = s.run("x")
    assert not ok and "timed out" in why


# ---------------------------------------------------------------- setup that fails part-way

class RaisingAPI(FakeAPI):
    def __init__(self, raise_on, **kw):
        super().__init__(**kw)
        self.raise_on = raise_on

    def _request_safe(self, method, path, body=None, query=None):
        if (method, self.raise_on) in (("POST", "attach"), ("DELETE", "detach")):
            self.calls.append((method, path, body))
            raise ConnectionError(f"{method} failed")
        return super()._request_safe(method, path, body, query)

    def get_instance(self, instance_id, safe=False):
        if self.raise_on == "get_instance":
            raise ConnectionError("instance lookup failed")
        return super().get_instance(instance_id, safe)


class _Dirs:
    """Record the key directory the session made, even if setup dies before
    returning it."""

    def __init__(self, monkeypatch):
        self.made = []
        real = remote.tempfile.mkdtemp

        def mkdtemp(**kw):
            self.made.append(real(**kw))
            return self.made[-1]
        monkeypatch.setattr(remote.tempfile, "mkdtemp", mkdtemp)

    def all_gone(self):
        return self.made and not any(os.path.exists(d) for d in self.made)


def _enter(api, runner):
    """__enter__ must not raise; the session must report why it is unusable."""
    s = RemoteSession(api, 5555, runner=runner, sleep=lambda s: None)
    entered = s.__enter__()
    ok, why = entered.run("x")
    entered.__exit__(None, None, None)
    return ok, why


def test_ssh_keygen_raising_is_a_reason_and_the_key_dir_is_removed(monkeypatch):
    dirs = _Dirs(monkeypatch)

    class Broken(FakeRunner):
        def __call__(self, cmd, **kw):
            if cmd[0] == "ssh-keygen":
                raise OSError("exec format error")
            return super().__call__(cmd, **kw)

    api = FakeAPI()
    ok, why = _enter(api, Broken())
    assert not ok and "OSError" in why
    assert dirs.all_gone() and api.calls == []


def test_an_unreadable_public_key_is_a_reason_and_nothing_is_attached(monkeypatch):
    dirs = _Dirs(monkeypatch)

    class NoPub(FakeRunner):
        def __call__(self, cmd, **kw):
            r = super().__call__(cmd, **kw)
            if cmd[0] == "ssh-keygen":
                os.remove(cmd[cmd.index("-f") + 1] + ".pub")
            return r

    api = FakeAPI()
    ok, why = _enter(api, NoPub())
    assert not ok and "FileNotFoundError" in why
    assert dirs.all_gone() and api.calls == []


def test_an_attach_call_raising_is_a_reason_and_the_key_dir_is_removed(monkeypatch):
    dirs = _Dirs(monkeypatch)
    api = RaisingAPI("attach")
    ok, why = _enter(api, FakeRunner())
    assert not ok and "ConnectionError" in why and "may remain on the instance" in why
    assert dirs.all_gone()
    assert not any(c[0] == "DELETE" for c in api.calls)     # no id was ever returned


def test_get_instance_raising_detaches_the_attached_key_and_removes_the_dir(monkeypatch):
    dirs = _Dirs(monkeypatch)
    api = RaisingAPI("get_instance")
    runner = FakeRunner()
    ok, why = _enter(api, runner)
    assert not ok and "instance lookup failed" in why
    assert ("DELETE", "/api/v0/instances/5555/ssh/991/", None) in api.calls
    assert dirs.all_gone() and runner.ssh_calls == []


def test_a_failing_detach_never_raises_and_still_removes_the_key_pair(monkeypatch):
    dirs = _Dirs(monkeypatch)
    api = RaisingAPI("detach")
    with RemoteSession(api, 5555, runner=FakeRunner([(0, "", ""), (0, "ok", "")]),
                       sleep=lambda s: None) as s:
        assert s.run("x") == (True, "ok")
    assert any(c[0] == "DELETE" for c in api.calls) and dirs.all_gone()


# ---------------------------------------------------------------- the key id

@pytest.mark.parametrize("attach", [
    {"success": True},
    {"success": True, "key": "not json"},
    {"success": True, "key": json.dumps({"public_key": "ssh-ed25519 AAA"})},
    {"success": True, "key": json.dumps({"id": "abc"})},
    {"success": True, "key": json.dumps({"id": True})},
    {"success": True, "key": json.dumps([991])},
], ids=["no-key", "not-json", "no-id", "id-not-a-number", "id-bool", "not-an-object"])
def test_an_unreadable_key_id_makes_the_session_unavailable(monkeypatch, attach):
    """Without the id the key can never be detached, so the session is not used —
    and the reason says the key may stay on the instance."""
    dirs = _Dirs(monkeypatch)
    api, runner = FakeAPI(attach=attach), FakeRunner()
    with RemoteSession(api, 5555, runner=runner, sleep=lambda s: None) as s:
        ok, why = s.run("x")
    assert not ok and "cannot be detached" in why and "may remain on the instance" in why
    assert runner.ssh_calls == [], "a session whose key cannot be removed was used"
    assert dirs.all_gone()


def test_a_key_id_given_as_an_object_is_read():
    api = FakeAPI(attach={"success": True, "key": {"id": 992}})
    with RemoteSession(api, 5555, runner=FakeRunner([(0, "", ""), (0, "ok", "")]),
                       sleep=lambda s: None) as s:
        assert s.run("x")[0]
    assert ("DELETE", "/api/v0/instances/5555/ssh/992/", None) in api.calls


# ---------------------------------------------------------------- the deadline

class _Deadline:
    def __init__(self, left):
        self.left = left

    def remaining(self):
        return self.left


class TimedRunner(FakeRunner):
    """Records each ssh call's timeout; spends time on the deadline."""

    def __init__(self, ssh_results, deadline, cost=0, raise_timeout=False):
        super().__init__(ssh_results)
        self.deadline, self.cost, self.raise_timeout = deadline, cost, raise_timeout
        self.timeouts = []

    def __call__(self, cmd, **kw):
        if cmd[0] == "ssh":
            self.timeouts.append(kw.get("timeout"))
            self.deadline.left -= self.cost
            if self.raise_timeout and cmd[-1] != "true":
                raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        return super().__call__(cmd, **kw)


def test_a_passed_deadline_attaches_nothing():
    api = FakeAPI()
    with RemoteSession(api, 5555, runner=FakeRunner(), deadline=_Deadline(0)) as s:
        ok, why = s.run("x")
    assert not ok and "deadline exceeded" in why and api.calls == []


def test_connect_retries_stop_at_the_deadline():
    dl = _Deadline(25)
    slept = []

    def sleep(s):
        slept.append(s)
        dl.left -= s

    runner = TimedRunner([(255, "", "Permission denied")] * 12, dl, cost=0)
    with RemoteSession(FakeAPI(), 5555, runner=runner, sleep=sleep, deadline=dl) as s:
        ok, why = s.run("x")
    assert not ok and "deadline exceeded" in why and "Permission denied" in why
    assert len(runner.timeouts) < remote.CONNECT_ATTEMPTS
    assert all(t <= 25 for t in runner.timeouts) and sum(slept) <= 25


def test_the_remote_script_is_bounded_by_the_deadline():
    dl = _Deadline(50)
    runner = TimedRunner([(0, "", "")], dl, raise_timeout=True)
    with RemoteSession(FakeAPI(), 5555, runner=runner, sleep=lambda s: None, deadline=dl) as s:
        ok, why = s.run("x")
    assert runner.timeouts[-1] == 50                   # not SSH_TIMEOUT (300)
    assert not ok and "deadline exceeded" in why


def test_without_a_deadline_the_modules_own_timeouts_apply():
    runner = TimedRunner([(0, "", ""), (0, "ok", "")], _Deadline(0))
    with RemoteSession(FakeAPI(), 5555, runner=runner, sleep=lambda s: None) as s:
        assert s.run("x") == (True, "ok")
    assert runner.timeouts == [remote.CONNECT_PROBE_TIMEOUT, remote.SSH_TIMEOUT]
