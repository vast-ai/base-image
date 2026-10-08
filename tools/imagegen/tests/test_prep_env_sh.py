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

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from _docker_gate import assert_docker_present_under_ci, requires_docker

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
    # A single quote with something bash expands: the only values written 'it'\\''s'.
    "SQUOTE_DOLLAR": "it's $BASE",
    "SQUOTE_SUBST": "a'$(touch {canary})'",
    "BACKSLASH": "a\\b\\",
    "NEWLINE": "line1\nFAKE=injected\n$BASE",
    "TAB": "a\t$BASE",
    "TRAILING_NL": "x\n",
    "UNICODE": "café ☕",
    "EMPTY": "",
    "SPACES": "  padded  ",
    "EQUALS": "a=b=c",
    "GLOB": "*",
    "HASH": "p@ss#word",
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


# --- Readers that are not bash ---------------------------------------------------- #
#
# pam_env (non-interactive SSH, sudo) and linux-desktop's export_env.sh read the same
# file. Neither expands anything: each strips one pair of surrounding quotes and keeps
# the rest. So a value both they and bash read the same way is one written 'value'
# (no single quote inside) or "value" (a single quote, but nothing bash expands inside
# double quotes). Only a single quote together with $ ` " \, or a control character,
# needs a form that only bash reads correctly.

def _readers_agree(value: str) -> bool:
    """Written as 'value' or "value", which pam_env and the desktop parser read as bash
    does. The rest (a single quote with $ ` " \\, or a control character) is written in
    a form only bash reads."""
    return not any(c < " " or c == "\x7f" for c in value) and not (
        "'" in value and any(c in value for c in '$`"\\'))


def _pam_env_read(line: str) -> tuple[str, str]:
    """pam_env's reading of a `NAME=value` line (Linux-PAM modules/pam_env/pam_env.c,
    _assemble_line and _parse_env_file, checked against libpam-modules 1.5.3): the line
    is cut at its first `#`, and a quote is stripped from each end only when the value
    starts with one. Nothing is unescaped or expanded."""
    name, raw = line.split("#", 1)[0].split("=", 1)
    if raw[:1] in ("'", '"'):
        raw = raw[1:]
        if raw[-1:] in ("'", '"'):
            raw = raw[:-1]
    return name, raw


def test_pam_env_reads_every_value_it_can(launch: dict[str, str]) -> None:
    got = dict(_pam_env_read(line) for line in _dump(launch).splitlines())
    agree = {k: v for k, v in launch.items() if _readers_agree(v) and "#" not in v}
    assert {k: got[k] for k in agree} == agree
    # The one regression the old format did not have: a lone quote reads back intact.
    assert got["SQUOTE"] == "it's"
    # pam_env cuts at #, whatever the quoting (so did the old format); documented.
    assert got["HASH"] == "p@ss"


DESKTOP_PARSER = REPO / "derivatives/linux-desktop/ROOT/opt/instance-tools/bin/export_env.sh"


def test_linux_desktop_parser_reads_every_value_it_can(launch: dict[str, str], tmp_path: Path) -> None:
    """The desktop's own parser of /etc/environment, run on the dumped file."""
    envfile = tmp_path / "environment"
    envfile.write_text(_dump(launch))
    parser = DESKTOP_PARSER.read_text().replace("/etc/environment", str(envfile))
    names = list(launch)
    script = parser + '\nfor n in "$@"; do printf "%s\\0" "${!n-<unset>}"; done'
    out = subprocess.run(["/bin/bash", "-c", script, "_", *names], env={},
                         check=True, capture_output=True).stdout.decode()
    got = dict(zip(names, out.split("\0")[:-1]))
    agree = {k: v for k, v in launch.items() if _readers_agree(v)}
    assert {k: got[k] for k in agree} == agree
    # The bash-only forms match none of its patterns: the variable is skipped, not mangled.
    assert {k for k, v in got.items() if k not in agree and v != "<unset>"} == set()


