"""The launcher's result-name table (external_checks.RESULT_NAMES) is what it reports.

qa-gate's `require_external` may name only a structural check the launcher can pass
for the gated image, and lint L126 enforces that from this table alone. So the table
must not drift from the launcher, in either direction:

  * every result the launcher reports belongs to a row, with that row's kind, and is
    reported passed only if the row says it can pass;
  * every row is reported by some run, and every row that can pass is seen passing;
  * every check name contract.py reports is in contract.CHECKS, and a name listed as
    never passing is only ever reported as an error.

The runs below drive run_external_checks with fakes that cover every row; the
contract's own names are read from its source, because its checks are ported
verbatim and keep their names as literals.
"""
from __future__ import annotations

import ast
import inspect
import io
import json
import math
import struct
import wave
import zlib

import pytest

import contract
import external_checks as ec

INSTANCE = {"public_ipaddr": "203.0.113.7",
            "ports": {"8000/tcp": [{"HostPort": "40000"}], "8188/tcp": [{"HostPort": "40188"}],
                      "8288/tcp": [{"HostPort": "40288"}], "8080/tcp": [{"HostPort": "40080"}]}}
ENGINE_TESTS = [{"name": "vllm.d/10-vllm-serving", "state": "passed"}]
COMFY_TESTS = [{"name": "comfyui.d/10-comfyui-serving", "state": "passed"}]
COMFY_PORTAL = ("localhost:8188:18188:/:ComfyUI|localhost:8288:18288:/docs:API Wrapper|"
                "localhost:8080:18080:/:Jupyter")


# ---------------------------------------------------------------- fakes

def _wav(seconds=3.0, rate=24000):
    buf = io.BytesIO()
    w = wave.open(buf, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(rate)
    w.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(i / 7)))
                           for i in range(int(seconds * rate))))
    w.close()
    return buf.getvalue()


def _png(w=96, h=80):
    rows = b"".join(b"\x00" + bytes(v for x in range(w) for v in [(x * 7 + y * 3) % 256] * 3)
                    for y in range(h))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


class _Engine:
    """A healthy OpenAI engine: every route answers well, chat only if served."""

    def __init__(self, chat=True):
        self.chat = chat

    def call(self, path, body=None):
        if path == "/v1/models":
            return 200, {"data": [{"id": "m"}]}
        if path == "/v1/chat/completions":
            if not self.chat:
                return 404, {"detail": "Not Found"}
            return 200, {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                         "usage": {"completion_tokens": 2}}
        if path == "/v1/completions":
            return 200, {"choices": [{"text": " Paris"}], "usage": {"completion_tokens": 2}}
        if path == "/v1/embeddings":
            table = dict(zip(ec.EMBED_TEXTS, ([1.0, 0.1, 0.0], [0.9, 0.2, 0.1], [0.0, 0.1, 1.0])))
            return 200, {"data": [{"index": i, "embedding": table[t]}
                                  for i, t in enumerate(body["input"])]}
        return 404, {"detail": "Not Found"}

    def call_raw(self, path, data, content_type, binary=False):
        if path == "/v1/audio/transcriptions":
            return 200, {"text": ec.ASR_REFERENCE}
        if path == "/v1/audio/speech":
            return 200, _wav()
        return 404, {"detail": "Not Found"}


class _Comfy:
    """The API wrapper and ComfyUI's /object_info in one."""

    def __init__(self, outputs, object_info=200):
        self.outputs, self.object_info = outputs, object_info

    def call(self, path, body=None):
        if path == "/object_info":
            if self.object_info != 200:
                return self.object_info, "error"
            return 200, {c: {"output_node": True} for c in ("SaveImage", "PreviewImage", "SaveVideo")}
        return 404, {}

    def call_raw_with_headers(self, path, data, ctype, headers, timeout=None, binary=False):
        import base64
        out = [{"filename": f"f{i}", "node_id": nid, "data": base64.b64encode(b).decode()}
               for i, (nid, b) in enumerate(self.outputs)]
        return 200, {"status": "completed", "output": out, "timings": {"generation_ms": 1}}


