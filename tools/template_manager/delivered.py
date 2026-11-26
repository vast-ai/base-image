"""The delivered run: send the on-box checks over SSH, run them detached, follow them
(ADR 0068, decisions 6 to 9 and 12 to 15).

The instance boots as a customer's does. Once it is up, over one SSH session
(remote.RemoteSession, persistent):

1. tools     the box has what the driver and the transfer need;
2. baked     a copy of the suite an older image still carries is removed;
3. send      the bundle (bundle.py) goes over stdin, is extracted, checked against its
             manifest, listed, and its digest echoed back;
4. start     the driver is started in its own session, detached from this one;
5. follow    every POLL_INTERVAL seconds one command returns the new bytes of each
             followed file from the client's own byte offsets, so a dropped connection
             needs no special path: the next poll asks from the same offset.

`Delivery.events()` yields what the baked path's stream yields ({"_event": "output"},
{"_event": "log"}, {"_event": "result"}, {"_timeout"}, {"_error"}), so the launcher
feeds both into the same handling. A step that fails is one more event,
{"_delivery": {"name": "delivery/<phase>", "state": "error", "detail": ...}}: an error
row, never a skip and never a pass.

On the box, <root>/ is mode 0700 and holds `lock` and one directory per run:
bundle/, output.log, results.json, pid, started, done, driver.err (see onbox/driver.sh).
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import time
from dataclasses import dataclass

ROOT = "/var/tmp/vast-test"
BAKED = "/opt/instance-tools/tests"
# What the driver and the steps below call. Checked first: a box without one of them
# fails in a way that reads as a broken check.
TOOLS = ("bash", "tar", "sha256sum", "setsid", "flock", "timeout",
         "find", "sort", "grep", "env", "date", "tail", "head", "wc")
POLL_INTERVAL = 2
START_WAIT = 60                 # for a sign that the driver is running
STREAM_FAILURE_LIMIT = 600      # of polls failing without a break
COMMAND_TIMEOUT = 60
SEND_TIMEOUT = 300
CHUNK = 1 << 20                 # most bytes of one file returned by one poll
ENVIRONMENT_SOURCE = "supervisord"
LAYER_STATE = "unstamped"       # layer stamps (ADR 0068 decision 10) are a later step
ENVIRONMENT_ROW = "onbox/environment"


class DeliveryFailure(Exception):
    """A step failed: `name` is the result row, `detail` what was seen."""

    def __init__(self, name, detail):
        super().__init__(f"{name}: {detail}")
        self.name, self.detail = name, str(detail)


@dataclass(frozen=True)
class Box:
    """Where things are on the box, and how long the driver waits for supervisord.
    None leaves the driver's own default."""
    root: str = ROOT
    baked: str = BAKED
    supervisord_pidfile: str | None = None
    supervisord_wait: int | None = None


def offers_ssh(template) -> bool:
    """Whether a template's instances accept SSH. The `ssh` and `jupyter` launch modes
    both run sshd; the entrypoint mode (`args`) does only with `use_ssh`."""
    runtype = str((template or {}).get("runtype") or "").lower()
    return "ssh" in runtype or "jupyter" in runtype or bool((template or {}).get("use_ssh"))


def _resolve_cr(raw: str):
    """A line as the baked results server sent it: line ending stripped, and a
    carriage return inside it (a progress bar) resolved to its last segment."""
    s = raw.rstrip("\r\n")
    if "\r" in s:
        return s.rsplit("\r", 1)[-1], True
    return s, False


@dataclass
class Poll:
    chunks: dict
    has_pid: bool = False
    alive: bool = False
    started: bool = False
    done: tuple | None = None      # (run id, state)
    gone: bool = False


def parse_poll(data: bytes):
    """One poll reply, or None when it is not complete and well formed.

    A header line `@@ <index> <byte count>` then exactly that many bytes, repeated;
    `@@ status <has pid> <alive> <started>`; and a last line `@@ done <run-id> <state>`
    or `@@ running`. Anything else is a reply cut short, and none of it is used.
    """
    poll, pos = Poll(chunks={}), 0
    while True:
        end = data.find(b"\n", pos)
        if end < 0:
            return None
        parts = data[pos:end].decode("ascii", "replace").split()
        pos = end + 1
        if len(parts) < 2 or parts[0] != "@@":
            return None
        kind = parts[1]
        if kind == "gone":
            poll.gone = True
            return poll
        if kind == "running":
            return poll if pos == len(data) else None
        if kind == "done":
            if len(parts) != 4 or pos != len(data):
                return None
            poll.done = (parts[2], parts[3])
            return poll
        if kind == "status":
            if len(parts) != 5:
                return None
            poll.has_pid, poll.alive, poll.started = (p == "1" for p in parts[2:5])
            continue
        try:
            index, count = int(kind), int(parts[2])
        except (ValueError, IndexError):
            return None
        chunk = data[pos:pos + count]
        if count < 0 or len(chunk) != count:
            return None
        poll.chunks[index] = poll.chunks.get(index, b"") + chunk
        pos += count


