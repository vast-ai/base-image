"""The test-type registry (checks.REGISTRY) is complete and the tables read from it agree.

A test type is one module in the `checks` package (ADR 0068). What these pin:

  1. every module in the package is registered, exactly once;
  2. no two types report the same name, and a type's `run` reports only names it
     declares, with the declared kind;
  3. the capability and result-name tables external_checks derives from the registry
     are consistent with it and with each other;
  4. the undeclared-chat guard is selected last, whatever else is selected;
  5. external_checks reads the package beside it, not one that merely has its name;
  6. a feature type is selected only when the caller names it and its route is
     declared, and its name is one of the known features.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import os
import pkgutil
import shutil

import pytest

import checks
import contract
import external_checks as ec
import test_result_names as fakes

MODULES = [importlib.import_module(f"checks.{m.name}") for m in pkgutil.iter_modules(checks.__path__)]


# ---------------------------------------------------------------- 1. registration

def test_every_module_in_the_package_is_registered_exactly_once():
    assert MODULES, "found no modules: this test is reading the wrong thing"
    for module in MODULES:
        types = [v for v in vars(module).values() if isinstance(v, checks.TestType)]
        assert len(types) == 1, f"{module.__name__} must export exactly one TestType, has {len(types)}"
        assert module.TYPE is types[0]
        assert sum(t is module.TYPE for t in ec.REGISTRY) == 1, \
            f"{module.__name__}.TYPE is not listed exactly once in checks.REGISTRY"
    assert len(ec.REGISTRY) == len(MODULES)


def test_names_and_orders_are_distinct_and_fixtures_exist():
    assert len({t.name for t in ec.REGISTRY}) == len(ec.REGISTRY)
    assert len({t.order for t in ec.REGISTRY}) == len(ec.REGISTRY)
    for t in ec.REGISTRY:
        for path in t.fixtures:
            assert os.path.isfile(path), f"{t.name}: fixture {path} is missing"


def test_an_unknown_axis_is_refused():
    with pytest.raises(ValueError):
        checks.TestType(name="x", axis="nope", requires=(), images=checks.ANY_IMAGE,
                        results=(), run=lambda ctx: [], order=1)


# ---------------------------------------------------------------- 2. result names

def test_no_two_types_report_the_same_name():
    owner = {}
    for t in ec.REGISTRY:
        assert t.results, f"{t.name} declares no result names"
        for row in t.results:
            assert row.shape not in owner, f"{row.shape} is declared by {owner[row.shape]} and {t.name}"
            owner[row.shape] = t.name
            assert row.images == t.images, f"{t.name}: {row.shape} is scoped to {row.images}"
            assert row.kind in (checks.STRUCTURAL, checks.ANSWER, None)


def test_the_names_reported_for_a_type_that_did_not_run_are_its_own():
    ctx = _context(fakes._Engine())
    for t in ec.REGISTRY:
        names = t.names_if_not_run(ctx)
        assert names, f"{t.name} names nothing to report when the deadline leaves it no time"
        for name in names:
            assert ec.result_name(name) in t.results, f"{t.name}: {name} is not one of its rows"


class _Refusing(fakes._Engine):
    """An engine that refuses every route but /v1/models."""

    def call(self, path, body=None):
        return super().call(path, body) if path == "/v1/models" else (404, {"detail": "Not Found"})

    def call_raw(self, path, data, content_type, binary=False):
        return 404, {"detail": "Not Found"}


class _Silent:
    """Nothing answers: every request gets no HTTP response."""

    def call(self, path, body=None):
        return 0, "TimeoutError: timed out"

    def call_raw(self, path, data, content_type, binary=False):
        return 0, "TimeoutError: timed out"

    def call_raw_with_headers(self, path, data, ctype, headers, timeout=None, binary=False):
        return 0, "TimeoutError: timed out"


def _context(endpoint, env="-e VLLM_MODEL=m", comfy=None, workflows=None):
    comfy = comfy or fakes._Comfy([("9", fakes._png()), ("42", fakes._png())])
    return checks.Context(
        template={"image": "vastai/vllm", "env": env}, env=env, env_overrides=(),
        host="203.0.113.7", token="tok", capabilities=set(ec.KNOWN_CAPABILITIES),
        engine_capabilities=set(ec._ENGINE_CAPABILITIES),
        deadline=contract.Deadline(3600, lambda: 0.0),
        contract_client_factory=lambda base, token: object(), engine_port="40000",
        endpoint=endpoint, wrapper=comfy, comfy=comfy,
        jupyter=fakes._Jupyter({"t2i": fakes.T2I} if workflows is None else workflows),
        generate_timeout=60)


@pytest.mark.parametrize("test_type", ec.REGISTRY, ids=lambda t: t.name)
def test_a_type_reports_only_the_names_it_declares(test_type, monkeypatch):
    monkeypatch.setattr(contract, "run_contract", fakes._fake_run_contract)
    silent = _Silent()
    contexts = [
        _context(fakes._Engine()),
        _context(fakes._Engine(chat=False)),
        _context(_Refusing()),
        _context(silent, comfy=silent),
        _context(fakes._Engine(), env="-e SPEECH_KIND=music"),
        _context(fakes._Engine(), comfy=fakes._Comfy([("9", fakes._png())], object_info=500)),
        _context(fakes._Engine(), workflows={}),
    ]
    reported = [r for ctx in contexts for r in test_type.run(ctx)]
    assert reported, f"{test_type.name} reported nothing under any fake"
    for r in reported:
        row = ec.result_name(r["name"])
        assert row in test_type.results, \
            f"{test_type.name} reported {r['name']!r}, which is not one of the names it declares"
        if "kind" in r:
            assert r["kind"] == row.kind, (r, row)
        if r["state"] == "passed":
            assert row.passes, r


def test_every_external_name_literal_in_the_package_is_a_row():
    """A new `external/...` name written into a type must be one of its declared rows."""
    literals = set()
    for module in [checks, *MODULES]:
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and node.value.startswith("external/") and " " not in node.value
                    and not node.value.endswith("/")):
                literals.add(node.value)
    assert literals, "found no name literals: this test is reading the wrong thing"
    missing = sorted(n for n in literals if ec.result_name(n) is None)
    assert not missing, f"names in the checks package with no row in RESULT_NAMES: {missing}"


# ---------------------------------------------------------------- 3. derived tables

def test_the_result_name_table_is_the_launchers_rows_then_every_types():
    from_types = [row for t in ec.REGISTRY for row in t.results]
    own = [row for row in ec.RESULT_NAMES if row not in from_types]
    assert list(ec.RESULT_NAMES) == own + from_types
    unchecked = set(ec.KNOWN_CAPABILITIES) - set(ec.CHECKED_CAPABILITIES)
    unbuilt = set(ec.KNOWN_FEATURES) - {t.name for t in ec.REGISTRY if t.axis == checks.FEATURE}
    assert {row.shape for row in own} == ({ec.CAPABILITIES_RESULT}
                                          | {f"external/{c}" for c in unchecked | unbuilt})
    assert all(row.kind is None and not row.passes for row in own)


def test_the_capability_tables_agree_with_the_registry_and_each_other():
    known, checked, engine = (set(ec.KNOWN_CAPABILITIES), set(ec.CHECKED_CAPABILITIES),
                              set(ec._ENGINE_CAPABILITIES))
    assert engine <= checked <= known
    assert set(ec.ROUTE_CAPABILITIES.values()) <= known
    for table in (ec.KNOWN_CAPABILITIES, ec.CHECKED_CAPABILITIES, ec._ENGINE_CAPABILITIES):
        assert isinstance(table, tuple) and len(table) == len(set(table))
    for t in ec.REGISTRY:
        assert set(t.requires) <= known, f"{t.name} requires a capability nothing can declare"
    # A capability is checked exactly when declaring it alone selects a type that
    # requires it; an unchecked one selects nothing, so it can only be skipped.
    for cap in known:
        requiring = [t for t in ec.select_types({cap}) if cap in t.requires]
        assert bool(requiring) == (cap in checked), cap
        assert (cap in engine) == any(t.images == ec.OPENAI_ENGINE for t in requiring), cap
    assert ec.select_types(set()) == []


# ---------------------------------------------------------------- 4. the guard is last

def test_a_guard_is_selected_after_every_other_type():
    guards = [t for t in ec.REGISTRY if t.axis == checks.GUARD]
    assert guards, "no guard is registered: this test is reading the wrong thing"
    assert min(t.order for t in guards) > max(t.order for t in ec.REGISTRY if t.axis != checks.GUARD)
    for caps in ({"embeddings"}, {"embeddings", ec.COMFYUI_CAPABILITY},
                 set(ec.KNOWN_CAPABILITIES) - {"chat"}):
        selected = ec.select_types(caps)
        assert selected[-1].axis == checks.GUARD, [t.name for t in selected]
        assert [t.order for t in selected] == sorted(t.order for t in selected)
    assert all(t.axis != checks.GUARD for t in ec.select_types({"chat", "embeddings"}))
    assert all(t.axis != checks.GUARD for t in ec.select_types({ec.COMFYUI_CAPABILITY}))


# ---------------------------------------------------------------- 6. the feature axis

def test_a_feature_type_is_selected_only_when_named_and_its_route_is_declared():
    features = [t for t in ec.REGISTRY if t.axis == checks.FEATURE]
    assert features, "no feature type is registered: this test is reading the wrong thing"
    routes = [t for t in ec.REGISTRY if t.axis == checks.ROUTE and t.images == ec.OPENAI_ENGINE]
    assert not set(ec.KNOWN_FEATURES) & set(ec.KNOWN_CAPABILITIES), "a name must mean one thing"
    for t in features:
        assert t.name in ec.KNOWN_FEATURES and t.requires and t.images == ec.OPENAI_ENGINE
        assert ec.feature_requires(t.name) == tuple(t.requires)
        declared = set(t.requires)
        assert t not in ec.select_types(declared)
        assert t in ec.select_types(declared, {t.name})
        assert t not in ec.select_types({"embeddings"}, {t.name})
        # Naming it changes nothing else that is selected.
        assert [x for x in ec.select_types(declared, {t.name}) if x is not t] == ec.select_types(declared)
        # It runs after every route check on the engine API and before a guard.
        assert max(r.order for r in routes) < t.order < min(
            g.order for g in ec.REGISTRY if g.axis == checks.GUARD)


def test_a_feature_type_must_be_a_known_feature():
    with pytest.raises(ValueError):
        checks.TestType(name="smell", axis=checks.FEATURE, requires=("chat",),
                        images=checks.OPENAI_ENGINE, results=(), run=lambda ctx: [], order=1)


# ---------------------------------------------------------------- 5. the package beside it

def test_a_copy_of_the_launcher_reads_its_own_checks_package(tmp_path):
    """Lint L126 loads external_checks.py by path from the tree it lints. A copy must
    read the `checks` package beside it even while another one is already imported."""
    src = os.path.dirname(ec.__file__)
    for name in ("external_checks.py", "contract.py"):
        shutil.copy(os.path.join(src, name), tmp_path / name)
    shutil.copytree(os.path.join(src, "checks"), tmp_path / "checks",
                    ignore=shutil.ignore_patterns("__pycache__"))
    with open(tmp_path / "checks" / "chat.py", "a") as f:
        f.write("\nMARKED_COPY = True\n")
    spec = importlib.util.spec_from_file_location("_a_copy_of_external_checks",
                                                  tmp_path / "external_checks.py")
    copy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(copy)
    assert copy.checks is not checks and copy.checks.chat.MARKED_COPY
    assert not hasattr(checks.chat, "MARKED_COPY")
    assert [tuple(r) for r in copy.RESULT_NAMES] == [tuple(r) for r in ec.RESULT_NAMES]
