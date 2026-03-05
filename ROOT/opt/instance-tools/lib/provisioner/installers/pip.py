"""Pip/uv package installer."""

from __future__ import annotations

import logging
import os
import shlex
import shutil

from ..schema import PipPackages
from ..subprocess_runner import run_cmd

log = logging.getLogger("provisioner")


def ensure_venv(venv_path: str, python_version: str = "") -> None:
    """Ensure a venv exists at venv_path, creating it if necessary.

    Uses `uv venv --python X.Y <path>` if uv is available,
    otherwise falls back to `python -m venv`.
    """
    if os.path.isdir(venv_path) and os.path.isfile(os.path.join(venv_path, "bin", "python")):
        log.debug("Venv already exists: %s", venv_path)
        return

    log.info("Creating venv: %s (python=%s)", venv_path, python_version or "default")

    if shutil.which("uv"):
        cmd = ["uv", "venv"]
        if python_version:
            cmd.extend(["--python", python_version])
        cmd.append(venv_path)
    else:
        python_bin = f"python{python_version}" if python_version else "python3"
        cmd = [python_bin, "-m", "venv", venv_path]

    run_cmd(cmd, label="venv")
    log.info("Venv created: %s", venv_path)


def install_pip_packages(
    config: PipPackages,
    venv: str = "",
    default_venv: str = "",
    dry_run: bool = False,
) -> None:
    """Install pip packages and requirements files.

    Supports both 'uv' and 'pip' as the install tool.
    Packages are installed first, then requirements files.
    All installs use --no-cache-dir to save disk space.

    The venv is resolved as: config.venv > venv arg > default_venv.
    If the block specifies a python version, the venv is auto-created.
    venv="system" installs to system python with --break-system-packages.
    """
    # Resolve venv: block-level > explicit arg > default
    resolved_venv = config.venv or venv or default_venv
    if not resolved_venv:
        resolved_venv = "/venv/main"

    is_system = resolved_venv == "system"

    tool = config.tool or "uv"
    packages = config.packages or []
    requirements = config.requirements or []
    extra_args = config.args or ""

    if not packages and not requirements:
        log.info("No pip packages to install")
        return

    target_label = "system python" if is_system else resolved_venv

    if dry_run:
        if packages:
            log.info("[DRY RUN] Would install pip packages (%s) in %s: %s",
                     tool, target_label, ", ".join(packages))
        if requirements:
            log.info("[DRY RUN] Would install requirements in %s: %s",
                     target_label, ", ".join(requirements))
        return

    # Auto-create venv if needed (not for system python)
    if not is_system and (config.venv or config.python):
        ensure_venv(resolved_venv, config.python)

    # Resolve python binary
    if is_system:
        if config.python:
            python_bin = f"/usr/bin/python{config.python}"
        else:
            python_bin = shutil.which("python3") or "python3"
    else:
        python_bin = f"{resolved_venv}/bin/python"

    # Install packages
    if packages:
        log.info("Installing %d pip packages with %s in %s: %s",
                 len(packages), tool, target_label, ", ".join(packages))

        if tool == "uv":
            cmd = ["uv", "pip", "install", "--no-cache", "--python", python_bin]
            if is_system:
                cmd.append("--system")
                cmd.append("--break-system-packages")
        else:
            cmd = [python_bin, "-m", "pip", "install", "--no-cache-dir"]
            if is_system:
                cmd.append("--break-system-packages")

        if extra_args:
            cmd.extend(shlex.split(extra_args))
        cmd.extend(packages)

        run_cmd(cmd, label="pip")
        log.info("Pip packages installed successfully")

    # Install from requirements files
    for req_file in requirements:
        log.info("Installing requirements from %s", req_file)

        if tool == "uv":
            cmd = ["uv", "pip", "install", "--no-cache", "--python", python_bin, "-r", req_file]
            if is_system:
                cmd.append("--system")
                cmd.append("--break-system-packages")
        else:
            cmd = [python_bin, "-m", "pip", "install", "--no-cache-dir", "-r", req_file]
            if is_system:
                cmd.append("--break-system-packages")

        if extra_args:
            cmd.extend(shlex.split(extra_args))

        run_cmd(cmd, label="pip")
        log.info("Requirements installed: %s", req_file)
