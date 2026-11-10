"""Every run leaves a proof (proof.py, ADR 0059 D10).

What must hold: the record is built from an allowlist, so no host data reaches any
proof file whatever the run carried; secrets are masked; the integrity block lets
verify_proof.py detect any change; and nothing is written before a template is
resolved.
"""
from __future__ import annotations

import json
import os

import pytest

import proof as P
import verify_proof as V
from test_template import redact_ips

API_KEY = "vk_live_0123456789abcdef0123456789abcdef"
INSTANCE_TOKEN = "f00dfacecafebeef0123456789abcdef0123456789abcdef"
HF_TOKEN = "hf_AbCdEfGhIjKlMnOpQrStUvWxYz012345"
INSTANCE_ID, MACHINE_ID, HOST_ID, OFFER_ID = "31415926", "271828", "1618033", "4669201"
HOST_DATA = ("203.0.113.57", "198.51.100.23", "2001:db8::42", MACHINE_ID,
             HOST_ID, OFFER_ID, "ssh9.example.com", "40022", "Iceland, IS", "dc-west-17",
             "box-17.example.net", API_KEY, INSTANCE_TOKEN, HF_TOKEN,
             API_KEY[:8], INSTANCE_TOKEN[:8], HF_TOKEN[:8])     # prefixes are secrets too

