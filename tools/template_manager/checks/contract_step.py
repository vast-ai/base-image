"""The OpenAI contract (contract.py) for an engine image, keyed on the declared routes."""

from __future__ import annotations

import io

from . import (CONTRACT, ROUTE, STRUCTURAL, ResultName, TestType, _attach_deadline,
               _env_with_overrides, contract)

# The name a contract run is reported under when it raises or the deadline leaves it
# no time; its own results are `contract/<check>`.
CONTRACT_STEP = "contract"


class _LazySession:
    """Opens a remote.RemoteSession only if a check actually needs the box, and
    closes it (detaching and deleting the ephemeral key) as soon as the contract is
    done — the key exists for those seconds only. The run's deadline, when given, is
    handed to the session so SSH cannot run past it."""

    def __init__(self, factory, deadline=None):
        self.factory, self.session, self.deadline = factory, None, deadline

    def run(self, script, python="python3"):
        if self.session is None:
            session = self.factory()
            if self.deadline is not None:
                _attach_deadline(session, self.deadline)
            self.session = session.__enter__()
        return self.session.run(script, python=python)

    def close(self):
        if self.session is not None:
            self.session.__exit__(None, None, None)
            self.session = None


# Report findings -> result states. A declared deviation is reported, never blocking
# (ADR 0031 decision 6a), so it gets its own state rather than passing or failing.
def check_contract(host, port, token, image, env, env_overrides, routes,
                   client_factory=None, remote_factory=None, deadline=None):
    """The OpenAI contract (contract.py, ported from the in-image checkers) for an
    engine image, keyed on the declared routes. Results are `contract/<check>`.
    `deadline` (contract.Deadline) caps every contract request's timeout and the SSH
    session's connect retries and remote script."""
    row = contract.set_engine(image)
    if row is None:
        return []
    out = io.StringIO()
    rep = contract.Report(out)
    cli = _attach_deadline((client_factory or contract.Client)(f"{host}:{port}", token), deadline)
    lazy = _LazySession(remote_factory, deadline) if remote_factory else None
    try:
        _run_contract(rep, cli, row, env, env_overrides, routes,
                      contract.remote_counter(lazy) if lazy else None)
    finally:
        if lazy:
            lazy.close()
    results = [{"name": f"contract/{c}", "state": "passed", "kind": "structural", "detail": ""}
               for c in rep.passed]
    for bucket, state in ((rep.violations, "failed"), (rep.deviated, "deviation"),
                          (rep.advisories, "advisory"), (rep.errors, "error"),
                          (rep.na, "skipped")):
        results += [{"name": f"contract/{c}", "state": state, "kind": "structural", "detail": d}
                    for c, d in bucket]
    return results


def _run_contract(rep, cli, row, env, env_overrides, routes, counter):
    contract.run_contract(
        rep, cli,
        model=_env_with_overrides(env, env_overrides, row["model_env"]) or "",
        engine_args=_env_with_overrides(env, env_overrides, row["args_env"]) or "",
        expect_caps=_env_with_overrides(env, env_overrides, row["caps_env"]) or "",
        routes=set(routes), counter=counter)


def _run(ctx):
    return check_contract(
        ctx.host, ctx.engine_port, ctx.token, ctx.image, ctx.env, ctx.env_overrides,
        ctx.engine_capabilities, client_factory=ctx.contract_client_factory,
        remote_factory=ctx.remote_factory, deadline=ctx.deadline)


# Needs no particular route: it runs whenever the template declares an engine route,
# before the route checks, and reports nothing for an image without a contract.
TYPE = TestType(
    name="contract",
    axis=ROUTE,
    requires=(),
    images=CONTRACT,
    results=(ResultName(CONTRACT_STEP, None, CONTRACT, False),
             *(ResultName(f"contract/{check}", STRUCTURAL, CONTRACT, passes)
               for check, passes in contract.CHECKS.items())),
    unrun=(CONTRACT_STEP,),
    order=10,
    run=_run,
)
