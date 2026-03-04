"""Tests for provisioner.schema -- dataclass construction and validation."""

from __future__ import annotations

import pytest

from provisioner.schema import (
    Auth,
    AuthProvider,
    ConcurrencySettings,
    ConditionalDownload,
    DownloadEntry,
    GitRepo,
    Manifest,
    PipPackages,
    RetrySettings,
    Service,
    Settings,
    _build_nested,
    validate_manifest,
)


# ---------- validate_manifest ----------

class TestValidateManifest:
    def test_minimal(self):
        m = validate_manifest({"version": 1})
        assert isinstance(m, Manifest)
        assert m.version == 1

    def test_rejects_wrong_version(self):
        with pytest.raises(ValueError, match="version"):
            validate_manifest({"version": 2})

    def test_rejects_missing_version(self):
        with pytest.raises(ValueError, match="version"):
            validate_manifest({})

    def test_rejects_non_dict(self):
        with pytest.raises(ValueError, match="mapping"):
            validate_manifest("not a dict")

    def test_rejects_none_version(self):
        with pytest.raises(ValueError, match="version"):
            validate_manifest({"version": None})


# ---------- Default values ----------

class TestDefaults:
    def test_settings_defaults(self):
        m = validate_manifest({"version": 1})
        assert m.settings.workspace == "/workspace"
        assert m.settings.venv == "/venv/main"
        assert m.settings.log_file == "/var/log/portal/provisioning.log"

    def test_concurrency_defaults(self):
        m = validate_manifest({"version": 1})
        assert m.settings.concurrency.hf_downloads == 3
        assert m.settings.concurrency.wget_downloads == 5

    def test_retry_defaults(self):
        m = validate_manifest({"version": 1})
        assert m.settings.retry.max_attempts == 5
        assert m.settings.retry.initial_delay == 2
        assert m.settings.retry.backoff_multiplier == 2

    def test_auth_defaults(self):
        m = validate_manifest({"version": 1})
        assert m.auth.huggingface.token_env == "HF_TOKEN"
        assert m.auth.civitai.token_env == "CIVITAI_TOKEN"

    def test_pip_packages_defaults(self):
        m = validate_manifest({"version": 1})
        assert m.pip_packages.tool == "uv"
        assert m.pip_packages.packages == []
        assert m.pip_packages.args == ""
        assert m.pip_packages.requirements == []

    def test_empty_lists_default(self):
        m = validate_manifest({"version": 1})
        assert m.apt_packages == []
        assert m.git_repos == []
        assert m.downloads == []
        assert m.conditional_downloads == []
        assert m.services == []
        assert m.post_commands == []

    def test_service_defaults(self):
        s = Service()
        assert s.skip_on_serverless is True
        assert s.venv == "/venv/main"
        assert s.wait_for_provisioning is True
        assert s.environment == {}

    def test_git_repo_defaults(self):
        g = GitRepo()
        assert g.recursive is True
        assert g.pull_if_exists is False
        assert g.pip_install_editable is False


# ---------- Nested construction ----------

class TestBuildNested:
    def test_settings_override(self):
        m = validate_manifest({
            "version": 1,
            "settings": {
                "workspace": "/my/workspace",
                "concurrency": {"hf_downloads": 10},
            },
        })
        assert m.settings.workspace == "/my/workspace"
        assert m.settings.concurrency.hf_downloads == 10
        # Non-overridden fields keep defaults
        assert m.settings.concurrency.wget_downloads == 5
        assert m.settings.venv == "/venv/main"

    def test_downloads_list(self):
        m = validate_manifest({
            "version": 1,
            "downloads": [
                {"url": "https://example.com/a", "dest": "/d/a"},
                {"url": "https://example.com/b", "dest": "/d/b"},
            ],
        })
        assert len(m.downloads) == 2
        assert isinstance(m.downloads[0], DownloadEntry)
        assert m.downloads[0].url == "https://example.com/a"
        assert m.downloads[1].dest == "/d/b"

    def test_git_repos_list(self):
        m = validate_manifest({
            "version": 1,
            "git_repos": [
                {"url": "https://github.com/a/b", "dest": "/d", "recursive": False},
            ],
        })
        assert len(m.git_repos) == 1
        assert isinstance(m.git_repos[0], GitRepo)
        assert m.git_repos[0].recursive is False

    def test_services_with_environment(self):
        m = validate_manifest({
            "version": 1,
            "services": [{
                "name": "svc",
                "command": "echo hi",
                "workdir": "/tmp",
                "environment": {"FOO": "bar", "BAZ": "qux"},
            }],
        })
        assert m.services[0].environment == {"FOO": "bar", "BAZ": "qux"}

    def test_conditional_downloads(self):
        m = validate_manifest({
            "version": 1,
            "conditional_downloads": [{
                "when": "hf_token_valid",
                "downloads": [{"url": "https://a", "dest": "/a"}],
                "else_downloads": [{"url": "https://b", "dest": "/b"}],
            }],
        })
        assert len(m.conditional_downloads) == 1
        assert isinstance(m.conditional_downloads[0], ConditionalDownload)
        assert len(m.conditional_downloads[0].downloads) == 1
        assert len(m.conditional_downloads[0].else_downloads) == 1

    def test_ignores_unknown_keys(self):
        """Unknown top-level or nested keys should not cause errors."""
        m = validate_manifest({
            "version": 1,
            "unknown_key": "ignored",
            "settings": {"workspace": "/w", "also_unknown": True},
        })
        assert m.settings.workspace == "/w"

    def test_pip_packages_full(self):
        m = validate_manifest({
            "version": 1,
            "pip_packages": {
                "tool": "pip",
                "packages": ["numpy", "scipy"],
                "args": "--no-cache-dir",
                "requirements": ["/a/req.txt", "/b/req.txt"],
            },
        })
        assert m.pip_packages.tool == "pip"
        assert m.pip_packages.packages == ["numpy", "scipy"]
        assert m.pip_packages.args == "--no-cache-dir"
        assert len(m.pip_packages.requirements) == 2


# ---------- Full manifest roundtrip ----------

class TestFullManifest:
    def test_full_manifest(self, full_manifest_data):
        m = validate_manifest(full_manifest_data)
        assert m.version == 1
        assert m.settings.workspace == "/workspace"
        assert m.settings.concurrency.hf_downloads == 2
        assert m.settings.retry.max_attempts == 3
        assert len(m.apt_packages) == 2
        assert m.pip_packages.tool == "uv"
        assert len(m.pip_packages.packages) == 2
        assert len(m.git_repos) == 1
        assert len(m.downloads) == 3
        assert len(m.conditional_downloads) == 1
        assert m.env_merge == {"HF_MODELS": "downloads"}
        assert len(m.services) == 1
        assert m.services[0].name == "test-app"
        assert len(m.post_commands) == 1
