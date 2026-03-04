"""Pip/uv package installer."""

from __future__ import annotations

import logging
import shlex
import subprocess

from ..schema import PipPackages

log = logging.getLogger("provisioner")


def install_pip_packages(
    config: PipPackages,
    venv: str,
    dry_run: bool = False,
) -> None:
    """Install pip packages and requirements files.

    Supports both 'uv' and 'pip' as the install tool.
    Packages are installed first, then requirements files.
    """
    tool = config.tool or "uv"
    packages = config.packages or []
    requirements = config.requirements or []
    extra_args = config.args or ""

    if not packages and not requirements:
        log.info("No pip packages to install")
        return

    if dry_run:
        if packages:
            log.info("[DRY RUN] Would install pip packages (%s): %s", tool, ", ".join(packages))
        if requirements:
            log.info("[DRY RUN] Would install requirements: %s", ", ".join(requirements))
        return

    python_bin = f"{venv}/bin/python"

    # Install packages
    if packages:
        log.info("Installing %d pip packages with %s: %s", len(packages), tool, ", ".join(packages))

        if tool == "uv":
            cmd = ["uv", "pip", "install", "--python", python_bin]
        else:
            cmd = [python_bin, "-m", "pip", "install"]

        if extra_args:
            cmd.extend(shlex.split(extra_args))
        cmd.extend(packages)

        subprocess.run(cmd, check=True)
        log.info("Pip packages installed successfully")

    # Install from requirements files
    for req_file in requirements:
        log.info("Installing requirements from %s", req_file)

        if tool == "uv":
            cmd = ["uv", "pip", "install", "--python", python_bin, "-r", req_file]
        else:
            cmd = [python_bin, "-m", "pip", "install", "-r", req_file]

        if extra_args:
            cmd.extend(shlex.split(extra_args))

        subprocess.run(cmd, check=True)
        log.info("Requirements installed: %s", req_file)
