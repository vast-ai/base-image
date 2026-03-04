"""Conda package installer.

Uses the Miniforge3 installation at /opt/miniforge3/ which provides
both mamba and conda. This matches the base image setup where
/venv/main is a conda prefix environment created with:
    conda create -p /venv/main python=X.Y -y
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess

from ..schema import CondaPackages

log = logging.getLogger("provisioner")

# Miniforge3 install path in the base image
_MINIFORGE_BIN = "/opt/miniforge3/bin"


def _get_conda_tool() -> str:
    """Return the path to mamba (preferred) or conda.

    Checks the Miniforge3 install path first, then falls back to PATH.
    Raises RuntimeError if neither is found.
    """
    # Check the known Miniforge3 location first
    mamba_path = os.path.join(_MINIFORGE_BIN, "mamba")
    if os.path.isfile(mamba_path) and os.access(mamba_path, os.X_OK):
        return mamba_path

    conda_path = os.path.join(_MINIFORGE_BIN, "conda")
    if os.path.isfile(conda_path) and os.access(conda_path, os.X_OK):
        return conda_path

    # Fall back to PATH
    for name in ("mamba", "conda"):
        path = shutil.which(name)
        if path:
            return path

    raise RuntimeError(
        f"Neither mamba nor conda found at {_MINIFORGE_BIN}/ or in PATH"
    )


def ensure_conda_env(env_path: str, python_version: str = "") -> None:
    """Ensure a conda environment exists at env_path, creating it if necessary.

    Uses the same pattern as the Dockerfile:
        conda create -p <path> python=X.Y -y
    """
    # conda-meta directory is the marker for a conda prefix env
    if os.path.isdir(os.path.join(env_path, "conda-meta")):
        log.debug("Conda env already exists: %s", env_path)
        return

    tool = _get_conda_tool()
    log.info("Creating conda env: %s (python=%s)", env_path, python_version or "default")

    cmd = [tool, "create", "-y", "-p", env_path]
    if python_version:
        cmd.append(f"python={python_version}")

    subprocess.run(cmd, check=True)
    log.info("Conda env created: %s", env_path)


def install_conda_packages(
    config: CondaPackages,
    dry_run: bool = False,
) -> None:
    """Install conda packages into a conda prefix environment.

    Uses mamba if available (Miniforge3 includes it), otherwise conda.
    Packages can include version specifiers (e.g. "numpy=1.24", "scipy>=1.10").

    If config.env is set, installs into that prefix environment (creating it
    if it doesn't exist). Otherwise installs into the base/active environment.
    """
    packages = config.packages or []
    channels = config.channels or []
    extra_args = config.args or ""
    env_path = config.env or ""

    if not packages:
        log.info("No conda packages to install")
        return

    target_label = env_path if env_path else "base environment"

    if dry_run:
        log.info("[DRY RUN] Would install conda packages in %s: %s",
                 target_label, ", ".join(packages))
        if channels:
            log.info("[DRY RUN] Channels: %s", ", ".join(channels))
        return

    tool = _get_conda_tool()

    # Auto-create environment if a path is specified
    if env_path:
        ensure_conda_env(env_path, config.python)

    log.info("Installing %d conda packages with %s in %s: %s",
             len(packages), os.path.basename(tool), target_label, ", ".join(packages))

    cmd = [tool, "install", "-y"]

    if env_path:
        cmd.extend(["-p", env_path])

    for channel in channels:
        cmd.extend(["-c", channel])

    if extra_args:
        cmd.extend(shlex.split(extra_args))

    cmd.extend(packages)

    subprocess.run(cmd, check=True)
    log.info("Conda packages installed successfully")
