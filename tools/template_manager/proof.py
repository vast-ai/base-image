"""Every launcher run leaves a proof (ADR 0059, D10).

A proof is a directory written by test_template.py on every run that got as far as
resolving a template, on every exit path:

    <proof-dir>/<UTC timestamp>-<template hash prefix>-<run id>/
        proof.json        the machine-readable record (SCHEMA_VERSION)
        summary.md        a short human-readable table (fit for GITHUB_STEP_SUMMARY)
        logs/<name>       each streamed log, as streamed (redacted)
        logs/test-output.log   the in-image test output, as streamed (redacted)

The record is built from an ALLOWLIST of fields, never by copying the --raw payload:
the repo and its CI artifacts are public, so a proof carries no host data — no IP
address, machine, host or offer id, SSH coordinates, mapped port, hostname or
location. It does carry the instance id: an ephemeral id that ties the proof to the
run (owner ruling), recorded per attempt.
Every string that reaches a proof file (values, keys, log lines) goes through the
launcher's IP redaction and is scrubbed of the API key, the instance token and the
run's own host ids.

Integrity: `integrity.files` holds the sha256 of every sibling file, and
`integrity.record_sha256` the sha256 of the canonical JSON of the record without its
`integrity` block. verify_proof.py recomputes both.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid

SCHEMA_VERSION = 1
PROOF_FILE = "proof.json"
SUMMARY_FILE = "summary.md"
LOG_DIR = "logs"
TEST_OUTPUT_LOG = "test-output.log"
SECRET_NAME = re.compile(r"TOKEN|KEY|SECRET|PASSWORD|CREDENTIAL", re.I)
MASK = "***"
REDACTED_ID = "[redacted-id]"
# Shorter values are not replaced in text: a one-letter "secret" or a two-digit "id"
# would rewrite unrelated text. Real API keys and instance tokens are far longer, and
# Vast ids are many digits.
MIN_SECRET_LEN = 8
MIN_HOST_ID_LEN = 4
# The CI context that ties a proof to the run that produced it (all optional).
CI_ENV = ("GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA",
          "GITHUB_WORKFLOW", "GITHUB_JOB")
# Descriptive hardware, read from an offer or instance record. Nothing that locates
# or identifies the host is on this list.
HARDWARE_FIELDS = ("gpu_name", "num_gpus", "gpu_ram", "gpu_total_ram", "cpu_arch",
                   "compute_cap", "cuda_max_good", "driver_version")
# Values in an offer/instance record that identify or locate the host. Never copied;
# learned so that any occurrence in text is scrubbed.
IDENTIFYING_FIELDS = ("machine_id", "host_id", "ssh_host", "ssh_port", "public_ipaddr",
                      "local_ipaddrs", "hostname", "geolocation", "datacenter", "ask_contract_id",
                      "bundle_id")
_FLAG_WITH_SECRET = ("--api-key",)
# Options whose value is a local filesystem path (it can carry the user's name): the
# proof records that the option was given, not its value.
_FLAG_WITH_LOCAL_PATH = ("--proof-dir",)
# Options whose value identifies a host (the machines a redraw excludes): recorded as
# given, never the value, whatever its length.
_FLAG_WITH_HOST_ID = ("--exclude-machine",)
GIVEN = "<given>"


def canonical(obj) -> bytes:
    """The canonical JSON bytes the record hash is taken over."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _utc(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_state(path):
    """(sha, dirty) of the checkout holding `path`; "unknown" outside git."""
    try:
        sha = subprocess.run(["git", "-C", path, "rev-parse", "HEAD"], capture_output=True,
                             text=True, timeout=10)
        if sha.returncode != 0:
            return "unknown", "unknown"
        dirty = subprocess.run(["git", "-C", path, "status", "--porcelain"],
                               capture_output=True, text=True, timeout=10)
        return sha.stdout.strip(), (bool(dirty.stdout.strip()) if dirty.returncode == 0
                                    else "unknown")
    except Exception:                                             # noqa: BLE001
        return "unknown", "unknown"


def _secret_prefix_pattern(secret: str):
    """Matches the secret, or any run of its first MIN_SECRET_LEN or more characters."""
    head, rest = secret[:MIN_SECRET_LEN], secret[MIN_SECRET_LEN:]
    tail = ""
    for ch in reversed(rest):
        tail = f"(?:{re.escape(ch)}{tail})?"
    return re.compile(re.escape(head) + tail)


