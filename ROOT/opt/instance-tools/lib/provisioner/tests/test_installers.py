"""Tests for provisioner.installers -- apt, pip, git (mocked subprocess)."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, call, patch

import pytest

from provisioner.installers.apt import install_apt_packages
from provisioner.installers.git import _clone_single, clone_git_repos
from provisioner.installers.pip import install_pip_packages
from provisioner.schema import GitRepo, PipPackages


# ---------- APT ----------

class TestInstallAptPackages:
    def test_empty_list(self):
        # Should not raise, no subprocess calls
        install_apt_packages([])

    def test_dry_run(self):
        install_apt_packages(["vim", "curl"], dry_run=True)

    @patch("provisioner.installers.apt.subprocess.run")
    def test_calls_apt_get(self, mock_run):
        install_apt_packages(["vim", "curl"])
        assert mock_run.call_count == 2
        # First call: apt-get update
        update_cmd = mock_run.call_args_list[0][0][0]
        assert update_cmd[:2] == ["apt-get", "update"]
        # Second call: apt-get install
        install_cmd = mock_run.call_args_list[1][0][0]
        assert "install" in install_cmd
        assert "vim" in install_cmd
        assert "curl" in install_cmd
        assert "--no-install-recommends" in install_cmd

    @patch("provisioner.installers.apt.subprocess.run", side_effect=Exception("apt fail"))
    def test_raises_on_failure(self, mock_run):
        with pytest.raises(Exception, match="apt fail"):
            install_apt_packages(["bad-pkg"])


# ---------- PIP ----------

class TestInstallPipPackages:
    def test_empty(self):
        install_pip_packages(PipPackages(), venv="/venv/main")

    def test_dry_run(self):
        install_pip_packages(
            PipPackages(packages=["torch"]),
            venv="/venv/main",
            dry_run=True,
        )

    @patch("provisioner.installers.pip.subprocess.run")
    def test_uv_install(self, mock_run):
        config = PipPackages(tool="uv", packages=["torch", "numpy"])
        install_pip_packages(config, venv="/venv/main")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "uv"
        assert "pip" in cmd
        assert "install" in cmd
        assert "--python" in cmd
        assert "/venv/main/bin/python" in cmd
        assert "torch" in cmd
        assert "numpy" in cmd

    @patch("provisioner.installers.pip.subprocess.run")
    def test_pip_install(self, mock_run):
        config = PipPackages(tool="pip", packages=["requests"])
        install_pip_packages(config, venv="/venv/main")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/venv/main/bin/python"
        assert "-m" in cmd
        assert "pip" in cmd
        assert "requests" in cmd

    @patch("provisioner.installers.pip.subprocess.run")
    def test_extra_args(self, mock_run):
        config = PipPackages(
            packages=["torch"],
            args="--extra-index-url https://example.com",
        )
        install_pip_packages(config, venv="/venv/main")
        cmd = mock_run.call_args[0][0]
        assert "--extra-index-url" in cmd
        assert "https://example.com" in cmd

    @patch("provisioner.installers.pip.subprocess.run")
    def test_requirements_file(self, mock_run):
        config = PipPackages(requirements=["/workspace/req.txt"])
        install_pip_packages(config, venv="/venv/main")
        cmd = mock_run.call_args[0][0]
        assert "-r" in cmd
        assert "/workspace/req.txt" in cmd

    @patch("provisioner.installers.pip.subprocess.run")
    def test_packages_then_requirements(self, mock_run):
        """Packages are installed before requirements files."""
        config = PipPackages(
            packages=["torch"],
            requirements=["/req.txt"],
        )
        install_pip_packages(config, venv="/venv/main")
        assert mock_run.call_count == 2
        # First call installs packages
        first_cmd = mock_run.call_args_list[0][0][0]
        assert "torch" in first_cmd
        # Second call installs requirements
        second_cmd = mock_run.call_args_list[1][0][0]
        assert "-r" in second_cmd


# ---------- GIT ----------

class TestCloneSingle:
    def test_dry_run(self):
        repo = GitRepo(url="https://github.com/a/b", dest="/tmp/b")
        _clone_single(repo, venv="/venv/main", dry_run=True)

    @patch("provisioner.installers.git.subprocess.run")
    @patch("provisioner.installers.git.os.path.isdir", return_value=False)
    def test_clone_basic(self, mock_isdir, mock_run):
        repo = GitRepo(
            url="https://github.com/a/b",
            dest="/workspace/b",
            recursive=True,
        )
        _clone_single(repo, venv="/venv/main")
        cmd = mock_run.call_args_list[0][0][0]
        assert cmd[0] == "git"
        assert "clone" in cmd
        assert "--recursive" in cmd
        assert "https://github.com/a/b" in cmd
        assert "/workspace/b" in cmd

    @patch("provisioner.installers.git.subprocess.run")
    @patch("provisioner.installers.git.os.path.isdir", return_value=False)
    def test_clone_without_recursive(self, mock_isdir, mock_run):
        repo = GitRepo(
            url="https://github.com/a/b",
            dest="/workspace/b",
            recursive=False,
        )
        _clone_single(repo, venv="/venv/main")
        cmd = mock_run.call_args_list[0][0][0]
        assert "--recursive" not in cmd

    @patch("provisioner.installers.git.subprocess.run")
    @patch("provisioner.installers.git.os.path.isdir", return_value=False)
    def test_clone_with_ref(self, mock_isdir, mock_run):
        repo = GitRepo(
            url="https://github.com/a/b",
            dest="/workspace/b",
            ref="v2.0",
        )
        _clone_single(repo, venv="/venv/main")
        assert mock_run.call_count == 2
        checkout_cmd = mock_run.call_args_list[1][0][0]
        assert "checkout" in checkout_cmd
        assert "v2.0" in checkout_cmd

    @patch("provisioner.installers.git.subprocess.run")
    @patch("provisioner.installers.git.os.path.isdir", return_value=True)
    def test_skips_existing_repo(self, mock_isdir, mock_run):
        repo = GitRepo(
            url="https://github.com/a/b",
            dest="/workspace/b",
            pull_if_exists=False,
        )
        _clone_single(repo, venv="/venv/main")
        mock_run.assert_not_called()

    @patch("provisioner.installers.git.subprocess.run")
    @patch("provisioner.installers.git.os.path.isdir", return_value=True)
    def test_pulls_existing_repo(self, mock_isdir, mock_run):
        repo = GitRepo(
            url="https://github.com/a/b",
            dest="/workspace/b",
            pull_if_exists=True,
        )
        _clone_single(repo, venv="/venv/main")
        cmd = mock_run.call_args[0][0]
        assert "pull" in cmd

    @patch("provisioner.installers.pip.install_pip_packages")
    @patch("provisioner.installers.git.subprocess.run")
    @patch("provisioner.installers.git.os.path.isdir", return_value=False)
    @patch("provisioner.installers.git.os.path.isfile", return_value=True)
    def test_installs_requirements(self, mock_isfile, mock_isdir, mock_run, mock_pip):
        repo = GitRepo(
            url="https://github.com/a/b",
            dest="/workspace/b",
            requirements="requirements.txt",
        )
        _clone_single(repo, venv="/venv/main")
        mock_pip.assert_called_once()
        config = mock_pip.call_args[0][0]
        assert "/workspace/b/requirements.txt" in config.requirements

    @patch("provisioner.installers.git.subprocess.run")
    @patch("provisioner.installers.git.os.path.isdir", return_value=False)
    def test_editable_install(self, mock_isdir, mock_run):
        repo = GitRepo(
            url="https://github.com/a/b",
            dest="/workspace/b",
            pip_install_editable=True,
        )
        _clone_single(repo, venv="/venv/main")
        # Last call should be uv pip install -e
        last_cmd = mock_run.call_args_list[-1][0][0]
        assert "-e" in last_cmd
        assert "/workspace/b" in last_cmd


class TestCloneGitRepos:
    def test_empty_list(self):
        clone_git_repos([], venv="/venv/main")

    def test_dry_run(self):
        repos = [GitRepo(url="https://github.com/a/b", dest="/d")]
        clone_git_repos(repos, venv="/venv/main", dry_run=True)

    @patch("provisioner.installers.git._clone_single")
    def test_parallel_execution(self, mock_clone):
        repos = [
            GitRepo(url="https://github.com/a/b", dest="/d/a"),
            GitRepo(url="https://github.com/c/d", dest="/d/c"),
        ]
        clone_git_repos(repos, venv="/venv/main")
        assert mock_clone.call_count == 2

    @patch("provisioner.installers.git._clone_single", side_effect=RuntimeError("fail"))
    def test_raises_on_failure(self, mock_clone):
        repos = [GitRepo(url="https://github.com/a/b", dest="/d")]
        with pytest.raises(RuntimeError, match="failed"):
            clone_git_repos(repos, venv="/venv/main")
