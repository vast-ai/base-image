"""Features of the chat model, named by the caller with --features (ADR 0068), and the
first of them, vision.

What these pin:

  1. --features is a declaration and is never absorbed: an unknown name is a
     configuration error, a feature whose route is not declared is an error row, a
     feature with no check yet is skipped, and none of it changes the routes checked;
  2. the first result names the features and where they came from, and is unchanged
     when none is given; the --raw output and the proof carry the same;
  3. the vision structural check passes only when the image request is accepted and
     costs more prompt tokens than the text alone;
  4. the vision answer check passes only when the reply contains the number in the
     fixture, and stops at its retry limit;
  5. the committed fixture is the file its checksum names and shows VISION_TOKEN.
"""
from __future__ import annotations

import base64
import glob
import hashlib
import importlib.util
import json
import os
import struct
import sys
import zlib

import pytest

import external_checks as ec
import test_result_names as fakes
from test_attach_instance import launcher  # noqa: F401 - a pytest fixture

vision = ec.checks.vision
VLLM = {"image": "vastai/vllm", "env": "-e VLLM_MODEL=m"}


@pytest.fixture(autouse=True)
def _no_contract(monkeypatch):
    monkeypatch.setattr(ec.checks.contract_step, "check_contract", lambda *a, **k: [])


def _run(caps, features=None, endpoint=None):
    endpoint = endpoint or fakes._Engine()
    return ec.run_external_checks(fakes.INSTANCE, VLLM, fakes.ENGINE_TESTS,
                                  endpoint_factory=lambda h, p, t: endpoint,
                                  capabilities=caps, features=features)


def _by_name(out):
    return {r["name"]: r for r in out if r["name"] != ec.CAPABILITIES_RESULT}


# ---------------------------------------------------------------- 1. the declaration

def test_an_unknown_type_fails_argument_handling_listing_the_valid_names(monkeypatch, capsys):
    import test_template as tt
    monkeypatch.setattr(sys, "argv", ["test_template.py", "abc123", "--features", "vision,vison"])
    with pytest.raises(SystemExit) as e:
        tt.parse_args()
    assert e.value.code == tt.EXIT_CONFIG_ERROR
    captured = capsys.readouterr()
    assert "unknown feature vison" in captured.err
    for name in ec.KNOWN_FEATURES:
        assert name in captured.err
    assert json.loads(captured.out)["state"] == "config_error"


def test_an_unknown_type_given_in_code_is_an_error_row_not_dropped():
    out = _run("chat", features="vison")
    assert {"name": ec.CAPABILITIES_RESULT, "state": "error", "advisory": True,
            "detail": "unknown feature vison"} in out
    assert "features" not in out[0]["detail"]


def test_vision_without_chat_is_an_error_and_the_other_checks_still_run():
    class Recording(fakes._Engine):
        bodies = []

        def call(self, path, body=None):
            self.bodies.append(body)
            return super().call(path, body)

    endpoint = Recording(chat=False)
    out = _by_name(_run("embeddings", features="vision", endpoint=endpoint))
    assert out[vision.VISION_CHECK]["state"] == "error"
    assert "needs chat" in out[vision.VISION_CHECK]["detail"]
    assert vision.VISION_READ_CHECK not in out
    assert out[ec.EMBEDDINGS_CHECK]["state"] == "passed"
    assert out[ec.UNDECLARED_CHAT_CHECK]["state"] == "passed"
    assert not any(isinstance(b["messages"][0]["content"], list)
                   for b in endpoint.bodies if b and "messages" in b), "an image was sent"


@pytest.mark.parametrize("feature", sorted(set(ec.KNOWN_FEATURES) - {"vision"}))
def test_a_declared_feature_with_no_check_yet_is_skipped_never_passed(feature):
    out = _by_name(_run("chat", features=feature))
    assert out[f"external/{feature}"]["state"] == "skipped"
    assert "no external check" in out[f"external/{feature}"]["detail"]
    # Without its route it is the declaration that is wrong, so that is what is said.
    assert _by_name(_run("embeddings", features=feature))[f"external/{feature}"]["state"] == "error"


def test_types_add_checks_and_leave_the_routes_as_they_were():
    plain, typed = _run("chat,embeddings"), _run("chat,embeddings", features="vision,tools")
    added = {vision.VISION_CHECK, vision.VISION_READ_CHECK, "external/tools"}
    assert [r for r in typed[1:] if r["name"] not in added] == plain[1:]
    assert {r["name"] for r in typed} - {r["name"] for r in plain} == added
    # --features never implies a route: with no engine route there is nothing to run.
    alone = ec.run_external_checks(fakes.INSTANCE, {"image": "vastai/base-image", "env": ""},
                                   [{"name": "base/10-supervisor", "state": "passed"}],
                                   endpoint_factory=None, features="vision")
    assert [(r["name"], r["state"]) for r in alone] == [
        (ec.CAPABILITIES_RESULT, "info"), (vision.VISION_CHECK, "error")]


