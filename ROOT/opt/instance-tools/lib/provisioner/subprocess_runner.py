"""Subprocess runner that streams output through the provisioner logger."""

from __future__ import annotations

import logging
import os
import subprocess

log = logging.getLogger("provisioner")

# Environment variables that leak the provisioner's own virtualenv into
# child processes.  Stripping them ensures that child scripts (especially
# legacy provisioning scripts that `. /venv/main/bin/activate`) start with
# a clean slate and their own venv activation takes effect.
_VENV_ENV_VARS = frozenset({
    "VIRTUAL_ENV",
    "CONDA_DEFAULT_ENV",
    "CONDA_PREFIX",
    "UV_PROJECT_ENVIRONMENT",
})


def _clean_env(env: dict[str, str] | None) -> dict[str, str]:
    """Return an environment dict with venv-related vars stripped.

    When *env* is None (the default), starts from ``os.environ``.
    """
    base = dict(env if env is not None else os.environ)
    for var in _VENV_ENV_VARS:
        base.pop(var, None)
    return base


def run_cmd(
    cmd: list[str] | str,
    label: str = "cmd",
    check: bool = True,
    cwd: str | None = None,
    shell: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run a command, streaming its output line-by-line through the logger.

    Merges stdout and stderr (most tools write progress to stderr) and logs
    each line as INFO with a ``[label]`` prefix.

    The provisioner's own virtualenv environment variables (VIRTUAL_ENV,
    CONDA_PREFIX, etc.) are stripped from the child environment so that
    child processes are not polluted by the provisioner's venv.

    Returns a :class:`subprocess.CompletedProcess` with the captured output.
    Raises :class:`subprocess.CalledProcessError` when *check* is True and
    the process exits non-zero (same semantics as ``subprocess.run``).
    """
    child_env = _clean_env(env)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=cwd,
        shell=shell,
        env=child_env,
    )

    lines: list[str] = []
    assert proc.stdout is not None
    for raw_line in proc.stdout:
        line = raw_line.rstrip("\n")
        lines.append(line)
        log.info("[%s] %s", label, line)

    proc.wait()
    output = "\n".join(lines)

    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, cmd, output=output,
        )

    return subprocess.CompletedProcess(cmd, proc.returncode, stdout=output, stderr="")
