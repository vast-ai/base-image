"""The launcher's client-side test types, one module each (ADR 0068, "Test types").

A test type is one unit: the module declares, in a single `TestType` value, the
type's name, what selects it, the images it can be reported for, every result name it
can report, its fixtures, where it runs in the order, and the code that runs it.
`REGISTRY` lists the types explicitly; a module in this package that is not listed
fails the launcher's tests. external_checks.py derives its capability and result-name
tables from `REGISTRY` and runs the selected types in order.

This file holds what the types share: the `TestType` and `Context` shapes, the
result-name row, and the helpers that build the common result rows.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Callable, NamedTuple

try:                                    # a sibling of this package; loaded by path when needed
    import contract
except ImportError:                     # pragma: no cover - imported by path from elsewhere
    import importlib.util as _ilu
    _cspec = _ilu.spec_from_file_location(
        "contract", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "contract.py"))
    contract = _ilu.module_from_spec(_cspec)
    _cspec.loader.exec_module(contract)


# ---------------------------------------------------- the names a type can report

STRUCTURAL, ANSWER = "structural", "answer"
# Which images a name can be reported for: any image; an image with an OpenAI engine
# (external_checks.OPENAI_ENGINE_IMAGES), the only images whose routes the route checks
# exercise; an image with a contract (contract.ENGINES); a ComfyUI image.
ANY_IMAGE, OPENAI_ENGINE, CONTRACT, COMFYUI = "any image", "openai-engine", "contract", "comfyui"


class ResultName(NamedTuple):
    """One name the launcher can report. `shape` is the name itself, or its form with
    <placeholders> for the parts a run fills in (a workflow, an output node). `kind`
    is STRUCTURAL or ANSWER, or None for a row that is never a check. `passes` says
    whether the name is ever reported `passed`."""
    shape: str
    kind: str | None
    images: str
    passes: bool


# ---------------------------------------------------------------- the unit

# What selects a type. ROUTE: every capability in `requires` is declared. GUARD: an
# engine route is declared and none of `requires` is (it probes what must be refused).
# FEATURE: a property of the model behind the chat route, named by the caller at run
# time (--features); it also needs every capability in `requires`.
ROUTE, FEATURE, GUARD = "route", "feature", "guard"
AXES = (ROUTE, FEATURE, GUARD)

# Every feature a caller can name. One with no registered type yet is reported as
# skipped, never passed. All of them are properties of the chat model, so a feature
# without a type still needs FEATURE_ROUTE declared.
FEATURES = ("vision", "multi-image", "video-input", "reasoning", "tools")
FEATURE_ROUTE = "chat"


@dataclass
class Context:
    """What a type's `run` is given: the template under test, and the endpoints the
    launcher opened for it. The engine fields are set for types on the engine API, the
    ComfyUI fields for ComfyUI types."""
    template: dict | None
    env: str
    env_overrides: tuple
    host: str
    token: str | None
    capabilities: set
    engine_capabilities: set
    deadline: object                    # contract.Deadline: one budget for the whole phase
    contract_client_factory: Callable | None = None
    remote_factory: Callable | None = None
    engine_port: object = None
    endpoint: object = None             # the engine API through its public port
    wrapper: object = None              # the ComfyUI API wrapper
    jupyter: object = None              # read-only file access (external_checks.Jupyter)
    comfy: object = None                # ComfyUI itself, or None when its port is not mapped
    generate_timeout: int | None = None

    @property
    def image(self):
        return str((self.template or {}).get("image") or "")

    def env_value(self, name):
        """A template variable, with a later --env override winning."""
        return _env_with_overrides(self.env, self.env_overrides, name)


@dataclass(frozen=True)
class TestType:
    """One test type.

    `requires` names capabilities (see ROUTE, FEATURE and GUARD). A feature type's
    `name` is the feature it checks, one of FEATURES. `images` is the scope its
    results are reported for (ANY_IMAGE, OPENAI_ENGINE, CONTRACT, COMFYUI). `results`
    is every name `run` can report. `run(ctx)` returns the result dicts. `order`
    places the type in the run; a guard runs after everything else.

    `unrun` is the names reported as `error` when the deadline leaves the type no
    time, the first of them also naming the one error a `run` that raises becomes. It
    is a tuple, or a function of the context returning one; an empty tuple means the
    type is not a check under that context (it only reports a skip) and always runs.
    """
    __test__ = False                    # not a pytest class, whatever its name starts with

    name: str
    axis: str
    requires: tuple
    images: str
    results: tuple
    run: Callable
    order: int
    fixtures: tuple = ()
    unrun: object = ()

    def __post_init__(self):
        if self.axis not in AXES:
            raise ValueError(f"test type {self.name}: unknown axis {self.axis!r}")
        if self.axis == FEATURE and self.name not in FEATURES:
            raise ValueError(f"test type {self.name}: not a feature in FEATURES")

    def names_if_not_run(self, ctx):
        return tuple(self.unrun(ctx) if callable(self.unrun) else self.unrun)


# ---------------------------------------------------------------- shared helpers

def _env_value(env_str, name):
    """Value of `-e NAME=...` in a template env string (quoted or bare)."""
    m = re.search(rf'(?:^|\s)-e\s+{re.escape(name)}=("([^"]*)"|\'([^\']*)\'|(\S+))', env_str or "")
    if not m:
        return None
    return next(g for g in m.groups()[1:] if g is not None)


def _env_with_overrides(env, env_overrides, name):
    """`-e NAME=...` from the template env, with a later --env NAME=... winning."""
    value = _env_value(env, name)
    for kv in env_overrides or ():
        k, _, v = kv.partition("=")
        if k == name:
            value = v
    return value


def _deadline_result(name, deadline):
    """A check the overall deadline left no time for: not run, so `error`."""
    return {"name": name, "state": "error",
            "detail": f"{contract.DEADLINE_EXCEEDED}: the external checks' overall deadline "
                      f"({deadline.seconds:g}s) passed before this check ran"}


def _guarded(name, step):
    """step()'s results; an unexpected exception becomes ONE `error` result named
    `name`, so one broken check never costs the others their result. Only Exception
    is caught: KeyboardInterrupt and SystemExit still stop the run."""
    try:
        return step()
    except Exception as e:                                        # noqa: BLE001
        return [{"name": name, "state": "error",
                 "detail": f"the check raised {type(e).__name__}: {str(e)[:300]}"}]


def _attach_deadline(client, deadline):
    """Give a transport the run's deadline. Test fakes without one are left alone."""
    if hasattr(client, "deadline"):
        client.deadline = deadline
    return client


def _no_response(name, what, body, **extra):
    """The result for a request that got no HTTP response at all (status 0:
    unreachable, timed out, the deadline passed). The check could not be run, so it
    is `error` — never a pass, and never a failure of the template."""
    return {"name": name, "state": "error", **extra,
            "detail": f"{what}: no HTTP response ({str(body)[:200]})"}


def _no_response_pair(structural, answer, what, body):
    """_no_response for a structural check and the answer check that depends on it."""
    return [_no_response(structural, what, body, kind="structural"),
            {"name": answer, "state": "skipped", "kind": "answer",
             "detail": "structural check could not run"}]


# ---------------------------------------------------------------- the registry

from . import (chat, comfyui, completions, contract_step, embeddings, speech,  # noqa: E402
               transcriptions, undeclared_chat, vision)

# Every registered type. The order here is the order of the launcher's result-name
# table; the order types RUN in is each type's `order`.
REGISTRY = (
    chat.TYPE,
    completions.TYPE,
    embeddings.TYPE,
    transcriptions.TYPE,
    speech.TYPE,
    vision.TYPE,
    undeclared_chat.TYPE,
    contract_step.TYPE,
    comfyui.TYPE,
)