# ---------------------------------------------------------------- 2. what is recorded

def test_the_first_result_names_the_features_only_when_some_are_given():
    template = {"image": "vastai/vllm", "env": ""}
    run = lambda features: ec.run_external_checks(                              # noqa: E731
        fakes.INSTANCE, template, fakes.ENGINE_TESTS, features=features,
        endpoint_factory=lambda h, p, t: fakes._Engine())[0]
    assert run(None) == {"name": ec.CAPABILITIES_RESULT, "state": "info", "advisory": True,
                         "detail": "chat, completions (OPENAI_ROUTES unset: pyworker default)"}
    assert run("vision, tools") == {
        "name": ec.CAPABILITIES_RESULT, "state": "info", "advisory": True,
        "detail": "chat, completions (OPENAI_ROUTES unset: pyworker default); "
                  "features: tools, vision (--features)"}


def test_the_launcher_passes_types_through_and_records_them(launcher, monkeypatch, capsys, tmp_path):  # noqa: F811
    import test_template as tt
    monkeypatch.setattr(tt, "run_external_checks", ec.run_external_checks)
    code, _ = launcher("--instance", "777", "--raw", "--features", "vision")
    assert code == 0
    row = json.loads(capsys.readouterr().out)["external_checks"][0]
    assert row["name"] == ec.CAPABILITIES_RESULT
    assert row["detail"].endswith("; features: vision (--features)")
    proofs = glob.glob(str(tmp_path / "qa-proofs" / "*" / "proof.json"))
    assert len(proofs) == 1
    with open(proofs[0]) as f:
        proof = json.load(f)
    recorded = proof["capabilities"][0]
    assert recorded["detail"].endswith("; features: vision (--features)")
    assert [(r["name"], r["state"]) for r in proof["external_checks"]] == [
        (vision.VISION_CHECK, "error")]


# ---------------------------------------------------------------- 3. vision, structural

class _Model:
    """A chat endpoint. `text` and `image` answer a request without and with an image:
    each is (status, body), or a function of the request body returning that."""

    def __init__(self, text, image):
        self.text, self.image, self.bodies = text, image, []

    def call(self, path, body=None):
        if path == "/v1/models":
            return 200, {"data": [{"id": "m"}]}
        self.bodies.append(body)
        has_image = isinstance(body["messages"][0]["content"], list)
        answer = self.image if has_image else self.text
        return answer(body) if callable(answer) else answer


def _reply(prompt_tokens, content="ok"):
    usage = {"completion_tokens": 1}
    if prompt_tokens is not None:
        usage["prompt_tokens"] = prompt_tokens
    return 200, {"choices": [{"message": {"role": "assistant", "content": content}}],
                 "usage": usage}


def _states(results):
    assert [r["name"] for r in results] == [vision.VISION_CHECK, vision.VISION_READ_CHECK]
    assert [r["kind"] for r in results] == ["structural", "answer"]
    return [r["state"] for r in results]


