"""Integration tests -- end-to-end dry-run, CLI invocation, download classification, error handling."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest
import yaml

from provisioner.__main__ import _classify_downloads, run
from provisioner.schema import DownloadEntry

# Compute the lib directory (parent of the provisioner package) for PYTHONPATH
_LIB_DIR = str(pathlib.Path(__file__).resolve().parent.parent.parent)


# ---------- _classify_downloads ----------

class TestClassifyDownloads:
    def test_empty(self):
        hf, wget = _classify_downloads([])
        assert hf == []
        assert wget == []

    def test_hf_url(self):
        hf, wget = _classify_downloads([
            DownloadEntry(url="https://huggingface.co/org/repo/resolve/main/f.bin", dest="/d/f"),
        ])
        assert len(hf) == 1
        assert len(wget) == 0

    def test_civitai_url(self):
        hf, wget = _classify_downloads([
            DownloadEntry(url="https://civitai.com/api/download/models/123", dest="/d/m"),
        ])
        assert len(hf) == 0
        assert len(wget) == 1

    def test_generic_url(self):
        hf, wget = _classify_downloads([
            DownloadEntry(url="https://example.com/file.bin", dest="/d/f"),
        ])
        assert len(hf) == 0
        assert len(wget) == 1

    def test_mixed(self):
        hf, wget = _classify_downloads([
            DownloadEntry(url="https://huggingface.co/a/b/resolve/main/f", dest="/d/1"),
            DownloadEntry(url="https://civitai.com/api/download/models/1", dest="/d/2"),
            DownloadEntry(url="https://example.com/f.bin", dest="/d/3"),
            DownloadEntry(url="https://huggingface.co/c/d/resolve/main/g", dest="/d/4"),
        ])
        assert len(hf) == 2
        assert len(wget) == 2


# ---------- Full dry-run pipeline ----------

class TestDryRunPipeline:
    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    def test_minimal_manifest(self, mock_civ, mock_hf, tmp_manifest):
        path = tmp_manifest({"version": 1})
        result = run(path, dry_run=True)
        assert result == 0

    @patch("provisioner.__main__.validate_hf_token", return_value=True)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    def test_full_manifest_dry_run(self, mock_civ, mock_hf, tmp_manifest, full_manifest_data):
        path = tmp_manifest(full_manifest_data)
        result = run(path, dry_run=True)
        assert result == 0

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    def test_conditional_resolves_false(self, mock_civ, mock_hf, tmp_manifest):
        path = tmp_manifest({
            "version": 1,
            "conditional_downloads": [{
                "when": "hf_token_valid",
                "downloads": [
                    {"url": "https://huggingface.co/a/b/resolve/main/gated.bin", "dest": "/d/g"},
                ],
                "else_downloads": [
                    {"url": "https://huggingface.co/a/b/resolve/main/open.bin", "dest": "/d/o"},
                ],
            }],
        })
        result = run(path, dry_run=True)
        assert result == 0

    @patch("provisioner.__main__.validate_hf_token", return_value=True)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    def test_conditional_resolves_true(self, mock_civ, mock_hf, tmp_manifest):
        path = tmp_manifest({
            "version": 1,
            "conditional_downloads": [{
                "when": "hf_token_valid",
                "downloads": [
                    {"url": "https://huggingface.co/a/b/resolve/main/gated.bin", "dest": "/d/g"},
                ],
                "else_downloads": [],
            }],
        })
        result = run(path, dry_run=True)
        assert result == 0

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    def test_env_merge_in_dry_run(self, mock_civ, mock_hf, tmp_manifest, monkeypatch):
        monkeypatch.setenv("EXTRA_MODELS", "https://example.com/a.bin|/d/a")
        path = tmp_manifest({
            "version": 1,
            "env_merge": {"EXTRA_MODELS": "downloads"},
        })
        result = run(path, dry_run=True)
        assert result == 0

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    def test_services_dry_run(self, mock_civ, mock_hf, tmp_manifest):
        path = tmp_manifest({
            "version": 1,
            "services": [{
                "name": "test-svc",
                "command": "echo hi",
                "workdir": "/tmp",
                "portal_search_term": "Test",
                "environment": {"KEY": "val"},
            }],
        })
        result = run(path, dry_run=True)
        assert result == 0

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    def test_post_commands_dry_run(self, mock_civ, mock_hf, tmp_manifest):
        path = tmp_manifest({
            "version": 1,
            "post_commands": ["echo hello", "echo world"],
        })
        result = run(path, dry_run=True)
        assert result == 0

    def test_invalid_manifest_returns_1(self, tmp_manifest):
        path = tmp_manifest({"version": 2})
        result = run(path, dry_run=True)
        assert result == 1

    def test_empty_manifest_returns_1(self, tmp_path):
        path = tmp_path / "empty.yaml"
        path.write_text("")
        result = run(str(path), dry_run=True)
        assert result == 1

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    def test_env_expansion_throughout(self, mock_civ, mock_hf, tmp_manifest, monkeypatch):
        monkeypatch.setenv("MY_WS", "/workspace")
        path = tmp_manifest({
            "version": 1,
            "downloads": [{
                "url": "https://huggingface.co/a/b/resolve/main/f.bin",
                "dest": "${MY_WS}/models/f.bin",
            }],
        })
        result = run(path, dry_run=True)
        assert result == 0

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    def test_multi_pip_blocks_dry_run(self, mock_civ, mock_hf, tmp_manifest):
        path = tmp_manifest({
            "version": 1,
            "pip_packages": [
                {"packages": ["torch"], "venv": "/venv/main"},
                {"packages": ["numpy"], "venv": "/venv/custom", "python": "3.11"},
            ],
        })
        result = run(path, dry_run=True)
        assert result == 0

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    def test_old_dict_pip_packages_dry_run(self, mock_civ, mock_hf, tmp_manifest):
        """Old single-dict format still works via backward compat."""
        path = tmp_manifest({
            "version": 1,
            "pip_packages": {"packages": ["torch"], "tool": "uv"},
        })
        result = run(path, dry_run=True)
        assert result == 0

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    def test_write_files_dry_run(self, mock_civ, mock_hf, tmp_manifest):
        path = tmp_manifest({
            "version": 1,
            "write_files": [
                {"path": "/tmp/early.conf", "content": "key=value\n"},
            ],
            "write_files_late": [
                {"path": "/tmp/late.conf", "content": "done=true\n", "permissions": "0600"},
            ],
        })
        result = run(path, dry_run=True)
        assert result == 0

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    def test_conda_packages_dry_run(self, mock_civ, mock_hf, tmp_manifest):
        path = tmp_manifest({
            "version": 1,
            "conda_packages": {
                "packages": ["numpy=1.24", "scipy>=1.10"],
                "channels": ["conda-forge"],
            },
        })
        result = run(path, dry_run=True)
        assert result == 0

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    def test_system_pip_dry_run(self, mock_civ, mock_hf, tmp_manifest):
        path = tmp_manifest({
            "version": 1,
            "pip_packages": [{"venv": "system", "packages": ["requests"]}],
        })
        result = run(path, dry_run=True)
        assert result == 0

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    def test_extensions_dry_run(self, mock_civ, mock_hf, tmp_manifest):
        path = tmp_manifest({
            "version": 1,
            "extensions": [
                {"module": "nonexistent_ext", "config": {"key": "val"}},
                {"module": "disabled_ext", "enabled": False},
            ],
        })
        result = run(path, dry_run=True)
        assert result == 0

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    def test_on_failure_in_manifest(self, mock_civ, mock_hf, tmp_manifest):
        path = tmp_manifest({
            "version": 1,
            "on_failure": {"action": "retry", "max_retries": 2},
        })
        result = run(path, dry_run=True)
        assert result == 0


# ---------- Error handling behavior ----------

class TestErrorHandling:
    """Verify fail-fast vs best-effort vs always-run semantics."""

    @patch("provisioner.__main__.handle_failure")
    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    @patch("provisioner.__main__.install_apt_packages", side_effect=RuntimeError("apt broke"))
    @patch("provisioner.__main__.clone_git_repos")
    @patch("provisioner.__main__.install_pip_packages")
    @patch("provisioner.__main__.register_services")
    def test_apt_failure_is_fatal(
        self, mock_svc, mock_pip, mock_git, mock_apt, mock_civ, mock_hf,
        mock_failure, tmp_manifest,
    ):
        """Phase 3 failure should abort -- phases 4-8 never run."""
        path = tmp_manifest({"version": 1, "apt_packages": ["bad-pkg"]})
        result = run(path)
        assert result == 1
        mock_git.assert_not_called()
        mock_pip.assert_not_called()
        mock_svc.assert_not_called()
        mock_failure.assert_called_once()

    @patch("provisioner.__main__.handle_failure")
    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    @patch("provisioner.__main__.install_apt_packages")
    @patch("provisioner.__main__.clone_git_repos", side_effect=RuntimeError("clone broke"))
    @patch("provisioner.__main__.install_pip_packages")
    @patch("provisioner.__main__.register_services")
    def test_git_failure_is_fatal(
        self, mock_svc, mock_pip, mock_git, mock_apt, mock_civ, mock_hf,
        mock_failure, tmp_manifest,
    ):
        """Phase 4 failure should abort -- phases 5-8 never run."""
        path = tmp_manifest({
            "version": 1,
            "git_repos": [{"url": "https://github.com/a/b", "dest": "/d"}],
        })
        result = run(path)
        assert result == 1
        mock_pip.assert_not_called()
        mock_svc.assert_not_called()

    @patch("provisioner.__main__.handle_failure")
    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    @patch("provisioner.__main__.install_apt_packages")
    @patch("provisioner.__main__.clone_git_repos")
    @patch("provisioner.__main__.install_pip_packages", side_effect=RuntimeError("pip broke"))
    @patch("provisioner.__main__.register_services")
    def test_pip_failure_is_fatal(
        self, mock_svc, mock_pip, mock_git, mock_apt, mock_civ, mock_hf,
        mock_failure, tmp_manifest,
    ):
        """Phase 5 failure should abort -- phases 6-8 never run."""
        path = tmp_manifest({
            "version": 1,
            "pip_packages": [{"packages": ["bad-pkg"]}],
        })
        result = run(path)
        assert result == 1
        mock_svc.assert_not_called()

    @patch("provisioner.__main__.handle_failure")
    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    @patch("provisioner.__main__.install_apt_packages")
    @patch("provisioner.__main__.clone_git_repos")
    @patch("provisioner.__main__.install_pip_packages")
    @patch("provisioner.__main__.run_parallel", return_value=[ValueError("download failed")])
    @patch("provisioner.__main__.register_services")
    def test_download_failure_continues_to_services(
        self, mock_svc, mock_parallel, mock_pip, mock_git, mock_apt, mock_civ, mock_hf,
        mock_failure, tmp_manifest,
    ):
        """Phase 6 failure should NOT block phases 7-8."""
        path = tmp_manifest({
            "version": 1,
            "downloads": [
                {"url": "https://example.com/f.bin", "dest": "/d/f"},
            ],
            "services": [{"name": "app", "command": "echo", "workdir": "/tmp"}],
        })
        result = run(path)
        assert result == 1  # exit code still non-zero
        mock_svc.assert_called_once()  # but services were registered

    @patch("provisioner.__main__.handle_failure")
    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    @patch("provisioner.__main__.install_apt_packages")
    @patch("provisioner.__main__.clone_git_repos")
    @patch("provisioner.__main__.install_pip_packages")
    @patch("provisioner.__main__.run_parallel", return_value=[ValueError("download failed")])
    @patch("provisioner.__main__.register_services")
    @patch("provisioner.__main__.subprocess.run")
    def test_download_failure_continues_to_post_commands(
        self, mock_subproc, mock_svc, mock_parallel, mock_pip, mock_git, mock_apt,
        mock_civ, mock_hf, mock_failure, tmp_manifest,
    ):
        """Phase 6 failure should NOT block phase 8 post_commands."""
        mock_subproc.return_value = MagicMock(returncode=0)
        path = tmp_manifest({
            "version": 1,
            "downloads": [
                {"url": "https://example.com/f.bin", "dest": "/d/f"},
            ],
            "post_commands": ["echo hello"],
        })
        result = run(path)
        assert result == 1  # exit code still non-zero
        mock_subproc.assert_called_once()  # but post_command ran

    @patch("provisioner.__main__.handle_failure")
    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    @patch("provisioner.__main__.install_apt_packages")
    @patch("provisioner.__main__.run_extensions", side_effect=RuntimeError("extension broke"))
    @patch("provisioner.__main__.clone_git_repos")
    @patch("provisioner.__main__.install_pip_packages")
    @patch("provisioner.__main__.register_services")
    def test_extension_failure_is_fatal(
        self, mock_svc, mock_pip, mock_git, mock_ext, mock_apt, mock_civ, mock_hf,
        mock_failure, tmp_manifest,
    ):
        """Phase 3b failure should abort -- phases 4-8 never run."""
        path = tmp_manifest({
            "version": 1,
            "extensions": [{"module": "bad_ext", "config": {}}],
            "git_repos": [{"url": "https://github.com/a/b", "dest": "/d"}],
            "services": [{"name": "app", "command": "echo", "workdir": "/tmp"}],
        })
        result = run(path)
        assert result == 1
        mock_git.assert_not_called()
        mock_pip.assert_not_called()
        mock_svc.assert_not_called()
        mock_failure.assert_called_once()

    @patch("provisioner.__main__.handle_failure")
    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    @patch("provisioner.__main__.install_apt_packages")
    @patch("provisioner.__main__.clone_git_repos")
    @patch("provisioner.__main__.install_pip_packages")
    @patch("provisioner.__main__.register_services", side_effect=RuntimeError("supervisor broke"))
    @patch("provisioner.__main__.subprocess.run")
    def test_service_failure_continues_to_post_commands(
        self, mock_subproc, mock_svc, mock_pip, mock_git, mock_apt,
        mock_civ, mock_hf, mock_failure, tmp_manifest,
    ):
        """Phase 7 failure should NOT block phase 8."""
        mock_subproc.return_value = MagicMock(returncode=0)
        path = tmp_manifest({
            "version": 1,
            "services": [{"name": "app", "command": "echo", "workdir": "/tmp"}],
            "post_commands": ["echo hello"],
        })
        result = run(path)
        assert result == 1  # exit code still non-zero
        mock_subproc.assert_called_once()  # but post_command ran

    @patch("provisioner.__main__.handle_failure")
    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    @patch("provisioner.__main__.install_apt_packages")
    @patch("provisioner.__main__.clone_git_repos")
    @patch("provisioner.__main__.install_pip_packages")
    @patch("provisioner.__main__.subprocess.run")
    def test_post_command_failure_does_not_block_later_commands(
        self, mock_subproc, mock_pip, mock_git, mock_apt, mock_civ, mock_hf,
        mock_failure, tmp_manifest,
    ):
        """A failing post_command should not prevent subsequent commands from running."""
        mock_subproc.side_effect = [
            MagicMock(returncode=1),  # first command fails
            MagicMock(returncode=0),  # second command succeeds
        ]
        path = tmp_manifest({
            "version": 1,
            "post_commands": ["false", "echo hello"],
        })
        result = run(path)
        assert result == 1
        assert mock_subproc.call_count == 2  # both commands ran

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    @patch("provisioner.__main__.install_apt_packages")
    @patch("provisioner.__main__.clone_git_repos")
    @patch("provisioner.__main__.install_pip_packages")
    def test_no_errors_returns_0(
        self, mock_pip, mock_git, mock_apt, mock_civ, mock_hf, tmp_manifest,
    ):
        """Clean run with no failures returns 0."""
        path = tmp_manifest({"version": 1})
        result = run(path)
        assert result == 0


# ---------- Stage idempotency ----------

class TestStageIdempotency:
    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    @patch("provisioner.__main__.install_apt_packages")
    @patch("provisioner.__main__.clone_git_repos")
    @patch("provisioner.__main__.install_pip_packages")
    def test_second_run_skips_stages(
        self, mock_pip, mock_git, mock_apt, mock_civ, mock_hf, tmp_manifest, tmp_path,
        monkeypatch,
    ):
        """Second run with same manifest should skip all stages."""
        # Point STATE_DIR to temp directory
        state_dir = str(tmp_path / "state")
        monkeypatch.setattr("provisioner.__main__.is_stage_complete",
                            __import__("provisioner.state", fromlist=["is_stage_complete"]).is_stage_complete)
        monkeypatch.setattr("provisioner.state.STATE_DIR", state_dir)

        path = tmp_manifest({
            "version": 1,
            "apt_packages": ["vim"],
        })
        # First run
        result = run(path)
        assert result == 0
        assert mock_apt.call_count == 1

        # Second run - apt should be skipped
        result = run(path)
        assert result == 0
        assert mock_apt.call_count == 1  # not called again

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    @patch("provisioner.__main__.install_apt_packages")
    @patch("provisioner.__main__.clone_git_repos")
    @patch("provisioner.__main__.install_pip_packages")
    def test_force_clears_state(
        self, mock_pip, mock_git, mock_apt, mock_civ, mock_hf, tmp_manifest, tmp_path,
        monkeypatch,
    ):
        """--force should clear state and re-run everything."""
        state_dir = str(tmp_path / "state")
        monkeypatch.setattr("provisioner.state.STATE_DIR", state_dir)

        path = tmp_manifest({
            "version": 1,
            "apt_packages": ["vim"],
        })
        # First run
        result = run(path)
        assert result == 0
        assert mock_apt.call_count == 1

        # Second run with force
        result = run(path, force=True)
        assert result == 0
        assert mock_apt.call_count == 2  # called again


# ---------- CLI subprocess test ----------

class TestCLI:
    def test_help(self):
        """The --help flag should work and exit 0."""
        result = subprocess.run(
            [sys.executable, "-m", "provisioner", "--help"],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": _LIB_DIR},
        )
        assert result.returncode == 0
        assert "manifest" in result.stdout.lower()
        assert "dry-run" in result.stdout
        assert "force" in result.stdout

    def test_missing_manifest(self):
        """Running without a manifest arg should exit non-zero."""
        result = subprocess.run(
            [sys.executable, "-m", "provisioner"],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": _LIB_DIR},
        )
        assert result.returncode != 0

    def test_dry_run_via_subprocess(self, tmp_manifest, minimal_manifest_data):
        path = tmp_manifest(minimal_manifest_data)
        result = subprocess.run(
            [sys.executable, "-m", "provisioner", path, "--dry-run"],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": _LIB_DIR},
        )
        assert result.returncode == 0
        assert "Provisioning complete" in result.stdout
