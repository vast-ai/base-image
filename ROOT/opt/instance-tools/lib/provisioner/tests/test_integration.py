"""Integration tests -- end-to-end dry-run, CLI invocation, download classification, error handling."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time
from unittest.mock import MagicMock, patch

import pytest
import yaml

from provisioner.__main__ import _apply_env_overrides, _classify_downloads, run, run_with_retries
from provisioner.manifest import load_manifest
from provisioner.schema import DownloadEntry, Manifest, OnFailure

# Compute the lib directory (parent of the provisioner package) for PYTHONPATH
_LIB_DIR = str(pathlib.Path(__file__).resolve().parent.parent.parent)


def _load_and_run(path, dry_run=False, force=False):
    """Helper: load manifest and call run() with it."""
    manifest = load_manifest(path)
    return run(path, manifest, dry_run=dry_run, force=force)


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

    def test_spoofed_hf_url_not_classified_as_hf(self):
        """A URL containing 'huggingface.co' in a different domain is not HF."""
        hf, wget = _classify_downloads([
            DownloadEntry(url="https://not-huggingface.co.evil.com/file.bin", dest="/d/f"),
        ])
        assert len(hf) == 0
        assert len(wget) == 1

    def test_hf_subdomain_classified_as_hf(self):
        """HF CDN subdomains (e.g. cdn-lfs.huggingface.co) should be classified as HF."""
        hf, wget = _classify_downloads([
            DownloadEntry(url="https://cdn-lfs.huggingface.co/file.bin", dest="/d/f"),
        ])
        assert len(hf) == 1
        assert len(wget) == 0


# ---------- _apply_env_overrides ----------

class TestApplyEnvOverrides:
    def test_retry_max_override(self, monkeypatch):
        manifest = Manifest()
        monkeypatch.setenv("PROVISIONER_RETRY_MAX", "5")
        _apply_env_overrides(manifest)
        assert manifest.on_failure.max_retries == 5

    def test_retry_delay_override(self, monkeypatch):
        manifest = Manifest()
        monkeypatch.setenv("PROVISIONER_RETRY_DELAY", "60")
        _apply_env_overrides(manifest)
        assert manifest.on_failure.retry_delay == 60

    def test_failure_action_override(self, monkeypatch):
        manifest = Manifest()
        monkeypatch.setenv("PROVISIONER_FAILURE_ACTION", "destroy")
        _apply_env_overrides(manifest)
        assert manifest.on_failure.action == "destroy"

    def test_webhook_url_override(self, monkeypatch):
        manifest = Manifest()
        monkeypatch.setenv("PROVISIONER_WEBHOOK_URL", "https://hooks.example.com")
        _apply_env_overrides(manifest)
        assert manifest.on_failure.webhook == "https://hooks.example.com"

    def test_log_file_override(self, monkeypatch):
        manifest = Manifest()
        monkeypatch.setenv("PROVISIONER_LOG_FILE", "/tmp/custom.log")
        _apply_env_overrides(manifest)
        assert manifest.settings.log_file == "/tmp/custom.log"

    def test_venv_override(self, monkeypatch):
        manifest = Manifest()
        monkeypatch.setenv("PROVISIONER_VENV", "/venv/custom")
        _apply_env_overrides(manifest)
        assert manifest.settings.venv == "/venv/custom"

    def test_webhook_on_success_override(self, monkeypatch):
        manifest = Manifest()
        monkeypatch.setenv("PROVISIONER_WEBHOOK_ON_SUCCESS", "true")
        _apply_env_overrides(manifest)
        assert manifest.on_failure.webhook_on_success is True

    def test_webhook_on_success_override_false(self, monkeypatch):
        manifest = Manifest()
        monkeypatch.setenv("PROVISIONER_WEBHOOK_ON_SUCCESS", "0")
        _apply_env_overrides(manifest)
        assert manifest.on_failure.webhook_on_success is False

    def test_no_override_when_unset(self):
        manifest = Manifest()
        _apply_env_overrides(manifest)
        assert manifest.on_failure.max_retries == 3
        assert manifest.on_failure.retry_delay == 30
        assert manifest.on_failure.action == "continue"
        assert manifest.on_failure.webhook_on_success is False
        assert manifest.settings.venv == "/venv/main"

    def test_malformed_retry_max_ignored(self, monkeypatch):
        """Non-integer PROVISIONER_RETRY_MAX is ignored with a warning."""
        manifest = Manifest()
        monkeypatch.setenv("PROVISIONER_RETRY_MAX", "abc")
        _apply_env_overrides(manifest)
        assert manifest.on_failure.max_retries == 3  # unchanged default

    def test_malformed_retry_delay_ignored(self, monkeypatch):
        """Non-integer PROVISIONER_RETRY_DELAY is ignored with a warning."""
        manifest = Manifest()
        monkeypatch.setenv("PROVISIONER_RETRY_DELAY", "not_a_number")
        _apply_env_overrides(manifest)
        assert manifest.on_failure.retry_delay == 30  # unchanged default


# ---------- run_with_retries ----------

class TestRunWithRetries:
    @patch("provisioner.__main__.run", return_value=0)
    @patch("provisioner.__main__.handle_failure")
    def test_success_first_attempt(self, mock_failure, mock_run, tmp_manifest):
        path = tmp_manifest({"version": 1})
        rc = run_with_retries(path)
        assert rc == 0
        assert mock_run.call_count == 1
        mock_failure.assert_not_called()

    @patch("provisioner.__main__.run", return_value=1)
    @patch("provisioner.__main__.handle_failure")
    @patch("provisioner.__main__.time.sleep")
    def test_retries_then_fails(self, mock_sleep, mock_failure, mock_run, tmp_manifest):
        path = tmp_manifest({"version": 1, "on_failure": {"max_retries": 3, "retry_delay": 10}})
        rc = run_with_retries(path)
        assert rc == 1
        assert mock_run.call_count == 3
        # Should have slept between retries (but not after last attempt)
        assert mock_sleep.call_count == 2
        mock_sleep.assert_called_with(10)
        mock_failure.assert_called_once()

    @patch("provisioner.__main__.run", side_effect=[1, 0])
    @patch("provisioner.__main__.handle_failure")
    @patch("provisioner.__main__.time.sleep")
    def test_success_on_second_attempt(self, mock_sleep, mock_failure, mock_run, tmp_manifest):
        path = tmp_manifest({"version": 1, "on_failure": {"max_retries": 3, "retry_delay": 5}})
        rc = run_with_retries(path)
        assert rc == 0
        assert mock_run.call_count == 2
        mock_sleep.assert_called_once_with(5)
        mock_failure.assert_not_called()

    @patch("provisioner.__main__.run", return_value=1)
    @patch("provisioner.__main__.handle_failure")
    def test_max_retries_zero_skips_loop(self, mock_failure, mock_run, tmp_manifest):
        path = tmp_manifest({"version": 1, "on_failure": {"max_retries": 0}})
        rc = run_with_retries(path)
        assert rc == 1
        assert mock_run.call_count == 1
        mock_failure.assert_called_once()

    @patch("provisioner.__main__.run", return_value=0)
    @patch("provisioner.__main__.handle_failure")
    def test_dry_run_no_retries(self, mock_failure, mock_run, tmp_manifest):
        path = tmp_manifest({"version": 1})
        rc = run_with_retries(path, dry_run=True)
        assert rc == 0
        assert mock_run.call_count == 1
        mock_failure.assert_not_called()

    @patch("provisioner.__main__.run", return_value=1)
    @patch("provisioner.__main__.handle_failure")
    def test_dry_run_failure_no_handle(self, mock_failure, mock_run, tmp_manifest):
        """Dry run failure should not call handle_failure."""
        path = tmp_manifest({"version": 1})
        rc = run_with_retries(path, dry_run=True)
        assert rc == 1
        mock_failure.assert_not_called()

    @patch("provisioner.__main__.run", return_value=0)
    @patch("provisioner.__main__.notify_success")
    @patch("provisioner.__main__.handle_failure")
    def test_success_webhook_called(self, mock_failure, mock_notify, mock_run, tmp_manifest):
        """Success webhook should fire on successful provisioning."""
        path = tmp_manifest({"version": 1, "on_failure": {"webhook": "https://hooks.example.com", "webhook_on_success": True}})
        rc = run_with_retries(path)
        assert rc == 0
        mock_notify.assert_called_once()
        mock_failure.assert_not_called()

    @patch("provisioner.__main__.run", return_value=0)
    @patch("provisioner.__main__.notify_success")
    def test_success_webhook_not_called_on_dry_run(self, mock_notify, mock_run, tmp_manifest):
        """Dry run should not fire success webhook."""
        path = tmp_manifest({"version": 1, "on_failure": {"webhook": "https://hooks.example.com", "webhook_on_success": True}})
        rc = run_with_retries(path, dry_run=True)
        assert rc == 0
        mock_notify.assert_not_called()

    def test_invalid_manifest_returns_1(self, tmp_manifest):
        path = tmp_manifest({"version": 2})
        rc = run_with_retries(path)
        assert rc == 1

    @patch("provisioner.__main__.resolve_manifest_source", side_effect=RuntimeError("download failed"))
    def test_url_download_failure_returns_1(self, mock_resolve):
        """URL download failure should return 1 without crashing."""
        rc = run_with_retries("https://example.com/bad-manifest.yaml")
        assert rc == 1

    @patch("provisioner.__main__.run", return_value=0)
    @patch("provisioner.__main__.resolve_manifest_source", return_value="/tmp/manifest.yaml")
    @patch("provisioner.__main__.handle_failure")
    def test_url_resolved_before_load(self, mock_failure, mock_resolve, mock_run, tmp_manifest):
        """resolve_manifest_source is called before load_manifest."""
        path = tmp_manifest({"version": 1})
        mock_resolve.return_value = path
        rc = run_with_retries("https://example.com/manifest.yaml")
        assert rc == 0
        mock_resolve.assert_called_once_with("https://example.com/manifest.yaml")

    @patch("provisioner.__main__.run", return_value=1)
    @patch("provisioner.__main__.handle_failure")
    @patch("provisioner.__main__.time.sleep")
    def test_env_override_retry_max(self, mock_sleep, mock_failure, mock_run, tmp_manifest, monkeypatch):
        """PROVISIONER_RETRY_MAX env var overrides manifest."""
        monkeypatch.setenv("PROVISIONER_RETRY_MAX", "2")
        path = tmp_manifest({"version": 1, "on_failure": {"max_retries": 5}})
        rc = run_with_retries(path)
        assert rc == 1
        assert mock_run.call_count == 2  # env override: 2, not manifest: 5


# ---------- Full dry-run pipeline ----------

class TestDryRunPipeline:
    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    def test_minimal_manifest(self, mock_civ, mock_hf, tmp_manifest):
        path = tmp_manifest({"version": 1})
        result = _load_and_run(path, dry_run=True)
        assert result == 0

    @patch("provisioner.__main__.validate_hf_token", return_value=True)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    def test_full_manifest_dry_run(self, mock_civ, mock_hf, tmp_manifest, full_manifest_data):
        path = tmp_manifest(full_manifest_data)
        result = _load_and_run(path, dry_run=True)
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
        result = _load_and_run(path, dry_run=True)
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
        result = _load_and_run(path, dry_run=True)
        assert result == 0

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    def test_env_merge_in_dry_run(self, mock_civ, mock_hf, tmp_manifest, monkeypatch):
        monkeypatch.setenv("EXTRA_MODELS", "https://example.com/a.bin|/d/a")
        path = tmp_manifest({
            "version": 1,
            "env_merge": {"EXTRA_MODELS": "downloads"},
        })
        result = _load_and_run(path, dry_run=True)
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
        result = _load_and_run(path, dry_run=True)
        assert result == 0

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    def test_post_commands_dry_run(self, mock_civ, mock_hf, tmp_manifest):
        path = tmp_manifest({
            "version": 1,
            "post_commands": ["echo hello", "echo world"],
        })
        result = _load_and_run(path, dry_run=True)
        assert result == 0

    def test_invalid_manifest_returns_1(self, tmp_manifest):
        path = tmp_manifest({"version": 2})
        result = run_with_retries(path, dry_run=True)
        assert result == 1

    def test_empty_manifest_returns_1(self, tmp_path):
        path = tmp_path / "empty.yaml"
        path.write_text("")
        result = run_with_retries(str(path), dry_run=True)
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
        result = _load_and_run(path, dry_run=True)
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
        result = _load_and_run(path, dry_run=True)
        assert result == 0

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    def test_old_dict_pip_packages_dry_run(self, mock_civ, mock_hf, tmp_manifest):
        """Old single-dict format still works via backward compat."""
        path = tmp_manifest({
            "version": 1,
            "pip_packages": {"packages": ["torch"], "tool": "uv"},
        })
        result = _load_and_run(path, dry_run=True)
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
        result = _load_and_run(path, dry_run=True)
        assert result == 0

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    def test_conda_packages_dry_run(self, mock_civ, mock_hf, tmp_manifest):
        path = tmp_manifest({
            "version": 1,
            "conda_packages": [
                {
                    "packages": ["numpy=1.24", "scipy>=1.10"],
                    "channels": ["conda-forge"],
                },
                {
                    "packages": ["pytorch"],
                    "env": "/venv/conda-gpu",
                },
            ],
        })
        result = _load_and_run(path, dry_run=True)
        assert result == 0

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    def test_old_dict_conda_packages_dry_run(self, mock_civ, mock_hf, tmp_manifest):
        """Old single-dict format still works via backward compat."""
        path = tmp_manifest({
            "version": 1,
            "conda_packages": {
                "packages": ["numpy=1.24"],
                "channels": ["conda-forge"],
            },
        })
        result = _load_and_run(path, dry_run=True)
        assert result == 0

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    def test_system_pip_dry_run(self, mock_civ, mock_hf, tmp_manifest):
        path = tmp_manifest({
            "version": 1,
            "pip_packages": [{"venv": "system", "packages": ["requests"]}],
        })
        result = _load_and_run(path, dry_run=True)
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
        result = _load_and_run(path, dry_run=True)
        assert result == 0

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    def test_on_failure_in_manifest(self, mock_civ, mock_hf, tmp_manifest):
        path = tmp_manifest({
            "version": 1,
            "on_failure": {"action": "destroy", "max_retries": 2, "retry_delay": 10},
        })
        result = _load_and_run(path, dry_run=True)
        assert result == 0


# ---------- Error handling behavior ----------

class TestErrorHandling:
    """Verify fail-fast semantics -- all phases abort on failure."""

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    @patch("provisioner.__main__.install_apt_packages", side_effect=RuntimeError("apt broke"))
    @patch("provisioner.__main__.clone_git_repos")
    @patch("provisioner.__main__.install_pip_packages")
    @patch("provisioner.__main__.register_services")
    def test_apt_failure_is_fatal(
        self, mock_svc, mock_pip, mock_git, mock_apt, mock_civ, mock_hf,
        tmp_manifest,
    ):
        """Phase 3 failure should abort -- phases 4-8 never run."""
        path = tmp_manifest({"version": 1, "apt_packages": ["bad-pkg"]})
        result = _load_and_run(path)
        assert result == 1
        mock_git.assert_not_called()
        mock_pip.assert_not_called()
        mock_svc.assert_not_called()

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    @patch("provisioner.__main__.install_apt_packages")
    @patch("provisioner.__main__.clone_git_repos", side_effect=RuntimeError("clone broke"))
    @patch("provisioner.__main__.install_pip_packages")
    @patch("provisioner.__main__.register_services")
    def test_git_failure_is_fatal(
        self, mock_svc, mock_pip, mock_git, mock_apt, mock_civ, mock_hf,
        tmp_manifest,
    ):
        """Phase 4 failure should abort -- phases 5-8 never run."""
        path = tmp_manifest({
            "version": 1,
            "git_repos": [{"url": "https://github.com/a/b", "dest": "/d"}],
        })
        result = _load_and_run(path)
        assert result == 1
        mock_pip.assert_not_called()
        mock_svc.assert_not_called()

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    @patch("provisioner.__main__.install_apt_packages")
    @patch("provisioner.__main__.clone_git_repos")
    @patch("provisioner.__main__.install_pip_packages", side_effect=RuntimeError("pip broke"))
    @patch("provisioner.__main__.register_services")
    def test_pip_failure_is_fatal(
        self, mock_svc, mock_pip, mock_git, mock_apt, mock_civ, mock_hf,
        tmp_manifest,
    ):
        """Phase 5 failure should abort -- phases 6-8 never run."""
        path = tmp_manifest({
            "version": 1,
            "pip_packages": [{"packages": ["bad-pkg"]}],
        })
        result = _load_and_run(path)
        assert result == 1
        mock_svc.assert_not_called()

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    @patch("provisioner.__main__.install_apt_packages")
    @patch("provisioner.__main__.clone_git_repos")
    @patch("provisioner.__main__.install_pip_packages")
    @patch("provisioner.__main__.run_parallel", return_value=[ValueError("download failed")])
    @patch("provisioner.__main__.register_services")
    def test_download_failure_is_fatal(
        self, mock_svc, mock_parallel, mock_pip, mock_git, mock_apt, mock_civ, mock_hf,
        tmp_manifest,
    ):
        """Phase 6 failure should abort -- phases 7-8 never run."""
        path = tmp_manifest({
            "version": 1,
            "downloads": [
                {"url": "https://example.com/f.bin", "dest": "/d/f"},
            ],
            "services": [{"name": "app", "command": "echo", "workdir": "/tmp"}],
        })
        result = _load_and_run(path)
        assert result == 1
        mock_svc.assert_not_called()

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    @patch("provisioner.__main__.install_apt_packages")
    @patch("provisioner.__main__.clone_git_repos")
    @patch("provisioner.__main__.install_pip_packages")
    @patch("provisioner.__main__.run_parallel", return_value=[ValueError("download failed")])
    @patch("provisioner.__main__.register_services")
    @patch("provisioner.__main__.subprocess.run")
    def test_download_failure_aborts_post_commands(
        self, mock_subproc, mock_svc, mock_parallel, mock_pip, mock_git, mock_apt,
        mock_civ, mock_hf, tmp_manifest,
    ):
        """Phase 6 failure should abort -- post_commands never run."""
        mock_subproc.return_value = MagicMock(returncode=0)
        path = tmp_manifest({
            "version": 1,
            "downloads": [
                {"url": "https://example.com/f.bin", "dest": "/d/f"},
            ],
            "post_commands": ["echo hello"],
        })
        result = _load_and_run(path)
        assert result == 1
        mock_subproc.assert_not_called()

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    @patch("provisioner.__main__.install_apt_packages")
    @patch("provisioner.__main__.run_extensions", side_effect=RuntimeError("extension broke"))
    @patch("provisioner.__main__.clone_git_repos")
    @patch("provisioner.__main__.install_pip_packages")
    @patch("provisioner.__main__.register_services")
    def test_extension_failure_is_fatal(
        self, mock_svc, mock_pip, mock_git, mock_ext, mock_apt, mock_civ, mock_hf,
        tmp_manifest,
    ):
        """Phase 1b failure should abort -- phases 2-8 never run."""
        path = tmp_manifest({
            "version": 1,
            "extensions": [{"module": "bad_ext", "config": {}}],
            "git_repos": [{"url": "https://github.com/a/b", "dest": "/d"}],
            "services": [{"name": "app", "command": "echo", "workdir": "/tmp"}],
        })
        result = _load_and_run(path)
        assert result == 1
        mock_git.assert_not_called()
        mock_pip.assert_not_called()
        mock_svc.assert_not_called()

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    @patch("provisioner.__main__.install_apt_packages")
    @patch("provisioner.__main__.clone_git_repos")
    @patch("provisioner.__main__.install_pip_packages")
    @patch("provisioner.__main__.register_services", side_effect=RuntimeError("supervisor broke"))
    @patch("provisioner.__main__.subprocess.run")
    def test_service_failure_is_fatal(
        self, mock_subproc, mock_svc, mock_pip, mock_git, mock_apt,
        mock_civ, mock_hf, tmp_manifest,
    ):
        """Phase 7 failure should abort -- post_commands never run."""
        mock_subproc.return_value = MagicMock(returncode=0)
        path = tmp_manifest({
            "version": 1,
            "services": [{"name": "app", "command": "echo", "workdir": "/tmp"}],
            "post_commands": ["echo hello"],
        })
        result = _load_and_run(path)
        assert result == 1
        mock_subproc.assert_not_called()

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    @patch("provisioner.__main__.install_apt_packages")
    @patch("provisioner.__main__.clone_git_repos")
    @patch("provisioner.__main__.install_pip_packages")
    @patch("provisioner.__main__.subprocess.run")
    def test_post_command_failure_is_fatal(
        self, mock_subproc, mock_pip, mock_git, mock_apt, mock_civ, mock_hf,
        tmp_manifest,
    ):
        """A failing post_command should abort -- later commands do not run."""
        mock_subproc.side_effect = [
            MagicMock(returncode=1),  # first command fails
            MagicMock(returncode=0),  # second command would succeed
        ]
        path = tmp_manifest({
            "version": 1,
            "post_commands": ["false", "echo hello"],
        })
        result = _load_and_run(path)
        assert result == 1
        assert mock_subproc.call_count == 1  # only first command ran

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
        result = _load_and_run(path)
        assert result == 0

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    @patch("provisioner.__main__.install_apt_packages")
    @patch("provisioner.__main__.clone_git_repos")
    @patch("provisioner.__main__.install_pip_packages")
    @patch("provisioner.__main__.subprocess.run")
    def test_post_command_output_captured(
        self, mock_subproc, mock_pip, mock_git, mock_apt, mock_civ, mock_hf,
        tmp_manifest,
    ):
        """Post commands capture stdout/stderr."""
        mock_subproc.return_value = MagicMock(returncode=0, stdout="hello\n", stderr="warn\n")
        path = tmp_manifest({
            "version": 1,
            "post_commands": ["echo hello"],
        })
        result = _load_and_run(path)
        assert result == 0
        mock_subproc.assert_called_once()
        call_kwargs = mock_subproc.call_args
        assert call_kwargs.kwargs.get("capture_output") is True
        assert call_kwargs.kwargs.get("text") is True


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
        result = _load_and_run(path)
        assert result == 0
        assert mock_apt.call_count == 1

        # Second run - apt should be skipped
        result = _load_and_run(path)
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
        result = _load_and_run(path)
        assert result == 0
        assert mock_apt.call_count == 1

        # Second run with force
        result = _load_and_run(path, force=True)
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

    def test_no_manifest_no_script_exits_0_cli(self):
        """Running without a manifest arg and no PROVISIONING_SCRIPT exits 0."""
        env = {**os.environ, "PYTHONPATH": _LIB_DIR}
        env.pop("PROVISIONING_SCRIPT", None)
        result = subprocess.run(
            [sys.executable, "-m", "provisioner"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "nothing to do" in result.stdout.lower()

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


# ---------- Phase 9: PROVISIONING_SCRIPT ----------

class TestProvisioningScript:
    """Tests for Phase 9 -- PROVISIONING_SCRIPT integration."""

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    def test_script_phase_dry_run_logs_intent(self, mock_civ, mock_hf, tmp_manifest, monkeypatch):
        """Dry run should log what it would do for PROVISIONING_SCRIPT."""
        monkeypatch.setenv("PROVISIONING_SCRIPT", "https://example.com/setup.sh")
        path = tmp_manifest({"version": 1})
        result = _load_and_run(path, dry_run=True)
        assert result == 0

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    @patch("provisioner.__main__.resolve_manifest_source", side_effect=RuntimeError("download failed"))
    def test_script_download_failure_is_fatal(self, mock_resolve, mock_civ, mock_hf, tmp_manifest, monkeypatch):
        """Script download failure should be fail-fast."""
        monkeypatch.setenv("PROVISIONING_SCRIPT", "https://example.com/bad.sh")
        path = tmp_manifest({"version": 1})
        manifest = load_manifest(path)
        result = run(path, manifest)
        assert result == 1

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    @patch("provisioner.__main__.resolve_manifest_source", return_value="/tmp/test_script.sh")
    @patch("provisioner.__main__.shutil.which", return_value=None)
    @patch("provisioner.__main__.os.chmod")
    @patch("provisioner.__main__.subprocess.run")
    def test_script_execution_failure_returns_1(
        self, mock_subproc, mock_chmod, mock_which, mock_resolve, mock_civ, mock_hf,
        tmp_manifest, monkeypatch,
    ):
        """Non-zero exit from the script should return 1."""
        monkeypatch.setenv("PROVISIONING_SCRIPT", "https://example.com/setup.sh")
        mock_subproc.return_value = MagicMock(returncode=1, stdout="", stderr="error\n")
        path = tmp_manifest({"version": 1})
        manifest = load_manifest(path)
        result = run(path, manifest)
        assert result == 1

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    @patch("provisioner.__main__.resolve_manifest_source", return_value="/tmp/test_script.sh")
    @patch("provisioner.__main__.shutil.which", return_value=None)
    @patch("provisioner.__main__.os.chmod")
    @patch("provisioner.__main__.subprocess.run")
    def test_script_output_captured(
        self, mock_subproc, mock_chmod, mock_which, mock_resolve, mock_civ, mock_hf,
        tmp_manifest, monkeypatch,
    ):
        """Script stdout/stderr should be captured."""
        monkeypatch.setenv("PROVISIONING_SCRIPT", "https://example.com/setup.sh")
        mock_subproc.return_value = MagicMock(returncode=0, stdout="hello\n", stderr="warn\n")
        path = tmp_manifest({"version": 1})
        manifest = load_manifest(path)
        result = run(path, manifest)
        assert result == 0
        # Verify subprocess was called with capture_output
        script_call = [c for c in mock_subproc.call_args_list if c.args and c.args[0] == ["/tmp/test_script.sh"]]
        assert len(script_call) == 1
        assert script_call[0].kwargs.get("capture_output") is True
        assert script_call[0].kwargs.get("text") is True

    @patch("provisioner.__main__.validate_hf_token", return_value=False)
    @patch("provisioner.__main__.validate_civitai_token", return_value=False)
    @patch("provisioner.__main__.resolve_manifest_source", return_value="/tmp/test_script.sh")
    @patch("provisioner.__main__.shutil.which", return_value=None)
    @patch("provisioner.__main__.os.chmod")
    @patch("provisioner.__main__.subprocess.run")
    def test_script_hash_idempotency(
        self, mock_subproc, mock_chmod, mock_which, mock_resolve, mock_civ, mock_hf,
        tmp_manifest, tmp_path, monkeypatch,
    ):
        """Second run with same PROVISIONING_SCRIPT should skip Phase 9."""
        monkeypatch.setenv("PROVISIONING_SCRIPT", "https://example.com/setup.sh")
        monkeypatch.setattr("provisioner.state.STATE_DIR", str(tmp_path / "state"))
        mock_subproc.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")

        path = tmp_manifest({"version": 1})
        manifest = load_manifest(path)

        # First run — script executes
        result = run(path, manifest)
        assert result == 0
        script_calls = [c for c in mock_subproc.call_args_list if c.args and c.args[0] == ["/tmp/test_script.sh"]]
        assert len(script_calls) == 1

        # Second run — script skipped (hash match)
        mock_subproc.reset_mock()
        result = run(path, manifest)
        assert result == 0
        script_calls = [c for c in mock_subproc.call_args_list if c.args and c.args[0] == ["/tmp/test_script.sh"]]
        assert len(script_calls) == 0

    def test_script_only_mode_no_manifest(self, monkeypatch):
        """Script-only mode (no manifest) should work when PROVISIONING_SCRIPT is set."""
        monkeypatch.setenv("PROVISIONING_SCRIPT", "https://example.com/setup.sh")
        with patch("provisioner.__main__.run", return_value=0) as mock_run, \
             patch("provisioner.__main__.notify_success"):
            rc = run_with_retries(None)
        assert rc == 0
        mock_run.assert_called_once()
        # manifest_path should be "(script-only)"
        assert mock_run.call_args.args[0] == "(script-only)"

    def test_no_manifest_no_script_returns_0(self, monkeypatch):
        """No manifest and no PROVISIONING_SCRIPT should return 0."""
        monkeypatch.delenv("PROVISIONING_SCRIPT", raising=False)
        rc = run_with_retries(None)
        assert rc == 0
