"""Tests for the launch-env dump in `etc/vast_boot.d/10-prep-env.sh` (ADR 0052).

At first boot the hook writes the launch environment to /etc/environment, and the
boot shell, every login shell and every supervisor service source that file. A
value must come back as Docker passed it, with one documented exception: plain
references to other variables, `$OTHER` and `${OTHER}`, are expanded. Nothing in a
value runs, `\\$` gives a literal `$`, and every variable stays on one line
(12-cpu-thread-limits.sh finds a user variable by matching `^NAME=`).

The functions are sourced from the shipped file, so the text under test is the text
that ships.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
HOOK = REPO / "ROOT/etc/vast_boot.d/10-prep-env.sh"

# Values that must come back byte-for-byte.
LITERAL = {
    "SEMI": "Server=db;Database=app;User Id=x",
    "SUBST": "$(touch {canary})",
    "BACKTICK": "`touch {canary}`",
    "NESTED": "${BASE:-$(touch {canary})}",
    "PROMPT": "${PAYLOAD@P}",
    "ARITH": "$((1+1))",
    "SPECIAL_PARAMS": "$1 $$ $? $# $@ $* $! $- $0",
    "LONE_DOLLAR": "$",
    "TRAILING_DOLLAR": "a$",
    "EMPTY_BRACE": "${}",
    "UNCLOSED_BRACE": "${BASE",
    "DQUOTE": 'say "hi"',
    "SQUOTE": "it's",
    "BACKSLASH": "a\\b\\",
    "DOUBLE_BACKSLASH": "\\\\",
    "BACKSLASH_QUOTE": '\\"',
    "NEWLINE": "line1\nFAKE=injected\n$BASE",
    "TAB": "a\t$BASE",
    "TRAILING_NL": "x\n",
    "UNICODE": "café ☕",
    "EMPTY": "",
    "SPACES": "  padded  ",
    "EQUALS": "a=b=c",
    "GLOB": "*",
    "SELF": "/x:$SELF",
    "SELF_BRACED": "/x:${SELF_BRACED}",
}

# Values with references to other variables, and what sourcing must produce.
EXPANDED = {
    "BASE": ("/base", "/base"),
    "PAYLOAD": ("$(touch {canary})", "$(touch {canary})"),
    "REF": ("$BASE/x", "/base/x"),
    "BRACED": ("${BASE}y", "/basey"),
    "MISSING": ("$NOPE/x", "/x"),
    "ESCAPED": ("\\$BASE", "$BASE"),
    "MIXED": ('$BASE;$(touch {canary});"q";`x`', '/base;$(touch {canary});"q";`x`'),
}


def _dump(env: dict[str, str]) -> str:
    """Run the shipped `_vast_dump_env` with `env` and return its output, minus the
    PWD and `_` lines bash itself adds to every child environment."""
    script = f'_VAST_PREP_ENV_LIB_ONLY=1 . "{HOOK}"; unset _VAST_PREP_ENV_LIB_ONLY; _vast_dump_env'
    out = subprocess.run(
        ["/bin/bash", "-c", script], env=env, check=True, capture_output=True
    ).stdout.decode()
    return "".join(
        line for line in out.splitlines(keepends=True) if not line.startswith(("PWD=", "_="))
    )


def _source(text: str, names: list[str], env: dict[str, str], tmp_path: Path) -> dict[str, str]:
    """Source `text` with `set -a` the way the boot shell does (its environment is
    the launch env) and read the named variables back."""
    envfile = tmp_path / "environment"
    envfile.write_text(text)
    script = f'set -a; . "{envfile}"; set +a; for n in "$@"; do printf "%s\\0" "${{!n-<unset>}}"; done'
    out = subprocess.run(
        ["/bin/bash", "-c", script, "_", *names], env=env, check=True, capture_output=True
    ).stdout.decode()
    return dict(zip(names, out.split("\0")[:-1]))


@pytest.fixture
def canary(tmp_path: Path) -> Path:
    return tmp_path / "canary"


@pytest.fixture
def launch(canary: Path) -> dict[str, str]:
    env = {k: v.replace("{canary}", str(canary)) for k, v in LITERAL.items()}
    env.update({k: v.replace("{canary}", str(canary)) for k, (v, _) in EXPANDED.items()})
    return env


def test_literal_values_round_trip(launch: dict[str, str], tmp_path: Path) -> None:
    got = _source(_dump(launch), list(LITERAL), launch, tmp_path)
    assert got == {k: launch[k] for k in LITERAL}


def test_plain_references_expand(launch: dict[str, str], canary: Path, tmp_path: Path) -> None:
    got = _source(_dump(launch), list(EXPANDED), launch, tmp_path)
    assert got == {k: want.replace("{canary}", str(canary)) for k, (_, want) in EXPANDED.items()}


def test_references_resolve_in_a_fresh_shell(launch: dict[str, str], tmp_path: Path) -> None:
    """A login shell sources the file without the launch env; a reference then
    resolves against lines above it or the shell's own environment."""
    text = "BASE='/base'\n" + _dump({"REF": "$BASE/x"})
    assert _source(text, ["REF"], {}, tmp_path) == {"REF": "/base/x"}


def test_nothing_in_a_value_runs(launch: dict[str, str], canary: Path, tmp_path: Path) -> None:
    _source(_dump(launch), list(launch), launch, tmp_path)
    _source(_dump(launch), list(launch), {}, tmp_path)
    assert not canary.exists()


def test_one_line_per_variable(launch: dict[str, str]) -> None:
    lines = _dump(launch).splitlines()
    assert sorted(line.split("=", 1)[0] for line in lines) == sorted(launch)
    assert not any(line.startswith("FAKE=") for line in lines)


def test_non_identifier_names_are_skipped(canary: Path, tmp_path: Path) -> None:
    env = {"OK": "1", f"$(touch {canary})": "x", "A.B": "y", "1X": "z"}
    out = _dump(env)
    assert out == 'OK="1"\n'
    _source(out, ["OK"], {}, tmp_path)
    assert not canary.exists()


def test_excluded_variables_are_not_dumped() -> None:
    out = _dump({"HOME": "/root", "SHLVL": "1", "CONDA_PREFIX": "/c", "KEEP": "1"})
    assert out == 'KEEP="1"\n'


def test_a_large_value_is_quoted_quickly(tmp_path: Path) -> None:
    """The platform allows env strings of up to 32 KB; boot must not stall on one."""
    value = '$BASE;"`\\x' * 3000
    start = time.monotonic()
    out = _dump({"BASE": "b", "BIG": value})
    assert time.monotonic() - start < 10
    assert _source(out, ["BIG"], {"BASE": "b"}, tmp_path)["BIG"] == value.replace("$BASE", "b")