# --- Old bash --------------------------------------------------------------------- #

OLD_BASH_SCRIPT = r'''
grep() { cat; }   # busybox grep has no -z; the filter is not under test here
_VAST_PREP_ENV_LIB_ONLY=1 . /hook.sh
unset _VAST_PREP_ENV_LIB_ONLY
out=$(_vast_dump_env)
env -i bash -c "$out"$'\n''for n in "$@"; do printf "%s\0" "${!n}"; done' _ "$@"
'''


def test_docker_is_available_under_ci():
    """A SKIP IS NOT A PASS: the old-bash test below needs docker."""
    assert_docker_present_under_ci()


@requires_docker
@pytest.mark.parametrize("version", ["4.2", "3.2"])
def test_old_bash_writes_the_same_literal_file(version: str, canary: Path, tmp_path: Path) -> None:
    """Inside a double-quoted ${//}, bash 4.2 and older keep the backslashes of the
    replacement text, which reopened the quote: `a'; touch x; #` ran `touch x`."""
    pull = subprocess.run(["docker", "pull", "-q", f"bash:{version}"], capture_output=True)
    if pull.returncode != 0:
        pytest.skip(f"bash:{version} could not be pulled: {pull.stderr.decode().strip()}")
    values = {"Q": "it's", "INJECT": f"a'; touch /work/{canary.name}; #", "MIX": "x'$y`z\\",
              "CNTRL": f"a'\tb\nC=$(touch /work/{canary.name})"}
    args = ["docker", "run", "--rm", "-v", f"{HOOK}:/hook.sh:ro", "-v", f"{tmp_path}:/work"]
    for k, v in values.items():
        args += ["-e", f"{k}={v}"]
    proc = subprocess.run(args + [f"bash:{version}", "bash", "-c", OLD_BASH_SCRIPT, "_", *values],
                          capture_output=True)
    assert proc.returncode == 0, proc.stderr.decode()
    assert dict(zip(values, proc.stdout.decode().split("\0")[:-1])) == values
    assert not canary.exists()


def test_boot_never_honours_a_stage_lib_only_switch() -> None:
    """A stage that returns early on a `_VAST_*_LIB_ONLY` variable (so its tests can
    load its functions) would be skipped by a template that set it, because the boot
    shell inherits the launch environment. boot_default.sh unsets every one first."""
    stages = [s for s in REPO.glob("**/ROOT*/etc/vast_boot.d/*.sh") if "pcl" not in s.parts]
    switches = {m for s in stages for m in re.findall(r"\b(_VAST_\w+_LIB_ONLY)\b", s.read_text())}
    assert switches, "no stage defines a lib-only switch; drop this test"
    boot = (REPO / "ROOT/opt/instance-tools/bin/boot_default.sh").read_text()
    before_loop = boot[: boot.index("for script in /etc/vast_boot.d/")]
    unset = {n for names in re.findall(r"\bunset ([\w ]+)", before_loop) for n in names.split()}
    assert switches <= unset, f"not unset before the stage loop: {sorted(switches - unset)}"


def test_unexpanded_notice_names_variables_never_values() -> None:
    """The boot log flags values with $NAME text (no longer expanded), naming only the
    variable: values can be secrets. It covers what the dump writes, nothing else."""
    env = {"MODEL_DIR": "$WORKSPACE/models", "PASS": "p4$sW0rd", "PRICE": "cost $5",
           "HOME": "$HOME", "CONDA_X": "$Y"}
    script = f'_VAST_PREP_ENV_LIB_ONLY=1 . "{HOOK}"; unset _VAST_PREP_ENV_LIB_ONLY; _vast_note_unexpanded'
    out = subprocess.run(["/bin/bash", "-c", script], env=env, check=True,
                         capture_output=True).stdout.decode()
    assert sorted(line.split()[1] for line in out.splitlines()) == ["MODEL_DIR", "PASS"]
    assert "WORKSPACE" not in out and "sW0rd" not in out