class Delivery:
    """One delivered run on one instance.

    `session` needs command(script, stdin=None, timeout=...) -> (exit code or None,
    stdout bytes, stderr text): remote.RemoteSession. `bundle` is bundle.Bundle.
    `stop()` true ends the follow at once (the launcher's instance-death monitor).
    `report` is what the proof records about the delivery; it holds no host data.
    """

    def __init__(self, session, bundle, run_id, *, logs=(), box=Box(), log=None,
                 stop=None, clock=time.monotonic, sleep=time.sleep,
                 poll_interval=POLL_INTERVAL, start_wait=START_WAIT,
                 stream_failure_limit=STREAM_FAILURE_LIMIT):
        self.session, self.bundle, self.run_id = session, bundle, str(run_id)
        self.logs, self.box = list(logs), box
        self.log = log or (lambda msg: None)
        self.stop = stop or (lambda: False)
        self.clock, self.sleep = clock, sleep
        self.poll_interval, self.start_wait = poll_interval, start_wait
        self.stream_failure_limit = stream_failure_limit
        self.run_dir = f"{box.root.rstrip('/')}/{self.run_id}"
        self.report = {"bundle_digest": bundle.digest, "client_commit": bundle.commit,
                       "client_dirty": bundle.dirty, "baked_copy": "not checked",
                       "environment_source": ENVIRONMENT_SOURCE, "layer_state": LAYER_STATE}

    # -- one remote command ------------------------------------------------------
    def _sh(self, script, stdin=None, timeout=COMMAND_TIMEOUT, attempts=1):
        """(exit code or None, stdout text, stderr). `attempts` > 1 only for a step
        that is safe to repeat: a command whose outcome is unknown is sent again."""
        code, out, err = None, b"", "not attempted"
        for attempt in range(attempts):
            code, out, err = self.session.command(script, stdin=stdin, timeout=timeout)
            if code is not None:
                break
            if attempt + 1 < attempts:
                self.sleep(self.poll_interval)
        return code, out.decode("utf-8", "replace"), err

    @staticmethod
    def _marks(out):
        """The `@@ ` lines of a step's reply, without the prefix."""
        return [ln[3:] for ln in out.splitlines() if ln.startswith("@@ ")]

    # -- the steps ---------------------------------------------------------------
    def check_tools(self):
        script = ("for t in " + " ".join(TOOLS) + "; do command -v \"$t\" >/dev/null 2>&1 "
                  "|| echo \"@@ missing $t\"; done; echo '@@ checked'")
        code, out, err = self._sh(script, attempts=3)
        marks = self._marks(out)
        if "checked" not in marks:
            raise DeliveryFailure("delivery/tools",
                                  f"the box's tools could not be checked: {err.strip()[:300]}")
        missing = [m.split(" ", 1)[1] for m in marks if m.startswith("missing ")]
        if missing:
            raise DeliveryFailure("delivery/tools",
                                  f"the box lacks {', '.join(missing)}, which the on-box "
                                  f"driver needs")

    def remove_baked(self):
        """Remove a baked copy of the suite, so the delivered one is the only one."""
        baked = shlex.quote(self.box.baked)
        script = (f"if [ -e {baked} ] || [ -L {baked} ]; then "
                  f"rm -rf -- {baked} && echo '@@ removed' || echo '@@ failed'; "
                  f"else echo '@@ absent'; fi")
        code, out, err = self._sh(script, attempts=3)
        marks = self._marks(out)
        if "removed" in marks:
            self.report["baked_copy"] = "removed"
        elif "absent" in marks:
            self.report["baked_copy"] = "absent"
        else:
            raise DeliveryFailure("delivery/baked-copy",
                                  f"the baked suite at {self.box.baked} could not be "
                                  f"removed: {err.strip()[:300]}")

    def send(self):
        root, run_dir = shlex.quote(self.box.root), shlex.quote(self.run_dir)
        script = f"""
umask 077
mkdir -p {root} && chmod 700 {root} && rm -rf -- {run_dir} && mkdir -p {run_dir}/bundle \\
    && cd {run_dir}/bundle || {{ echo '@@ no-directory'; exit 0; }}
tar -xf - --no-same-owner || {{ echo '@@ extract-failed'; exit 0; }}
echo '@@ extracted'
if sums=$(sha256sum -c MANIFEST 2>&1); then echo '@@ sums-ok'; else
    while IFS= read -r line; do
        [[ "$line" == *": OK" ]] || echo "@@ bad $line"
    done <<< "$sums"
fi
find . -mindepth 1 ! -type d | LC_ALL=C sort | while IFS= read -r f; do
    echo "@@ file ${{f#./}}"
done
echo "@@ manifest $(sha256sum < MANIFEST)"
echo "@@ run $(sha256sum < RUN)"
echo '@@ end'
"""
        code, out, err = self._sh(script, stdin=self.bundle.tar(self.run_id),
                                  timeout=SEND_TIMEOUT, attempts=3)
        marks = self._marks(out)

        def refuse(why):
            raise DeliveryFailure("delivery/transfer", why)

        if "end" not in marks:
            if "extract-failed" in marks:
                refuse(f"the bundle could not be extracted: {err.strip()[:300]}")
            if "no-directory" in marks:
                refuse(f"{self.run_dir} could not be created: {err.strip()[:300]}")
            refuse(f"the transfer did not complete: {err.strip()[:300]}")
        if "sums-ok" not in marks:
            bad = [m[4:] for m in marks if m.startswith("bad ")]
            refuse("files on the box do not match the manifest: " + "; ".join(bad)[:400])
        listed = sorted(m[5:] for m in marks if m.startswith("file "))
        expected = self.bundle.paths()
        if listed != expected:
            extra = sorted(set(listed) - set(expected))
            missing = sorted(set(expected) - set(listed))
            refuse("the box's file list differs from the manifest"
                   + (f"; not in the manifest: {', '.join(extra)[:200]}" if extra else "")
                   + (f"; missing: {', '.join(missing)[:200]}" if missing else ""))

        def echoed(key):
            return next((m.split()[1] for m in marks
                         if m.startswith(key + " ") and len(m.split()) > 1), "")
        if echoed("manifest") != self.bundle.digest:
            refuse("the manifest digest echoed by the box is not the bundle's")
        run_hash = hashlib.sha256(self.bundle.run_descriptor(self.run_id)).hexdigest()
        if echoed("run") != run_hash:
            refuse("the run descriptor on the box is not the one sent")

    def start(self):
        """Start the driver in its own session. Not repeated on an unknown outcome: a
        second start would be refused by the first one's lock. Whether it started is
        what the follow establishes."""
        options = ""
        if self.box.supervisord_pidfile:
            options += f" --supervisord-pidfile {shlex.quote(self.box.supervisord_pidfile)}"
        if self.box.supervisord_wait is not None:
            options += f" --supervisord-wait {int(self.box.supervisord_wait)}"
        script = (f"cd {shlex.quote(self.run_dir)} || {{ echo '@@ no-directory'; exit 0; }}; "
                  f"setsid --fork bash bundle/driver.sh{options} "
                  f"</dev/null >/dev/null 2>driver.err; echo \"@@ launched $?\"")
        code, out, err = self._sh(script)
        marks = self._marks(out)
        if code is not None and "launched 0" not in marks:
            raise DeliveryFailure("delivery/start",
                                  f"the driver could not be started: "
                                  f"{(' '.join(marks) + ' ' + err).strip()[:300]}")

    # -- following ---------------------------------------------------------------
    def _poll_script(self, offsets):
        files = ["output.log", *self.logs]
        emits = "\n".join(f"emit {i} {shlex.quote(path)} {offsets[i]}"
                          for i, path in enumerate(files))
        # `alive` is read before `done`: a driver seen dead with no `done` afterwards
        # ended without writing one. `done` is read before the bytes: once it is there
        # nothing more is written, so a poll that returns no bytes has returned all.
        return f"""
cd {shlex.quote(self.run_dir)} 2>/dev/null || {{ echo '@@ gone'; exit 0; }}
has_pid=0; alive=0; started=0
pid=$(cat pid 2>/dev/null) && [ -n "$pid" ] && has_pid=1
[ "$has_pid" = 1 ] && kill -0 "$pid" 2>/dev/null && alive=1
[ -e started ] && started=1
state='@@ running'; [ -e done ] && state="@@ done $(cat done)"
emit() {{
    local size count
    size=$(wc -c < "$2" 2>/dev/null) || return 0
    count=$(( size - $3 ))
    (( count > 0 )) || return 0
    (( count > {CHUNK} )) && count={CHUNK}
    printf '@@ %d %d\\n' "$1" "$count"
    tail -c "+$(( $3 + 1 ))" "$2" | head -c "$count"
}}
{emits}
printf '@@ status %d %d %d\\n' "$has_pid" "$alive" "$started"
printf '%s\\n' "$state"
"""

    def _driver_err(self):
        code, out, _ = self._sh(
            f"head -c 2000 {shlex.quote(self.run_dir)}/driver.err 2>/dev/null", attempts=2)
        text = " ".join(out.split())
        return f" driver.err: {text}" if text else " driver.err is empty"

    def _lines(self, index, tails, chunk):
        """Whole lines only: the unfinished tail waits for the next poll."""
        *lines, tails[index] = (tails[index] + chunk).split(b"\n")
        for raw in lines:
            clean, had_cr = _resolve_cr(raw.decode("utf-8", "replace"))
            if index == 0:
                yield {"_event": "output", "_data": clean}
            else:
                event = {"_event": "log", "src": os.path.basename(self.logs[index - 1]),
                         "line": clean}
                if had_cr:
                    event["overwrite"] = True
                yield event

    def follow(self, timeout):
        """Yield the run's events until its `done`, then its result."""
        count = 1 + len(self.logs)
        offsets, tails = [0] * count, [b""] * count
        begun = self.clock()
        deadline = begun + timeout
        failing_since, started = None, False
        while True:
            if self.stop():
                yield {"_error": "the instance stopped during the delivered run"}
                return
            now = self.clock()
            if now > deadline:
                yield {"_timeout": True}
                return
            code, out, err = self.session.command(self._poll_script(offsets),
                                                  timeout=COMMAND_TIMEOUT)
            poll = parse_poll(out) if code == 0 else None
            if poll is None:
                failing_since = now if failing_since is None else failing_since
                if now - failing_since >= self.stream_failure_limit:
                    raise DeliveryFailure(
                        "delivery/stream",
                        f"the run could not be followed for {self.stream_failure_limit}s: "
                        f"{(err or 'unreadable reply').strip()[:300]}")
                self.sleep(self.poll_interval)
                continue
            failing_since = None
            phase = "delivery/driver-died" if started or poll.started else "delivery/start"
            if poll.gone:
                raise DeliveryFailure(phase, f"the run directory {self.run_dir} is gone")
            for index in sorted(poll.chunks):
                if not 0 <= index < count:
                    continue
                offsets[index] += len(poll.chunks[index])
                yield from self._lines(index, tails, poll.chunks[index])
            started = started or poll.started
            if poll.done and poll.done[0] == self.run_id:
                if poll.chunks:
                    continue                 # bytes arrived with it: ask once more
                yield from self._finish(poll.done[1])
                return
            if poll.has_pid and not poll.alive:
                raise DeliveryFailure(
                    phase, "the driver ended without a completion marker." + self._driver_err())
            if not started and not poll.alive and now - begun >= self.start_wait:
                raise DeliveryFailure(
                    "delivery/start", f"the driver showed no sign of starting within "
                                      f"{self.start_wait}s." + self._driver_err())
            self.sleep(self.poll_interval)

    def _finish(self, state):
        if state == "busy":
            raise DeliveryFailure("delivery/busy",
                                  "another test run holds the lock on this box")
        if state == "environment":
            yield {"_delivery": {
                "name": ENVIRONMENT_ROW, "state": "error", "final_state": "failed",
                "detail": "supervisord never started, so the checks had no environment "
                          "to run in"}}
            return
        if state not in ("passed", "failed"):
            raise DeliveryFailure("delivery/stream",
                                  f"the driver reported an unknown final state {state!r}")
        code, out, err = self._sh(f"cat {shlex.quote(self.run_dir)}/results.json", attempts=3)
        try:
            doc = json.loads(out) if code == 0 else None
        except ValueError:
            doc = None
        if not isinstance(doc, dict) or doc.get("state") != state:
            raise DeliveryFailure("delivery/stream",
                                  f"the run ended {state} but its results could not be "
                                  f"read: {(err or out).strip()[:200]}")
        yield {"_event": "result", **doc}

    def events(self, timeout):
        """The whole run as events; a failed step is the last one."""
        try:
            self.log("  Checking the box's tools")
            self.check_tools()
            self.remove_baked()
            self.log(f"  Baked copy of the suite: {self.report['baked_copy']}")
            self.send()
            self.log(f"  Bundle {self.bundle.digest[:12]} delivered and verified "
                     f"({len(self.bundle.files)} files)")
            self.start()
            yield from self.follow(timeout)
        except DeliveryFailure as failure:
            yield {"_delivery": {"name": failure.name, "state": "error",
                                 "final_state": "error", "detail": failure.detail}}
