"""Tests for provisioner.subprocess_runner -- run_cmd helper."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch, call

import pytest

from provisioner.subprocess_runner import _clean_env, run_cmd


class TestRunCmd:
    @patch("provisioner.subprocess_runner.subprocess.Popen")
    def test_captures_and_logs_output(self, mock_popen, caplog):
        import logging
        caplog.set_level(logging.INFO)

        proc = MagicMock()
        proc.stdout = iter(["line one\n", "line two\n"])
        proc.wait.return_value = None
        proc.returncode = 0
        mock_popen.return_value = proc

        result = run_cmd(["echo", "hello"], label="test")

        assert result.returncode == 0
        assert "line one" in result.stdout
        assert "line two" in result.stdout
        assert "[test] line one" in caplog.text
        assert "[test] line two" in caplog.text

    @patch("provisioner.subprocess_runner.subprocess.Popen")
    def test_check_true_raises_on_failure(self, mock_popen):
        proc = MagicMock()
        proc.stdout = iter(["error msg\n"])
        proc.wait.return_value = None
        proc.returncode = 1
        mock_popen.return_value = proc

        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            run_cmd(["false"], label="fail", check=True)
        assert exc_info.value.returncode == 1
        assert "error msg" in exc_info.value.output

    @patch("provisioner.subprocess_runner.subprocess.Popen")
    def test_check_false_does_not_raise(self, mock_popen):
        proc = MagicMock()
        proc.stdout = iter([])
        proc.wait.return_value = None
        proc.returncode = 42
        mock_popen.return_value = proc

        result = run_cmd(["false"], label="ok", check=False)
        assert result.returncode == 42

    @patch("provisioner.subprocess_runner.subprocess.Popen")
    def test_forwards_cwd_shell_env(self, mock_popen):
        proc = MagicMock()
        proc.stdout = iter([])
        proc.wait.return_value = None
        proc.returncode = 0
        mock_popen.return_value = proc

        env = {"FOO": "bar"}
        run_cmd("echo hi", label="x", shell=True, cwd="/tmp", env=env)

        kwargs = mock_popen.call_args[1]
        assert kwargs["cwd"] == "/tmp"
        assert kwargs["shell"] is True
        assert kwargs["env"]["FOO"] == "bar"

    @patch("provisioner.subprocess_runner.subprocess.Popen")
    def test_merges_stderr_into_stdout(self, mock_popen):
        """stderr=STDOUT merges both streams."""
        proc = MagicMock()
        proc.stdout = iter([])
        proc.wait.return_value = None
        proc.returncode = 0
        mock_popen.return_value = proc

        run_cmd(["ls"], label="ls")

        kwargs = mock_popen.call_args[1]
        assert kwargs["stdout"] == subprocess.PIPE
        assert kwargs["stderr"] == subprocess.STDOUT

    @patch("provisioner.subprocess_runner.subprocess.Popen")
    def test_returns_completed_process(self, mock_popen):
        proc = MagicMock()
        proc.stdout = iter(["hello\n"])
        proc.wait.return_value = None
        proc.returncode = 0
        mock_popen.return_value = proc

        result = run_cmd(["echo", "hello"], label="echo")
        assert isinstance(result, subprocess.CompletedProcess)
        assert result.args == ["echo", "hello"]
        assert result.stdout == "hello"
        assert result.stderr == ""

    @patch("provisioner.subprocess_runner.subprocess.Popen")
    def test_strips_venv_env_vars(self, mock_popen, monkeypatch):
        """VIRTUAL_ENV and friends should be stripped from the child env."""
        monkeypatch.setenv("VIRTUAL_ENV", "/opt/instance-tools/provisioner/venv")
        monkeypatch.setenv("CONDA_PREFIX", "/some/conda")
        monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/some/uv")
        monkeypatch.setenv("KEEP_THIS", "yes")

        proc = MagicMock()
        proc.stdout = iter([])
        proc.wait.return_value = None
        proc.returncode = 0
        mock_popen.return_value = proc

        run_cmd(["echo"], label="test")

        child_env = mock_popen.call_args[1]["env"]
        assert "VIRTUAL_ENV" not in child_env
        assert "CONDA_PREFIX" not in child_env
        assert "UV_PROJECT_ENVIRONMENT" not in child_env
        assert child_env["KEEP_THIS"] == "yes"

    @patch("provisioner.subprocess_runner.subprocess.Popen")
    def test_explicit_env_also_cleaned(self, mock_popen):
        """Even an explicitly passed env dict gets venv vars stripped."""
        proc = MagicMock()
        proc.stdout = iter([])
        proc.wait.return_value = None
        proc.returncode = 0
        mock_popen.return_value = proc

        env = {"VIRTUAL_ENV": "/bad/venv", "FOO": "bar"}
        run_cmd(["echo"], label="test", env=env)

        child_env = mock_popen.call_args[1]["env"]
        assert "VIRTUAL_ENV" not in child_env
        assert child_env["FOO"] == "bar"


class TestCleanEnv:
    def test_strips_venv_vars(self):
        env = {
            "VIRTUAL_ENV": "/some/venv",
            "CONDA_PREFIX": "/conda",
            "CONDA_DEFAULT_ENV": "base",
            "UV_PROJECT_ENVIRONMENT": "/uv",
            "PATH": "/usr/bin",
            "HOME": "/root",
        }
        result = _clean_env(env)
        assert "VIRTUAL_ENV" not in result
        assert "CONDA_PREFIX" not in result
        assert "CONDA_DEFAULT_ENV" not in result
        assert "UV_PROJECT_ENVIRONMENT" not in result
        assert result["PATH"] == "/usr/bin"
        assert result["HOME"] == "/root"

    def test_none_uses_os_environ(self, monkeypatch):
        monkeypatch.setenv("VIRTUAL_ENV", "/provisioner/venv")
        monkeypatch.setenv("TEST_MARKER", "present")
        result = _clean_env(None)
        assert "VIRTUAL_ENV" not in result
        assert result["TEST_MARKER"] == "present"

    def test_does_not_mutate_input(self):
        env = {"VIRTUAL_ENV": "/venv", "FOO": "bar"}
        _clean_env(env)
        assert "VIRTUAL_ENV" in env  # original unchanged