def _safe_name(name: str) -> str:
    base = os.path.basename(name.rstrip("/")) or "log"
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    return base if base not in (".", "..", PROOF_FILE, SUMMARY_FILE) else f"_{base}"


class ProofRecorder:
    """Collects what a run did and writes its proof. Nothing is written until
    begin() (the template is resolved); write() is idempotent and never raises."""

    def __init__(self, proof_dir, argv, redact, *, env=None, clock=None, tool_dir=None,
                 git_state=None):
        self.proof_dir = proof_dir
        self.redact = redact
        self.env = os.environ if env is None else env
        self.clock = clock or __import__("time").time
        self.started = self.clock()
        self.run_id = uuid.uuid4().hex[:8]
        self.tool_dir = tool_dir or os.path.dirname(os.path.abspath(__file__))
        self._git_state = git_state or _git_state
        self._argv = list(argv)
        self._secrets: set[str] = set()
        self._host_ids: set[str] = set()
        home = os.path.expanduser("~")
        self._home = home if home not in ("", "/", "~") and len(home) > 1 else ""
        self.subject = None
        self.hardware = {}
        self.flags = {}
        self.attempts = {"launched": 0, "discarded": 0}
        self.instances: list[str] = []
        self.tests = []
        self.external = []
        self.logs: dict[str, list[str]] = {}
        self.outcome = None                         # (state, exit_code, reason)
        self.path = None
        self.written = False

    # -- what the run tells us -------------------------------------------------
    def add_secret(self, value):
        if value and len(str(value)) >= MIN_SECRET_LEN:
            self._secrets.add(str(value))

    def add_host_id(self, value):
        """An id that locates the run's host (instance, machine, offer): scrubbed from
        every string that reaches the proof, as a substring."""
        if value not in (None, "") and len(str(value)) >= MIN_HOST_ID_LEN:
            self._host_ids.add(str(value))

    def instance_seen(self, instance_id):
        """An instance this run created or attached to, from the moment it exists —
        including one discarded on a redraw. The instance id is NOT host data: it is
        an ephemeral id that ties the proof to the run, so it is recorded (every
        attempt's, in order) and is not scrubbed."""
        if instance_id in (None, "") or str(instance_id) in self.instances:
            return
        self.instances.append(str(instance_id))

    def begin(self, template_hash, template, image_full, env_overrides, flags):
        for kv in env_overrides or ():
            name, _, value = str(kv).partition("=")
            if SECRET_NAME.search(name):
                self.add_secret(value)          # masked wherever else it turns up
        self.subject = {
            "template_hash": str(template_hash),
            "template_name": str((template or {}).get("name") or ""),
            "image": str(image_full or ""),
            "image_digest": None,
            "env_overrides": [self._env_entry(kv) for kv in env_overrides or ()],
        }
        self.flags = {k: bool(v) for k, v in flags.items()}

    def set_hardware(self, record):
        """Copy the descriptive hardware fields of an offer or instance record, and
        learn the record's identifying values (ids, SSH coordinates, mapped ports,
        addresses, hostnames, location) so they are scrubbed wherever they appear."""
        record = record or {}
        for key in IDENTIFYING_FIELDS:
            value = record.get(key)
            self.add_host_id(value.strip() if isinstance(value, str) else value)
            for part in (value.split() if isinstance(value, str) else ()):
                self.add_host_id(part.strip(","))
        for key in ("jupyter_token",):
            self.add_secret(record.get(key))
        self.instance_seen(record.get("id"))
        for mappings in (record.get("ports") or {}).values():
            for m in mappings if isinstance(mappings, list) else ():
                if isinstance(m, dict):
                    self.add_host_id(m.get("HostPort"))
        self.hardware = {k: record[k] for k in HARDWARE_FIELDS
                         if isinstance(record.get(k), (int, float, str))
                         and not isinstance(record.get(k), bool)}
        digest = record.get("image_digest")
        if self.subject is not None and isinstance(digest, str) and digest.startswith("sha256:"):
            self.subject["image_digest"] = digest

    def instance_created(self, created):
        self.instance_seen(created)
        if created is None:
            self.attempts["discarded"] += 1
        else:
            self.attempts["launched"] += 1

    def add_log_line(self, src, line):
        name = _safe_name(src) if src else "log.txt"
        self.logs.setdefault(name, []).append(line)

    def add_test_output(self, line):
        self.logs.setdefault(TEST_OUTPUT_LOG, []).append(line)

    def set_results(self, tests=None, external=None):
        if tests is not None:
            self.tests = [{"name": str(t.get("name", "")), "state": str(t.get("state", "")),
                           **({"duration_s": t["duration_s"]}
                              if isinstance(t.get("duration_s"), (int, float)) else {})}
                          for t in tests if isinstance(t, dict)]
        if external is not None:
            self.external = [{"name": str(r.get("name", "")), "state": str(r.get("state", "")),
                              "kind": str(r.get("kind") or ""),
                              "detail": str(r.get("detail") or "")}
                             for r in external if isinstance(r, dict)]

    def set_outcome(self, state, exit_code, reason=""):
        self.outcome = (str(state), int(exit_code), str(reason or ""))

    # -- scrubbing -------------------------------------------------------------
    def scrub(self, text: str) -> str:
        """IP redaction, then every known secret and every learned host value, matched
        as SUBSTRINGS: an id glued inside a longer token (`C.<id>_auth_token`) is still
        the id. A numeric value only needs a non-digit on each side, so an id inside a
        longer number (a timestamp) is not rewritten. Any run of MIN_SECRET_LEN or more
        leading characters of a secret is masked: a logged token prefix
        (`2c9fa0cf...`) is still part of the token. The user's home directory becomes
        `~`."""
        text = self.redact(str(text))
        for secret in sorted(self._secrets, key=len, reverse=True):
            text = _secret_prefix_pattern(secret).sub(MASK, text)
        for hid in sorted(self._host_ids, key=len, reverse=True):
            if hid.isdigit():
                text = re.sub(rf"(?<!\d){hid}(?!\d)", REDACTED_ID, text)
            else:
                text = text.replace(hid, REDACTED_ID)
        if self._home:
            text = text.replace(self._home, "~")
        return text

    def _scrub_doc(self, value):
        """Every string value scrubbed; every key IP-redacted (keys are this module's
        own names or env names, never secrets or ids)."""
        if isinstance(value, str):
            return self.scrub(value)
        if isinstance(value, dict):
            return {self.redact(k) if isinstance(k, str) else k: self._scrub_doc(v)
                    for k, v in value.items()}
        if isinstance(value, list):
            return [self._scrub_doc(v) for v in value]
        return value

    def _env_entry(self, kv):
        name, _, value = str(kv).partition("=")
        return {"name": name, "value": MASK if SECRET_NAME.search(name) else value}

    def _argv_clean(self):
        out, hide_next = [], False
        for a in self._argv:
            if hide_next:
                out.append(hide_next)
                hide_next = False
                continue
            flag, eq, val = a.partition("=")
            if flag in _FLAG_WITH_SECRET + _FLAG_WITH_LOCAL_PATH + _FLAG_WITH_HOST_ID:
                hidden = (MASK if flag in _FLAG_WITH_SECRET else
                          REDACTED_ID if flag in _FLAG_WITH_HOST_ID else GIVEN)
                out.append(f"{flag}={hidden}" if eq else flag)
                hide_next = hidden if not eq else False
            elif flag == "--env" and eq and SECRET_NAME.search(val.split("=", 1)[0]):
                out.append(f"--env={val.split('=', 1)[0]}={MASK}")
            elif "=" in a and SECRET_NAME.search(a.split("=", 1)[0]):
                out.append(f"{a.split('=', 1)[0]}={MASK}")
            else:
                out.append(a)
        return out

    # -- the record --------------------------------------------------------------
    def record(self):
        ended = self.clock()
        state, code, reason = self.outcome or ("crashed", 1, "the launcher exited without an outcome")
        sha, dirty = self._git_state(self.tool_dir)
        ci = {k: self.env[k] for k in CI_ENV if self.env.get(k)}
        if {"GITHUB_REPOSITORY", "GITHUB_RUN_ID"} <= set(ci):
            server = self.env.get("GITHUB_SERVER_URL") or "https://github.com"
            ci["run_url"] = (f"{server}/{ci['GITHUB_REPOSITORY']}/actions/runs/"
                             f"{ci['GITHUB_RUN_ID']}"
                             + (f"/attempts/{ci['GITHUB_RUN_ATTEMPT']}"
                                if ci.get("GITHUB_RUN_ATTEMPT") else ""))
        capabilities = [r for r in self.external if r["name"] == "external/capabilities"]
        rec = {
            "schema_version": SCHEMA_VERSION,
            "tool": {"name": "test_template.py", "git_sha": sha, "git_dirty": dirty,
                     "python": sys.version.split()[0]},
            "run": {"id": self.run_id, "started_at": _utc(self.started), "ended_at": _utc(ended),
                    "duration_s": round(ended - self.started, 3), "exit_code": code,
                    "state": state, "reason": reason, "argv": self._argv_clean(),
                    "flags": self.flags, "attempts": dict(self.attempts),
                    "instance_id": self.instances[-1] if self.instances else None,
                    "instance_ids": list(self.instances)},
            "ci": ci,
            "subject": self.subject,
            "hardware": self.hardware,
            "capabilities": capabilities,
            "tests": self.tests,
            "external_checks": [r for r in self.external if r["name"] != "external/capabilities"],
        }
        return self._scrub_doc(rec)

    def summary(self, rec):
        def cell(text):
            return str(text).replace("|", "\\|").replace("\n", " ")[:300]
        run, subj = rec["run"], rec["subject"] or {}
        lines = [f"### Test proof — `{cell(subj.get('image', '?'))}`", "",
                 f"**{run['state']}** (exit {run['exit_code']})"
                 + (f" — {cell(run['reason'])}" if run["reason"] else ""), "",
                 "| | |", "|---|---|",
                 f"| template | `{cell(subj.get('template_hash', ''))}` "
                 f"{cell(subj.get('template_name', ''))} |",
                 f"| hardware | {cell(', '.join(f'{k}={v}' for k, v in rec['hardware'].items()) or 'not recorded')} |",
                 f"| capabilities | {cell('; '.join(c['detail'] for c in rec['capabilities']) or 'not resolved')} |",
                 f"| instance | {cell(', '.join(run['instance_ids']) or 'none')} |",
                 f"| run | {run['started_at']} → {run['ended_at']} ({run['duration_s']} s) |"]
        if rec["ci"].get("run_url"):
            lines.append(f"| CI run | {rec['ci']['run_url']} |")
        if rec["tests"] or rec["external_checks"]:
            lines += ["", "| check | state | detail |", "|---|---|---|"]
            lines += [f"| `{cell(t['name'])}` | {t['state']} | |" for t in rec["tests"]]
            lines += [f"| `{cell(r['name'])}` | {r['state']} | {cell(r['detail'])} |"
                      for r in rec["external_checks"]]
        return "\n".join(lines) + "\n"

    # -- writing -----------------------------------------------------------------
    def write(self, log=None):
        """Write the proof directory once; return its path, or None. Never raises:
        a proof that cannot be written is reported, and never changes the run's
        outcome."""
        if self.written or self.subject is None:
            return self.path
        self.written = True
        try:
            stamp = _dt.datetime.fromtimestamp(self.started, _dt.timezone.utc).strftime(
                "%Y%m%dT%H%M%SZ")
            prefix = re.sub(r"[^A-Za-z0-9]", "", self.subject["template_hash"])[:8] or "template"
            path = os.path.join(self.proof_dir, f"{stamp}-{prefix}-{self.run_id}")
            os.makedirs(os.path.join(path, LOG_DIR), exist_ok=True)
            rec = self.record()
            # Logs are held as streamed and scrubbed HERE, at finalisation, so values
            # learned after a line was streamed (an instance id, a token) still go.
            for name, lines in sorted(self.logs.items()):
                name = _safe_name(self.scrub(name))
                with open(os.path.join(path, LOG_DIR, name), "a", encoding="utf-8") as f:
                    f.write("".join(self.scrub(ln) + "\n" for ln in lines))
            with open(os.path.join(path, SUMMARY_FILE), "w", encoding="utf-8") as f:
                f.write(self.summary(rec))
            files = {}
            for root, _, names in os.walk(path):
                for n in sorted(names):
                    full = os.path.join(root, n)
                    rel = os.path.relpath(full, path).replace(os.sep, "/")
                    if rel != PROOF_FILE:
                        files[rel] = sha256_file(full)
            rec["integrity"] = {"algorithm": "sha256", "files": dict(sorted(files.items())),
                                "record_sha256": hashlib.sha256(canonical(rec)).hexdigest()}
            with open(os.path.join(path, PROOF_FILE), "w", encoding="utf-8") as f:
                json.dump(rec, f, sort_keys=True, indent=2, ensure_ascii=False)
                f.write("\n")
            self.path = path
        except Exception as e:                                    # noqa: BLE001
            if log:
                log(f"Proof could not be written: {type(e).__name__}: {e}")
            return None
        if log:
            log(f"Proof: {self.path}")
        return self.path