def test_an_image_that_costs_prompt_tokens_passes_and_is_sent_as_a_data_uri():
    ep = _Model(_reply(12), _reply(12 + vision.VISION_MIN_IMAGE_TOKENS, vision.VISION_TOKEN))
    results = vision.check_vision(ep)
    assert _states(results) == ["passed", "passed"]
    text, image = ep.bodies[0], ep.bodies[1]
    assert text["messages"][0]["content"] == vision.VISION_PROMPT
    parts = image["messages"][0]["content"]
    assert parts[0] == {"type": "text", "text": vision.VISION_PROMPT}
    with open(vision.VISION_FIXTURE, "rb") as f:
        assert parts[1] == {"type": "image_url", "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(f.read()).decode()}}
    for body in ep.bodies:
        assert body["temperature"] == 0 and body["model"] == "m"
    assert text["max_tokens"] == image["max_tokens"] == vision.VISION_MAX_TOKENS


@pytest.mark.parametrize("added", [0, 3, vision.VISION_MIN_IMAGE_TOKENS - 1])
def test_an_image_that_adds_too_few_prompt_tokens_fails(added):
    results = vision.check_vision(_Model(_reply(12), _reply(12 + added, vision.VISION_TOKEN)))
    assert _states(results) == ["failed", "skipped"]
    assert f"added {added} prompt tokens" in results[0]["detail"]


def test_an_engine_that_refuses_the_image_fails_with_its_status_and_body():
    ep = _Model(_reply(12), (400, '{"error": "this model does not support image input"}'))
    results = vision.check_vision(ep)
    assert _states(results) == ["failed", "skipped"]
    assert "HTTP 400" in results[0]["detail"]
    assert "does not support image input" in results[0]["detail"]
    assert len(ep.bodies) == 2, "the read must not be asked once the image was refused"


@pytest.mark.parametrize("text,image", [((0, "TimeoutError: timed out"), _reply(300)),
                                        (_reply(12), (0, "TimeoutError: timed out"))])
def test_no_response_is_an_error_not_a_failure(text, image):
    results = vision.check_vision(_Model(text, image))
    assert _states(results) == ["error", "skipped"]
    assert "no HTTP response" in results[0]["detail"]


@pytest.mark.parametrize("text,image", [(_reply(None), _reply(300)), (_reply(12), _reply(None))])
def test_a_response_without_usage_fails_and_says_so(text, image):
    results = vision.check_vision(_Model(text, image))
    assert _states(results) == ["failed", "skipped"]
    assert "no usage.prompt_tokens" in results[0]["detail"]


# ---------------------------------------------------------------- 4. vision, answer

@pytest.mark.parametrize("reply", [vision.VISION_TOKEN, "The number is 7319.", " 7 3\n1 9 "])
def test_a_reply_containing_the_number_passes_the_read(reply):
    assert _states(vision.check_vision(_Model(_reply(12), _reply(300, reply)))) == ["passed", "passed"]


def test_a_reply_without_the_number_fails_the_read_after_the_retry_limit():
    ep = _Model(_reply(12), _reply(300, "7318"))
    results = vision.check_vision(ep)
    assert _states(results) == ["passed", "failed"]
    reads = [b for b in ep.bodies if b["messages"][0]["content"][0] ==
             {"type": "text", "text": vision.VISION_READ_PROMPT}]
    assert len(reads) == 1 + vision.VISION_READ_RETRIES == 3
    assert all(b["temperature"] == 0 for b in reads)
    assert "'7318'" in results[1]["detail"]


def test_the_read_passes_on_a_retry_and_stops_there():
    replies = iter(["no idea", vision.VISION_TOKEN, "unreachable"])
    ep = _Model(_reply(12), lambda body: _reply(
        300, next(replies) if body["max_tokens"] == vision.VISION_READ_MAX_TOKENS else "ok"))
    assert _states(vision.check_vision(ep)) == ["passed", "passed"]
    assert next(replies) == "unreachable"


def test_a_read_that_gets_no_response_is_an_error_and_the_structural_result_stands():
    ep = _Model(_reply(12), lambda body: _reply(300) if body["max_tokens"] == vision.VISION_MAX_TOKENS
                else (0, "TimeoutError: timed out"))
    assert _states(vision.check_vision(ep)) == ["passed", "error"]


# ---------------------------------------------------------------- 5. the fixture

FIXTURE_DIR = os.path.dirname(vision.VISION_FIXTURE)


def test_the_committed_fixture_is_the_file_its_checksum_names():
    with open(os.path.join(FIXTURE_DIR, "SHA256SUMS")) as f:
        sums = dict(reversed(line.split()) for line in f if line.strip())
    name = os.path.basename(vision.VISION_FIXTURE)
    with open(vision.VISION_FIXTURE, "rb") as f:
        assert hashlib.sha256(f.read()).hexdigest() == sums[name]
    committed = set(os.listdir(FIXTURE_DIR)) - {"SHA256SUMS", "generate.py", "__pycache__"}
    assert committed == set(sums), "every fixture file has a checksum, and only those"


def test_the_fixture_shows_the_number_the_read_check_asks_for():
    """The PNG's pixels are the generator's drawing of VISION_TOKEN, so changing the
    number without redrawing the image (or the reverse) fails here."""
    spec = importlib.util.spec_from_file_location("_vision_fixture_generator",
                                                  os.path.join(FIXTURE_DIR, "generate.py"))
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    with open(vision.VISION_FIXTURE, "rb") as f:
        data = f.read()
    pos, idat, size = 8, b"", None
    while pos < len(data):
        length, tag = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            size = struct.unpack(">II", body[:8])
            assert body[8:] == bytes([8, 2, 0, 0, 0]), "8-bit RGB, not interlaced"
        elif tag == b"IDAT":
            idat += body
        pos += 12 + length
    width, height = size
    raw = zlib.decompress(idat)
    stride = 1 + width * 3
    rows = []
    for y in range(height):
        line = raw[y * stride:(y + 1) * stride]
        assert line[0] == 0, "the generator writes unfiltered rows"
        assert line[1::3] == line[2::3] == line[3::3]
        rows.append(line[1::3])
    assert rows == generator.raster(vision.VISION_TOKEN)
    assert rows != generator.raster("7318")