class _Jupyter:
    def __init__(self, workflows):
        self.files = {f"{n}.json": {"input": {"name": n, "workflow_json": wf}}
                      for n, wf in workflows.items()}

    def list_dir(self, path):
        return sorted(self.files), None

    def read_text(self, path):
        name = path.rsplit("/", 1)[1]
        return (json.dumps(self.files[name]), None) if name in self.files else (None, "HTTP 404")

    def read_bytes(self, path):
        return None, "HTTP 404"


# One workflow covering every ComfyUI row: a SaveImage that delivers an image (the
# workflow, the file and its content pass), a preview node (skipped), a video node that
# delivers nothing (failed), and a file from a node that is not an output node (error).
T2I = {"3": {"class_type": "KSampler", "inputs": {}},
       "9": {"class_type": "SaveImage", "inputs": {}},
       "10": {"class_type": "PreviewImage", "inputs": {}},
       "11": {"class_type": "SaveVideo", "inputs": {}}}


def _fake_run_contract(rep, cli, **_):
    """Every name contract.py can report, in a state it can be reported in."""
    for name, passes in contract.CHECKS.items():
        (rep.ok(name) if passes else rep.error(name, "fake"))


def _engine_run(monkeypatch, caps, chat=True, contract_raises=False):
    monkeypatch.setattr(contract, "run_contract", _fake_run_contract)
    if contract_raises:
        monkeypatch.setattr(ec, "check_contract",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    return ec.run_external_checks(
        INSTANCE, {"image": "vastai/vllm", "env": "-e VLLM_MODEL=m"}, ENGINE_TESTS,
        endpoint_factory=lambda h, p, t: _Engine(chat=chat), capabilities=caps,
        contract_client_factory=lambda base, token: object())


def _comfy_run(workflows, outputs, object_info=200):
    return ec.run_external_checks(
        INSTANCE, {"image": "vastai/comfyui", "env": f'-e PORTAL_CONFIG="{COMFY_PORTAL}"'},
        COMFY_TESTS, token="tok",
        endpoint_factory=lambda h, p, t: _Comfy(outputs, object_info),
        jupyter_factory=lambda h, p, t: _Jupyter(workflows))


def _all_runs(monkeypatch):
    everything = ",".join(c for c in ec.KNOWN_CAPABILITIES if c != ec.COMFYUI_CAPABILITY)
    runs = [
        _engine_run(monkeypatch, everything),                    # every route, chat served
        _engine_run(monkeypatch, "embeddings", chat=False),      # the undeclared-chat guard
        _engine_run(monkeypatch, "chat", contract_raises=True),  # the contract step's own name
        _comfy_run({"t2i": T2I}, [("9", _png()), ("42", _png())]),
        _comfy_run({"t2i": T2I}, [("9", _png())], object_info=500),
        _comfy_run({}, []),                                      # no payloads at all
    ]
    return [r for run in runs for r in run]


# ---------------------------------------------------------------- the drift tests

def test_every_reported_result_is_a_row_with_that_rows_kind(monkeypatch):
    for r in _all_runs(monkeypatch):
        row = ec.result_name(r["name"])
        assert row is not None, f"the launcher reports {r['name']!r}, which no row of RESULT_NAMES matches"
        if "kind" in r:
            assert r["kind"] == row.kind, (r, row)
        if r["state"] == "passed":
            assert row.passes, f"{r['name']!r} was reported passed; its row says it never is"


def test_every_row_is_reported_and_every_row_that_can_pass_is_seen_passing(monkeypatch):
    seen, passed = set(), set()
    for r in _all_runs(monkeypatch):
        row = ec.result_name(r["name"])
        seen.add(row)
        if r["state"] == "passed" and r.get("kind") == row.kind:
            passed.add(row)
    rows = set(ec.RESULT_NAMES)
    assert rows - seen == set(), f"rows no run reports: {sorted(r.shape for r in rows - seen)}"
    can_pass = {r for r in rows if r.passes}
    assert can_pass - passed == set(), \
        f"rows never seen passing: {sorted(r.shape for r in can_pass - passed)}"


def test_rows_are_distinct_and_each_name_matches_one_row():
    shapes = [r.shape for r in ec.RESULT_NAMES]
    assert len(shapes) == len(set(shapes))
    samples = {"<workflow>": "wf", "<class>": "SaveImage", "<node>": "9", "<n>": "1"}
    for row in ec.RESULT_NAMES:
        name = row.shape
        for k, v in samples.items():
            name = name.replace(k, v)
        assert ec.result_name(name) == row, (name, row)
    assert ec.result_name("external/nope") is None
    assert ec.result_name("vllm.d/10-vllm-serving") is None


def test_every_external_name_literal_in_the_launcher_is_a_row():
    """A new `external/...` name written into the launcher must be added to the table."""
    tree = ast.parse(inspect.getsource(ec))
    literals = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
                and isinstance(n.value, str) and n.value.startswith("external/")
                and " " not in n.value and not n.value.endswith("/")}
    assert literals, "found no name literals: this test is reading the wrong thing"
    missing = sorted(n for n in literals if ec.result_name(n) is None)
    assert not missing, f"names in external_checks.py with no row in RESULT_NAMES: {missing}"


