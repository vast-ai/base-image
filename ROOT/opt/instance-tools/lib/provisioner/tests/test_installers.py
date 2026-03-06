"""Tests for provisioner.installers -- apt, pip, git (mocked subprocess)."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, call, patch

import pytest

from provisioner.installers.apt import install_apt_packages
from provisioner.installers.git import _clone_single, _run_post_commands, clone_git_repos
from provisioner.installers.pip import ensure_venv, install_pip_packages
from provisioner.schema import GitRepo, PipPackages


# ---------- APT ----------

class TestInstallAptPackages:
    def test_empty_list(self):
        # Should not raise, no subprocess calls
        install_apt_packages([])

    def test_dry_run(self):
        install_apt_packages(["vim", "curl"], dry_run=True)

    @patch("provisioner.installers.apt.run_cmd")
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

    @patch("provisioner.installers.apt.run_cmd", side_effect=Exception("apt fail"))
    def test_raises_on_failure(self, mock_run):
        with pytest.raises(Exception, match="apt fail"):
            install_apt_packages(["bad-pkg"])


# ---------- PIP ----------

class TestInstallPipPackages:
    def test_empty(self):
        install_pip_packages(PipPackages(), default_venv="/venv/main")

    def test_dry_run(self):
        install_pip_packages(
            PipPackages(packages=["torch"]),
            default_venv="/venv/main",
            dry_run=True,
        )

    @patch("provisioner.installers.pip.run_cmd")
    def test_uv_install(self, mock_run):
        config = PipPackages(tool="uv", packages=["torch", "numpy"])
        install_pip_packages(config, default_venv="/venv/main")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "uv"
        assert "pip" in cmd
        assert "install" in cmd
        assert "--no-cache" in cmd
        assert "--python" in cmd
        assert "/venv/main/bin/python" in cmd
        assert "torch" in cmd
        assert "numpy" in cmd

    @patch("provisioner.installers.pip.run_cmd")
    def test_pip_install(self, mock_run):
        config = PipPackages(tool="pip", packages=["requests"])
        install_pip_packages(config, default_venv="/venv/main")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/venv/main/bin/python"
        assert "-m" in cmd
        assert "pip" in cmd
        assert "--no-cache-dir" in cmd
        assert "requests" in cmd

    @patch("provisioner.installers.pip.run_cmd")
    def test_extra_args(self, mock_run):
        config = PipPackages(
            packages=["torch"],
            args="--extra-index-url https://example.com",
        )
        install_pip_packages(config, default_venv="/venv/main")
        cmd = mock_run.call_args[0][0]
        assert "--extra-index-url" in cmd
        assert "https://example.com" in cmd

    @patch("provisioner.installers.pip.run_cmd")
    def test_requirements_file(self, mock_run):
        config = PipPackages(requirements=["/workspace/req.txt"])
        install_pip_packages(config, default_venv="/venv/main")
        cmd = mock_run.call_args[0][0]
        assert "-r" in cmd
        assert "/workspace/req.txt" in cmd
        assert "--no-cache" in cmd

    @patch("provisioner.installers.pip.run_cmd")
    def test_packages_then_requirements(self, mock_run):
        """Packages are installed before requirements files."""
        config = PipPackages(
            packages=["torch"],
            requirements=["/req.txt"],
        )
        install_pip_packages(config, default_venv="/venv/main")
        assert mock_run.call_count == 2
        # First call installs packages
        first_cmd = mock_run.call_args_list[0][0][0]
        assert "torch" in first_cmd
        # Second call installs requirements
        second_cmd = mock_run.call_args_list[1][0][0]
        assert "-r" in second_cmd

    @patch("provisioner.installers.pip.run_cmd")
    def test_block_venv_overrides_default(self, mock_run):
        """Block-level venv takes priority over default_venv."""
        config = PipPackages(venv="/venv/custom", packages=["torch"])
        install_pip_packages(config, default_venv="/venv/main")
        cmd = mock_run.call_args[0][0]
        assert "/venv/custom/bin/python" in cmd

    @patch("provisioner.installers.pip.run_cmd")
    def test_legacy_venv_arg(self, mock_run):
        """The old 'venv' positional arg still works."""
        config = PipPackages(packages=["torch"])
        install_pip_packages(config, venv="/venv/legacy")
        cmd = mock_run.call_args[0][0]
        assert "/venv/legacy/bin/python" in cmd

    def test_dry_run_shows_venv(self, caplog):
        """Dry run output mentions the resolved venv."""
        import logging
        caplog.set_level(logging.INFO)
        config = PipPackages(venv="/venv/custom", packages=["torch"])
        install_pip_packages(config, default_venv="/venv/main", dry_run=True)
        assert "/venv/custom" in caplog.text

    @patch("provisioner.installers.pip.shutil.which", return_value="/usr/bin/python3")
    @patch("provisioner.installers.pip.run_cmd")
    def test_system_venv_uv(self, mock_run, mock_which):
        """venv='system' should use system python with --system and --break-system-packages for uv."""
        config = PipPackages(venv="system", packages=["requests"])
        install_pip_packages(config, default_venv="/venv/main")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "uv"
        assert "--system" in cmd
        assert "--break-system-packages" in cmd
        assert "requests" in cmd

    @patch("provisioner.installers.pip.shutil.which", return_value="/usr/bin/python3")
    @patch("provisioner.installers.pip.run_cmd")
    def test_system_venv_pip(self, mock_run, mock_which):
        """venv='system' with tool=pip should use system python with --break-system-packages."""
        config = PipPackages(venv="system", tool="pip", packages=["requests"])
        install_pip_packages(config, default_venv="/venv/main")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/usr/bin/python3"
        assert "-m" in cmd
        assert "pip" in cmd
        assert "--break-system-packages" in cmd

    @patch("provisioner.installers.pip.shutil.which", return_value="/usr/bin/python3")
    @patch("provisioner.installers.pip.run_cmd")
    def test_system_venv_with_python_version(self, mock_run, mock_which):
        """venv='system' with python='3.11' uses /usr/bin/python3.11."""
        config = PipPackages(venv="system", python="3.11", tool="pip", packages=["requests"])
        install_pip_packages(config, default_venv="/venv/main")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/usr/bin/python3.11"

    @patch("provisioner.installers.pip.shutil.which", return_value="/usr/bin/python3")
    @patch("provisioner.installers.pip.run_cmd")
    def test_system_venv_requirements_with_system_flag(self, mock_run, mock_which):
        """Requirements files installed to system should also use --system and --break-system-packages."""
        config = PipPackages(venv="system", requirements=["/req.txt"])
        install_pip_packages(config, default_venv="/venv/main")
        cmd = mock_run.call_args[0][0]
        assert "--system" in cmd
        assert "--break-system-packages" in cmd
        assert "-r" in cmd

    def test_system_venv_dry_run(self, caplog):
        import logging
        caplog.set_level(logging.INFO)
        config = PipPackages(venv="system", packages=["requests"])
        install_pip_packages(config, default_venv="/venv/main", dry_run=True)
        assert "system python" in caplog.text

    @patch("provisioner.installers.pip.run_cmd")
    def test_version_specifiers(self, mock_run):
        """Version specifiers in package names are passed through."""
        config = PipPackages(packages=[
            "torch>=2.0",
            "numpy==1.24.0",
            "scipy<2.0,>=1.10",
            "transformers~=4.30",
        ])
        install_pip_packages(config, default_venv="/venv/main")
        cmd = mock_run.call_args[0][0]
        assert "torch>=2.0" in cmd
        assert "numpy==1.24.0" in cmd
        assert "scipy<2.0,>=1.10" in cmd
        assert "transformers~=4.30" in cmd


class TestEnsureVenv:
    @patch("provisioner.installers.pip.run_cmd")
    @patch("provisioner.installers.pip.shutil.which", return_value="/usr/bin/uv")
    @patch("provisioner.installers.pip.os.path.isfile", return_value=False)
    @patch("provisioner.installers.pip.os.path.isdir", return_value=False)
    def test_creates_with_uv(self, mock_isdir, mock_isfile, mock_which, mock_run):
        ensure_venv("/venv/test", python_version="3.11")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "uv"
        assert "venv" in cmd
        assert "--python" in cmd
        assert "3.11" in cmd
        assert "/venv/test" in cmd

    @patch("provisioner.installers.pip.run_cmd")
    @patch("provisioner.installers.pip.shutil.which", return_value=None)
    @patch("provisioner.installers.pip.os.path.isfile", return_value=False)
    @patch("provisioner.installers.pip.os.path.isdir", return_value=False)
    def test_creates_with_python_fallback(self, mock_isdir, mock_isfile, mock_which, mock_run):
        ensure_venv("/venv/test", python_version="3.11")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "python3.11"
        assert "-m" in cmd
        assert "venv" in cmd

    @patch("provisioner.installers.pip.run_cmd")
    @patch("provisioner.installers.pip.shutil.which", return_value=None)
    @patch("provisioner.installers.pip.os.path.isfile", return_value=False)
    @patch("provisioner.installers.pip.os.path.isdir", return_value=False)
    def test_creates_default_python(self, mock_isdir, mock_isfile, mock_which, mock_run):
        ensure_venv("/venv/test")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "python3"

    @patch("provisioner.installers.pip.os.path.isfile", return_value=True)
    @patch("provisioner.installers.pip.os.path.isdir", return_value=True)
    def test_skips_existing(self, mock_isdir, mock_isfile):
        # Should not raise or call subprocess
        ensure_venv("/venv/existing")


# ---------- GIT ----------

class TestCloneSingle:
    def test_dry_run(self):
        repo = GitRepo(url="https://github.com/a/b", dest="/tmp/b")
        _clone_single(repo, dry_run=True)

    @patch("provisioner.installers.git.run_cmd")
    @patch("provisioner.installers.git.os.path.isdir", return_value=False)
    def test_clone_basic(self, mock_isdir, mock_run):
        repo = GitRepo(
            url="https://github.com/a/b",
            dest="/workspace/b",
            recursive=True,
        )
        _clone_single(repo)
        cmd = mock_run.call_args_list[0][0][0]
        assert cmd[0] == "git"
        assert "clone" in cmd
        assert "--recursive" in cmd
        assert "https://github.com/a/b" in cmd
        assert "/workspace/b" in cmd

    @patch("provisioner.installers.git.run_cmd")
    @patch("provisioner.installers.git.os.path.isdir", return_value=False)
    def test_clone_without_recursive(self, mock_isdir, mock_run):
        repo = GitRepo(
            url="https://github.com/a/b",
            dest="/workspace/b",
            recursive=False,
        )
        _clone_single(repo)
        cmd = mock_run.call_args_list[0][0][0]
        assert "--recursive" not in cmd

    @patch("provisioner.installers.git.run_cmd")
    @patch("provisioner.installers.git.os.path.isdir", return_value=False)
    def test_clone_with_ref(self, mock_isdir, mock_run):
        repo = GitRepo(
            url="https://github.com/a/b",
            dest="/workspace/b",
            ref="v2.0",
        )
        _clone_single(repo)
        assert mock_run.call_count == 2
        checkout_cmd = mock_run.call_args_list[1][0][0]
        assert "checkout" in checkout_cmd
        assert "v2.0" in checkout_cmd

    @patch("provisioner.installers.git.run_cmd")
    @patch("provisioner.installers.git.os.path.isdir", return_value=True)
    def test_skips_existing_repo(self, mock_isdir, mock_run):
        repo = GitRepo(
            url="https://github.com/a/b",
            dest="/workspace/b",
            pull_if_exists=False,
        )
        _clone_single(repo)
        mock_run.assert_not_called()

    @patch("provisioner.installers.git.run_cmd")
    @patch("provisioner.installers.git.os.path.isdir", return_value=True)
    def test_pulls_existing_repo(self, mock_isdir, mock_run):
        repo = GitRepo(
            url="https://github.com/a/b",
            dest="/workspace/b",
            pull_if_exists=True,
        )
        _clone_single(repo)
        cmd = mock_run.call_args[0][0]
        assert "pull" in cmd

    @patch("provisioner.installers.git.run_cmd")
    @patch("provisioner.installers.git.os.path.isdir", return_value=False)
    def test_clone_does_not_run_post_commands(self, mock_isdir, mock_run):
        """_clone_single no longer runs post_commands (they run sequentially after all clones)."""
        repo = GitRepo(
            url="https://github.com/a/b",
            dest="/workspace/b",
            post_commands=["sed -i 's/foo/bar/' config.yaml", "chmod +x run.sh"],
        )
        _clone_single(repo)
        # Only clone, no post commands
        assert mock_run.call_count == 1

    @patch("provisioner.installers.git.run_cmd")
    @patch("provisioner.installers.git.os.path.isdir", return_value=False)
    def test_clone_with_ref_no_post_commands(self, mock_isdir, mock_run):
        """Clone + checkout only, post_commands handled separately."""
        repo = GitRepo(
            url="https://github.com/a/b",
            dest="/workspace/b",
            ref="v2.0",
            post_commands=["echo done"],
        )
        _clone_single(repo)
        # clone + checkout = 2 calls, no post command
        assert mock_run.call_count == 2
        assert "checkout" in mock_run.call_args_list[1][0][0]

    def test_post_commands_dry_run(self, caplog):
        import logging
        caplog.set_level(logging.INFO)
        repo = GitRepo(
            url="https://github.com/a/b",
            dest="/workspace/b",
            post_commands=["echo hello"],
        )
        _clone_single(repo, dry_run=True)
        assert "echo hello" in caplog.text


class TestRunPostCommands:
    @patch("provisioner.installers.git.run_cmd")
    def test_runs_commands_sequentially(self, mock_run):
        repo = GitRepo(
            url="https://github.com/a/b",
            dest="/workspace/b",
            post_commands=["sed -i 's/foo/bar/' config.yaml", "chmod +x run.sh"],
        )
        _run_post_commands(repo)
        assert mock_run.call_count == 2
        post1 = mock_run.call_args_list[0]
        assert post1[0][0] == "sed -i 's/foo/bar/' config.yaml"
        assert post1[1]["shell"] is True
        assert post1[1]["cwd"] == "/workspace/b"
        post2 = mock_run.call_args_list[1]
        assert post2[0][0] == "chmod +x run.sh"

    @patch("provisioner.installers.git.run_cmd")
    def test_no_commands_is_noop(self, mock_run):
        repo = GitRepo(url="https://github.com/a/b", dest="/workspace/b")
        _run_post_commands(repo)
        mock_run.assert_not_called()

    def test_dry_run(self, caplog):
        import logging
        caplog.set_level(logging.INFO)
        repo = GitRepo(
            url="https://github.com/a/b",
            dest="/workspace/b",
            post_commands=["echo hello"],
        )
        _run_post_commands(repo, dry_run=True)
        assert "echo hello" in caplog.text


class TestCloneGitRepos:
    def test_empty_list(self):
        clone_git_repos([])

    def test_dry_run(self):
        repos = [GitRepo(url="https://github.com/a/b", dest="/d")]
        clone_git_repos(repos, dry_run=True)

    @patch("provisioner.installers.git._run_post_commands")
    @patch("provisioner.installers.git._clone_single")
    def test_parallel_clone_sequential_post(self, mock_clone, mock_post):
        repos = [
            GitRepo(url="https://github.com/a/b", dest="/d/a", post_commands=["echo a"]),
            GitRepo(url="https://github.com/c/d", dest="/d/c", post_commands=["echo c"]),
        ]
        clone_git_repos(repos)
        assert mock_clone.call_count == 2
        assert mock_post.call_count == 2

    @patch("provisioner.installers.git._clone_single", side_effect=RuntimeError("fail"))
    def test_raises_on_failure(self, mock_clone):
        repos = [GitRepo(url="https://github.com/a/b", dest="/d")]
        with pytest.raises(RuntimeError, match="failed"):
            clone_git_repos(repos)