# A realistic instance record: everything Vast reports about a running box,
# descriptive fields and identifying ones side by side.
INSTANCE = {
    "id": int(INSTANCE_ID), "machine_id": int(MACHINE_ID), "host_id": int(HOST_ID),
    "public_ipaddr": "203.0.113.57", "local_ipaddrs": "198.51.100.23 ",
    "ssh_host": "ssh9.example.com", "ssh_port": 40022, "geolocation": "Iceland, IS",
    "datacenter": "dc-west-17", "hostname": "box-17.example.net",
    "jupyter_token": INSTANCE_TOKEN,
    "ports": {"8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "40000"}]},
    "gpu_name": "RTX A4000", "num_gpus": 1, "gpu_ram": 16376, "gpu_total_ram": 16376,
    "cpu_arch": "amd64", "compute_cap": 860, "cuda_max_good": 13.0,
    "driver_version": "595.91.07", "image_digest": "sha256:" + "ab" * 32,
}


def _recorder(tmp_path, **kw):
    argv = ["abc123def", "--api-key", API_KEY, "--instance", INSTANCE_ID,
            f"--env=HF_TOKEN={HF_TOKEN}", "--env", "VLLM_ARGS=--max-model-len 4096", "--raw"]
    rec = P.ProofRecorder(str(tmp_path / "proofs"), argv, redact_ips,
                          env=kw.get("env", {}), git_state=lambda p: ("c0ffee" * 6 + "abcd", False))
    rec.add_secret(API_KEY)
    return rec


def _full_run(tmp_path, **kw):
    rec = _recorder(tmp_path, **kw)
    rec.begin("abc123def", {"name": "vLLM QA", "id": 999}, "vastai/vllm:v0.32.0",
              [f"HF_TOKEN={HF_TOKEN}", "VLLM_ARGS=--max-model-len 4096",
               "MY_API_KEY=plain-but-named-secret"],
              {"instance": True, "offer": False, "keep": True})
    for hid in (MACHINE_ID, OFFER_ID, HOST_ID):
        rec.add_host_id(hid)
    rec.add_secret(INSTANCE_TOKEN)
    rec.instance_created(INSTANCE_ID)
    rec.set_hardware(INSTANCE)
    rec.add_test_output("─── Running: base/10-supervisor ───")
    rec.add_test_output(f"  ssh root@203.0.113.57 -p 40022 for instance {INSTANCE_ID}")
    rec.add_log_line("/var/log/portal/vllm.log",
                     f"INFO peer 198.51.100.23 token {INSTANCE_TOKEN} machine {MACHINE_ID}")
    rec.add_log_line("/var/log/portal/vllm.log", "\x1b[2m2001:db8::42 accepted")
    # The in-image caddy-auth test's own lines, as streamed: the container label glues
    # the instance id inside a cookie name, and the token is printed as a prefix.
    tok8 = INSTANCE_TOKEN[:8]
    rec.add_test_output(f"  cookie received: C.{INSTANCE_ID}_auth_token={tok8}...")
    rec.add_test_output(f"  cookie C.{INSTANCE_ID}_auth_token={tok8}... → 200 ok")
    rec.add_test_output(f"  cookie C.{INSTANCE_ID}_auth_token=FAKE_VALUE → 401 ok")
    rec.add_log_line("/var/log/portal/caddy.log",
                     f"peer 203.0.113.57 machine={MACHINE_ID} host box-17.example.net "
                     f"token {INSTANCE_TOKEN[:12]}")
    rec.set_results(
        tests=[{"name": "base/10-supervisor", "state": "passed", "duration_s": 3,
                "host": "203.0.113.57"}],
        external=[{"name": "external/capabilities", "state": "info",
                   "detail": "chat, completions (OPENAI_ROUTES unset: pyworker default)"},
                  {"name": "external/chat-inference", "state": "failed", "kind": "structural",
                   "detail": f"203.0.113.57:40000 instance {INSTANCE_ID} said no",
                   "machine_id": int(MACHINE_ID)},
                  {"name": "contract/identity", "state": "passed", "kind": "structural",
                   "detail": ""}])
    rec.set_outcome("failed", 1)
    return rec


def _all_text(path):
    out = []
    for root, _, names in os.walk(path):
        for n in names:
            with open(os.path.join(root, n), encoding="utf-8") as f:
                out.append((os.path.relpath(os.path.join(root, n), path), f.read()))
    return out


# ---------------------------------------------------------------- the hard rule

def test_no_host_data_appears_in_any_proof_file(tmp_path):
    path = _full_run(tmp_path).write()
    files = _all_text(path)
    assert {f for f, _ in files} >= {"proof.json", "summary.md", "logs/vllm.log",
                                     "logs/test-output.log"}
    for name, text in files:
        for value in HOST_DATA:
            assert value not in name and value not in text, f"{value!r} leaked into {name}"
    out = open(os.path.join(path, "logs/test-output.log")).read()
    # The instance id stays (it ties the proof to the run); the token prefix does not.
    assert f"cookie received: C.{INSTANCE_ID}_auth_token=***..." in out
    rec = json.loads(open(os.path.join(path, "proof.json")).read())
    assert rec["run"]["instance_id"] == INSTANCE_ID and rec["run"]["instance_ids"] == [INSTANCE_ID]
    assert f"| instance | {INSTANCE_ID} |" in open(os.path.join(path, "summary.md")).read()


def test_the_record_is_built_from_an_allowlist(tmp_path):
    rec = json.loads(open(os.path.join(_full_run(tmp_path).write(), "proof.json")).read())
    assert set(rec) == {"schema_version", "tool", "run", "ci", "subject", "hardware",
                        "capabilities", "tests", "external_checks", "integrity"}
    assert set(rec["hardware"]) <= set(P.HARDWARE_FIELDS)
    assert rec["hardware"]["gpu_name"] == "RTX A4000"
    assert rec["subject"]["image_digest"] == "sha256:" + "ab" * 32
    assert rec["tests"] == [{"name": "base/10-supervisor", "state": "passed", "duration_s": 3}]
    assert set(rec["external_checks"][0]) == {"name", "state", "kind", "detail"}
    assert rec["capabilities"][0]["detail"].startswith("chat, completions")
    assert rec["run"]["attempts"] == {"launched": 1, "discarded": 0}
    assert rec["tool"]["git_sha"].startswith("c0ffee") and rec["tool"]["git_dirty"] is False


def test_secrets_are_masked_in_argv_env_and_text(tmp_path):
    rec = json.loads(open(os.path.join(_full_run(tmp_path).write(), "proof.json")).read())
    assert rec["run"]["argv"][:3] == ["abc123def", "--api-key", "***"]
    assert "--env=HF_TOKEN=***" in rec["run"]["argv"]
    env = {e["name"]: e["value"] for e in rec["subject"]["env_overrides"]}
    assert env == {"HF_TOKEN": "***", "VLLM_ARGS": "--max-model-len 4096", "MY_API_KEY": "***"}


def test_ci_context_ties_the_proof_to_the_run(tmp_path):
    env = {"GITHUB_REPOSITORY": "example/base-image", "GITHUB_RUN_ID": "123456789",
           "GITHUB_RUN_ATTEMPT": "2", "GITHUB_SHA": "deadbeef", "GITHUB_WORKFLOW": "Build vLLM",
           "GITHUB_JOB": "qa", "GITHUB_SERVER_URL": "https://github.com"}
    rec = json.loads(open(os.path.join(_full_run(tmp_path, env=env).write(), "proof.json")).read())
    assert rec["ci"]["run_url"] == ("https://github.com/example/base-image/actions/runs/"
                                    "123456789/attempts/2")
    assert rec["ci"]["GITHUB_JOB"] == "qa"


def test_nothing_is_written_before_a_template_is_resolved(tmp_path):
    rec = _recorder(tmp_path)
    rec.set_outcome("config_error", 4, "no api key")
    assert rec.write() is None and not (tmp_path / "proofs").exists()


def test_a_run_without_an_outcome_is_recorded_as_crashed(tmp_path):
    rec = _recorder(tmp_path)
    rec.begin("abc123def", {}, "vastai/vllm:v1", [], {})
    data = json.loads(open(os.path.join(rec.write(), "proof.json")).read())
    assert data["run"]["state"] == "crashed" and data["run"]["exit_code"] == 1


def test_the_directory_name_is_timestamp_template_prefix_and_run_id(tmp_path):
    rec = _full_run(tmp_path)
    name = os.path.basename(rec.write())
    stamp, prefix, run_id = name.split("-")
    assert stamp.endswith("Z") and "T" in stamp and prefix == "abc123de" and run_id == rec.run_id


def test_write_is_once_and_never_raises(tmp_path):
    rec = _full_run(tmp_path)
    first = rec.write()
    assert rec.write() == first
    bad = _full_run(tmp_path)
    bad.proof_dir = str(tmp_path / "a-file")
    (tmp_path / "a-file").write_text("not a directory")
    said = []
    assert bad.write(said.append) is None and "could not be written" in said[0]


def test_summary_names_the_verdict_and_every_check(tmp_path):
    text = open(os.path.join(_full_run(tmp_path).write(), "summary.md")).read()
    assert "**failed** (exit 1)" in text and "`external/chat-inference` | failed" in text
    assert "`base/10-supervisor` | passed" in text and "RTX A4000" in text


# ---------------------------------------------------------------- the verifier

def test_a_fresh_proof_verifies(tmp_path):
    path = _full_run(tmp_path).write()
    assert V.verify(path) == (V.EXIT_OK, [])
    assert V.main([path]) == 0


@pytest.mark.parametrize("tamper,expect", [
    (lambda p: open(os.path.join(p, "logs/vllm.log"), "a").write("forged\n"), "hash mismatch"),
    (lambda p: os.remove(os.path.join(p, "summary.md")), "missing file"),
    (lambda p: open(os.path.join(p, "extra.txt"), "w").write("x"), "not in the proof"),
], ids=["edited-file", "removed-file", "added-file"])
def test_a_changed_file_fails_verification(tmp_path, tamper, expect):
    path = _full_run(tmp_path).write()
    tamper(path)
    code, problems = V.verify(path)
    assert code == V.EXIT_MISMATCH and any(expect in p for p in problems)


def test_an_edited_record_fails_verification(tmp_path):
    path = _full_run(tmp_path).write()
    f = os.path.join(path, "proof.json")
    data = json.load(open(f))
    data["run"]["state"] = "passed"
    json.dump(data, open(f, "w"))
    assert V.verify(path)[0] == V.EXIT_MISMATCH


def test_an_unknown_schema_is_refused(tmp_path):
    path = _full_run(tmp_path).write()
    f = os.path.join(path, "proof.json")
    data = json.load(open(f))
    data["schema_version"] = 99
    json.dump(data, open(f, "w"))
    assert V.verify(path)[0] == V.EXIT_UNKNOWN_SCHEMA and V.main([path]) == 2


def test_an_unreadable_proof_fails(tmp_path):
    assert V.verify(str(tmp_path))[0] == V.EXIT_MISMATCH
    assert V.main([]) == V.EXIT_MISMATCH


# ---------------------------------------------------------------- every channel is scrubbed

SENTINEL_ID = "86753090"          # a learned host id (a machine id), planted everywhere
SENTINEL_HOST = "sentinel-box.example.org"
SENTINEL_SECRET = "s3ntinelsecretvalue0123456789"


def test_every_input_channel_is_scrubbed_in_every_file_and_file_name(tmp_path):
    """A host value planted in EACH channel a proof is built from must reach no file,
    by content or by name: if a new channel bypasses the scrub, this fails."""
    planted = f"{SENTINEL_ID} {SENTINEL_HOST} {SENTINEL_SECRET[:10]} 203.0.113.99"
    rec = P.ProofRecorder(str(tmp_path / "proofs"),
                          ["abc123", "--label", planted, f"--env=X={planted}"], redact_ips,
                          env={"GITHUB_WORKFLOW": planted}, git_state=lambda p: ("x", False))
    rec.add_secret(SENTINEL_SECRET)
    rec.begin("abc123", {"name": planted}, f"vastai/vllm:{SENTINEL_ID}", [f"Y={planted}"],
              {"instance": True})
    rec.instance_created("12121212")
    rec.set_hardware({"machine_id": SENTINEL_ID, "hostname": SENTINEL_HOST, "gpu_name": planted,
                      "driver_version": planted})
    rec.add_test_output(planted)
    rec.add_log_line(f"/var/log/{SENTINEL_HOST}.log", planted)
    rec.add_log_line(f"/var/log/C.{SENTINEL_ID}_x.log", planted)
    rec.add_log_line("", planted)
    rec.set_results(tests=[{"name": planted, "state": planted}],
                    external=[{"name": "external/capabilities", "state": "info", "detail": planted},
                              {"name": planted, "state": "failed", "kind": planted,
                               "detail": planted}])
    rec.set_outcome(planted, 1, reason=planted)
    path = rec.write()
    for name, text in _all_text(path):
        for value in (SENTINEL_ID, SENTINEL_HOST, SENTINEL_SECRET[:8], "203.0.113.99"):
            assert value not in name and value not in text, f"{value!r} in {name}"
    assert V.verify(path) == (V.EXIT_OK, [])


def test_a_secret_prefix_is_masked_but_a_shorter_coincidence_is_not():
    rec = P.ProofRecorder("unused", [], redact_ips, env={})
    rec.add_secret("abcdefgh12345678")
    assert rec.scrub("x abcdefgh... y abcdefgh1234 z abcdefgh12345678!") == "x ***... y *** z ***!"
    assert rec.scrub("abcdefg is seven") == "abcdefg is seven"


def test_a_numeric_id_is_scrubbed_inside_tokens_but_not_inside_longer_numbers():
    rec = P.ProofRecorder("unused", [], redact_ips, env={})
    rec.add_host_id("40022")
    assert rec.scrub("C.40022_auth ssh -p40022 x") == "C.[redacted-id]_auth ssh -p[redacted-id] x"
    assert rec.scrub("ts 1731240022 and 400221") == "ts 1731240022 and 400221"


def test_argv_records_that_a_local_path_was_given_not_the_path(tmp_path):
    rec = P.ProofRecorder(str(tmp_path), ["abc", "--proof-dir", "/home/someone/proofs",
                                          "--proof-dir=/home/someone/p2", "--log",
                                          "/var/log/portal/vllm.log"], redact_ips, env={})
    assert rec._argv_clean() == ["abc", "--proof-dir", "<given>", "--proof-dir=<given>",
                                 "--log", "/var/log/portal/vllm.log"]


def test_the_home_directory_never_reaches_a_proof(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", "/home/someone")
    rec = P.ProofRecorder(str(tmp_path / "p"), [], redact_ips, env={})
    assert rec.scrub("Traceback: /home/someone/vast/x.py line 3") == "Traceback: ~/vast/x.py line 3"


def test_every_attempts_instance_id_is_recorded_in_order(tmp_path):
    rec = _recorder(tmp_path)
    rec.begin("abc123", {}, "vastai/vllm:v1", [], {})
    for created in ("11111111", None, "22222222"):          # a redraw: first one discarded
        rec.instance_created(created)
    rec.set_outcome("passed", 0)
    data = json.loads(open(os.path.join(rec.write(), "proof.json")).read())
    assert data["run"]["instance_ids"] == ["11111111", "22222222"]
    assert data["run"]["instance_id"] == "22222222"
    assert data["run"]["attempts"] == {"launched": 2, "discarded": 1}