_REPORT_METHODS = {"ok", "violation", "advisory", "error", "not_applicable"}


def _contract_reports():
    """{check name: set of Report methods it is reported with} from contract.py's
    source. Loop variables are bound from the loop they come from, evaluated in the
    module, so `for name in CHAT_ONLY[:5]` and f"cap:{name}" resolve to real names."""
    tree = ast.parse(inspect.getsource(contract))
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    ns = vars(contract)
    out = {}

    def methods_of(func):
        if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and func.value.id == "rep" and func.attr in _REPORT_METHODS):
            return {func.attr}
        if isinstance(func, ast.IfExp):
            return methods_of(func.body) | methods_of(func.orelse)
        return set()

    for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
        methods = methods_of(call.func)
        if not methods:
            continue
        arg = call.args[0]
        if isinstance(arg, ast.Constant):
            names = {arg.value}
        else:
            loop = parents.get(call)
            while loop is not None and not isinstance(loop, ast.For):
                loop = parents.get(loop)
            assert loop is not None, f"contract.py:{call.lineno}: a check name this test cannot resolve"
            items = eval(compile(ast.Expression(loop.iter), "contract.py", "eval"), ns)  # noqa: S307
            if isinstance(loop.target, ast.Name):
                targets, unpack = [loop.target.id], False
            else:
                targets, unpack = [t.id for t in loop.target.elts], True
            names = set()
            for item in items:
                env = dict(zip(targets, item if unpack else (item,)))
                names.add(eval(compile(ast.Expression(arg), "contract.py", "eval"), ns, env))  # noqa: S307
        for name in names:
            out.setdefault(name, set()).update(methods)
    return out


def test_contract_checks_lists_every_name_contract_py_reports():
    reports = _contract_reports()
    assert set(reports) == set(contract.CHECKS), (
        f"reported, not in CHECKS: {sorted(set(reports) - set(contract.CHECKS))}; "
        f"in CHECKS, never reported: {sorted(set(contract.CHECKS) - set(reports))}")


@pytest.mark.parametrize("name", sorted(contract.CHECKS))
def test_a_contract_check_can_pass_exactly_when_checks_says_so(name):
    """`ok` is the only way a contract check is reported passed; a name listed as never
    passing must only ever be an error."""
    methods = _contract_reports()[name]
    if contract.CHECKS[name]:
        assert "ok" in methods, f"{name} is listed as able to pass and is never reported ok"
    else:
        assert methods == {"error"}, f"{name} is listed as never passing and is reported {methods}"
