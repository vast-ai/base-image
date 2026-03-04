"""Tests for provisioner.installers.conda -- conda/mamba package installer."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from provisioner.installers.conda import (
    _MINIFORGE_BIN,
    _get_conda_tool,
    ensure_conda_env,
    install_conda_packages,
)
from provisioner.schema import CondaPackages


# Helper: make os.path.isfile return True only for specific miniforge paths
def _isfile_mamba(path):
    return path == f"{_MINIFORGE_BIN}/mamba"


def _isfile_conda(path):
    return path == f"{_MINIFORGE_BIN}/conda"


def _isfile_none(path):
    return False


# ---------- _get_conda_tool ----------

class TestGetCondaTool:
    @patch("provisioner.installers.conda.os.access", return_value=True)
    @patch("provisioner.installers.conda.os.path.isfile", side_effect=_isfile_mamba)
    def test_prefers_miniforge_mamba(self, mock_isfile, mock_access):
        assert _get_conda_tool() == f"{_MINIFORGE_BIN}/mamba"

    @patch("provisioner.installers.conda.os.access", return_value=True)
    @patch("provisioner.installers.conda.os.path.isfile", side_effect=_isfile_conda)
    def test_falls_back_to_miniforge_conda(self, mock_isfile, mock_access):
        assert _get_conda_tool() == f"{_MINIFORGE_BIN}/conda"

    @patch("provisioner.installers.conda.shutil.which", side_effect=lambda x: "/usr/bin/mamba" if x == "mamba" else None)
    @patch("provisioner.installers.conda.os.access", return_value=False)
    @patch("provisioner.installers.conda.os.path.isfile", return_value=False)
    def test_falls_back_to_path_mamba(self, mock_isfile, mock_access, mock_which):
        assert _get_conda_tool() == "/usr/bin/mamba"

    @patch("provisioner.installers.conda.shutil.which", side_effect=lambda x: "/usr/bin/conda" if x == "conda" else None)
    @patch("provisioner.installers.conda.os.access", return_value=False)
    @patch("provisioner.installers.conda.os.path.isfile", return_value=False)
    def test_falls_back_to_path_conda(self, mock_isfile, mock_access, mock_which):
        assert _get_conda_tool() == "/usr/bin/conda"

    @patch("provisioner.installers.conda.shutil.which", return_value=None)
    @patch("provisioner.installers.conda.os.access", return_value=False)
    @patch("provisioner.installers.conda.os.path.isfile", return_value=False)
    def test_raises_when_neither_available(self, mock_isfile, mock_access, mock_which):
        with pytest.raises(RuntimeError, match="Neither mamba nor conda"):
            _get_conda_tool()


# ---------- install_conda_packages ----------

class TestInstallCondaPackages:
    def test_empty(self):
        install_conda_packages(CondaPackages())

    def test_dry_run(self):
        install_conda_packages(
            CondaPackages(packages=["numpy"]),
            dry_run=True,
        )

    def test_dry_run_with_channels(self, caplog):
        import logging
        caplog.set_level(logging.INFO)
        install_conda_packages(
            CondaPackages(packages=["pytorch"], channels=["pytorch", "nvidia"]),
            dry_run=True,
        )
        assert "pytorch" in caplog.text
        assert "nvidia" in caplog.text

    def test_dry_run_with_env(self, caplog):
        import logging
        caplog.set_level(logging.INFO)
        install_conda_packages(
            CondaPackages(packages=["numpy"], env="/envs/myenv"),
            dry_run=True,
        )
        assert "/envs/myenv" in caplog.text

    @patch("provisioner.installers.conda.subprocess.run")
    @patch("provisioner.installers.conda.os.access", return_value=True)
    @patch("provisioner.installers.conda.os.path.isfile", side_effect=_isfile_mamba)
    def test_prefers_mamba(self, mock_isfile, mock_access, mock_run):
        config = CondaPackages(packages=["numpy", "scipy"])
        install_conda_packages(config)
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == f"{_MINIFORGE_BIN}/mamba"
        assert "install" in cmd
        assert "-y" in cmd
        assert "numpy" in cmd
        assert "scipy" in cmd

    @patch("provisioner.installers.conda.subprocess.run")
    @patch("provisioner.installers.conda.shutil.which", side_effect=lambda x: "/usr/bin/conda" if x == "conda" else None)
    @patch("provisioner.installers.conda.os.access", return_value=False)
    @patch("provisioner.installers.conda.os.path.isfile", return_value=False)
    def test_falls_back_to_conda(self, mock_isfile, mock_access, mock_which, mock_run):
        config = CondaPackages(packages=["numpy"])
        install_conda_packages(config)
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/usr/bin/conda"

    @patch("provisioner.installers.conda.subprocess.run")
    @patch("provisioner.installers.conda.os.access", return_value=True)
    @patch("provisioner.installers.conda.os.path.isfile", side_effect=_isfile_conda)
    def test_channels(self, mock_isfile, mock_access, mock_run):
        config = CondaPackages(
            packages=["pytorch"],
            channels=["pytorch", "nvidia"],
        )
        install_conda_packages(config)
        cmd = mock_run.call_args[0][0]
        assert "-c" in cmd
        c_indices = [i for i, x in enumerate(cmd) if x == "-c"]
        assert len(c_indices) == 2
        assert cmd[c_indices[0] + 1] == "pytorch"
        assert cmd[c_indices[1] + 1] == "nvidia"

    @patch("provisioner.installers.conda.subprocess.run")
    @patch("provisioner.installers.conda.os.access", return_value=True)
    @patch("provisioner.installers.conda.os.path.isfile", side_effect=_isfile_conda)
    def test_extra_args(self, mock_isfile, mock_access, mock_run):
        config = CondaPackages(
            packages=["numpy"],
            args="--no-update-deps",
        )
        install_conda_packages(config)
        cmd = mock_run.call_args[0][0]
        assert "--no-update-deps" in cmd

    @patch("provisioner.installers.conda.subprocess.run")
    @patch("provisioner.installers.conda.os.access", return_value=True)
    @patch("provisioner.installers.conda.os.path.isfile", side_effect=_isfile_conda)
    def test_version_specifiers(self, mock_isfile, mock_access, mock_run):
        """Version specifiers are passed through as-is."""
        config = CondaPackages(
            packages=["numpy=1.24", "scipy>=1.10", "pandas<2.0"],
        )
        install_conda_packages(config)
        cmd = mock_run.call_args[0][0]
        assert "numpy=1.24" in cmd
        assert "scipy>=1.10" in cmd
        assert "pandas<2.0" in cmd

    @patch("provisioner.installers.conda.ensure_conda_env")
    @patch("provisioner.installers.conda.subprocess.run")
    @patch("provisioner.installers.conda.os.access", return_value=True)
    @patch("provisioner.installers.conda.os.path.isfile", side_effect=_isfile_conda)
    def test_env_prefix(self, mock_isfile, mock_access, mock_run, mock_ensure):
        """When env is set, -p is passed and env is auto-created."""
        config = CondaPackages(packages=["numpy"], env="/envs/myenv")
        install_conda_packages(config)
        mock_ensure.assert_called_once_with("/envs/myenv", "")
        cmd = mock_run.call_args[0][0]
        assert "-p" in cmd
        assert "/envs/myenv" in cmd

    @patch("provisioner.installers.conda.ensure_conda_env")
    @patch("provisioner.installers.conda.subprocess.run")
    @patch("provisioner.installers.conda.os.access", return_value=True)
    @patch("provisioner.installers.conda.os.path.isfile", side_effect=_isfile_conda)
    def test_env_with_python(self, mock_isfile, mock_access, mock_run, mock_ensure):
        """When env and python are set, env is created with that python version."""
        config = CondaPackages(packages=["numpy"], env="/envs/myenv", python="3.11")
        install_conda_packages(config)
        mock_ensure.assert_called_once_with("/envs/myenv", "3.11")

    @patch("provisioner.installers.conda.subprocess.run")
    @patch("provisioner.installers.conda.os.access", return_value=True)
    @patch("provisioner.installers.conda.os.path.isfile", side_effect=_isfile_conda)
    def test_no_prefix_without_env(self, mock_isfile, mock_access, mock_run):
        """Without env, no -p is passed (uses base/active env)."""
        config = CondaPackages(packages=["numpy"])
        install_conda_packages(config)
        cmd = mock_run.call_args[0][0]
        assert "-p" not in cmd


# ---------- ensure_conda_env ----------

class TestEnsureCondaEnv:
    @patch("provisioner.installers.conda.subprocess.run")
    @patch("provisioner.installers.conda.os.access", return_value=True)
    @patch("provisioner.installers.conda.os.path.isfile", side_effect=_isfile_conda)
    @patch("provisioner.installers.conda.os.path.isdir", return_value=False)
    def test_creates_env(self, mock_isdir, mock_isfile, mock_access, mock_run):
        ensure_conda_env("/envs/myenv")
        cmd = mock_run.call_args[0][0]
        assert "create" in cmd
        assert "-y" in cmd
        assert "-p" in cmd
        assert "/envs/myenv" in cmd

    @patch("provisioner.installers.conda.subprocess.run")
    @patch("provisioner.installers.conda.os.access", return_value=True)
    @patch("provisioner.installers.conda.os.path.isfile", side_effect=_isfile_mamba)
    @patch("provisioner.installers.conda.os.path.isdir", return_value=False)
    def test_creates_env_with_mamba(self, mock_isdir, mock_isfile, mock_access, mock_run):
        ensure_conda_env("/envs/myenv")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == f"{_MINIFORGE_BIN}/mamba"

    @patch("provisioner.installers.conda.subprocess.run")
    @patch("provisioner.installers.conda.os.access", return_value=True)
    @patch("provisioner.installers.conda.os.path.isfile", side_effect=_isfile_conda)
    @patch("provisioner.installers.conda.os.path.isdir", return_value=False)
    def test_creates_env_with_python(self, mock_isdir, mock_isfile, mock_access, mock_run):
        ensure_conda_env("/envs/myenv", python_version="3.11")
        cmd = mock_run.call_args[0][0]
        assert "python=3.11" in cmd

    @patch("provisioner.installers.conda.os.path.isdir", return_value=True)
    def test_skips_existing(self, mock_isdir):
        """Env with conda-meta/ directory is considered existing."""
        ensure_conda_env("/envs/existing")
        # isdir should have been called with the conda-meta path
        mock_isdir.assert_called_with("/envs/existing/conda-meta")

    @patch("provisioner.installers.conda.shutil.which", return_value=None)
    @patch("provisioner.installers.conda.os.access", return_value=False)
    @patch("provisioner.installers.conda.os.path.isfile", return_value=False)
    @patch("provisioner.installers.conda.os.path.isdir", return_value=False)
    def test_raises_when_no_tool(self, mock_isdir, mock_isfile, mock_access, mock_which):
        with pytest.raises(RuntimeError, match="Neither mamba nor conda"):
            ensure_conda_env("/envs/myenv")
