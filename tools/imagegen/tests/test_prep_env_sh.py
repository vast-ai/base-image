"""Tests for the launch-env dump in `etc/vast_boot.d/10-prep-env.sh` (ADR 0052).

At first boot the hook writes the launch environment to /etc/environment, and the
boot shell, every login shell and every supervisor service source that file. A
value must come back exactly as Docker passed it: `;`, `$`, backticks, quotes and
backslashes are literal, no variable reference is expanded, nothing in a value
runs, and every variable stays on one line (12-cpu-thread-limits.sh finds a user
variable by matching `^NAME=`).

The function is sourced from the shipped file, so the text under test is the text
that ships.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
HOOK = REPO / "ROOT/etc/vast_boot.d/10-prep-env.sh"

VALUES = {
    "BASE": "/base",
    # Generated secrets: a `$` followed by a letter must not be read as a reference.
    "SECRET_LETTER": "p4$sW0rd",
    "SECRET_UNDERSCORE": "Xk9$_aZ",
    "SECRET_DIGIT": "abc$1xyz",
    "REF": "$BASE/x",
    "BRACED": "${BASE}y",
    "SELF": "/x:$SELF",
    "ESCAPED": "\\$BASE",
    "SEMI": "Server=db;Database=app;User Id=x",
    "SUBST": "$(touch {canary})",
    "BACKTICK": "`touch {canary}`",
    "NESTED": "${BASE:-$(touch {canary})}",
    "PROMPT": "${SUBST@P}",
    "ARITH": "$((1+1))",
    "SPECIAL_PARAMS": "$1 $$ $? $# $@ $* $! $- $0",
    "LONE_DOLLAR": "$",
    "DQUOTE": 'say "hi there"',
    "SQUOTE": "it's",
    "SQUOTES": "''a'b''",
    "BACKSLASH": "a\\b\\",
    "NEWLINE": "line1\nFAKE=injected\n$BASE",
    "TAB": "a\t$BASE",
    "TRAILING_NL": "x\n",
    "UNICODE": "café ☕",
    "EMPTY": "",
    "SPACES": "  padded  ",
    "EQUALS": "a=b=c",
    "GLOB": "*",
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
    """Source `text` with `set -a` in a bash whose environment is `env` and read the
    named variables back."""
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
    return {k: v.replace("{canary}", str(canary)) for k, v in VALUES.items()}


@pytest.mark.parametrize("shell_env", ["boot", "fresh"])
def test_values_round_trip_literally(
    launch: dict[str, str], shell_env: str, tmp_path: Path
) -> None:
    """Holds in the boot shell (whose environment is the launch env) and in a fresh
    login shell (which starts without it)."""
    env = launch if shell_env == "boot" else {}
    assert _source(_dump(launch), list(launch), env, tmp_path) == launch


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
    assert out == "OK='1'\n"
    _source(out, ["OK"], {}, tmp_path)
    assert not canary.exists()


def test_excluded_variables_are_not_dumped() -> None:
    out = _dump({"HOME": "/root", "SHLVL": "1", "CONDA_PREFIX": "/c", "KEEP": "1"})
    assert out == "KEEP='1'\n"
