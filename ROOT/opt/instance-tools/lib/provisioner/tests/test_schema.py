"""Tests for provisioner.schema -- dataclass construction and validation."""

from __future__ import annotations

import pytest

from provisioner.schema import (
    Auth,
    AuthProvider,
    ConcurrencySettings,
    CondaPackages,
    ConditionalDownload,
    DownloadEntry,
    Extension,
    FileWrite,
    GitRepo,
    Manifest,
    OnFailure,
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
        assert m.pip_packages == []

    def test_file_write_defaults(self):
        f = FileWrite()
        assert f.path == ""
        assert f.content == ""
        assert f.permissions == "0644"
        assert f.owner == ""

    def test_extension_defaults(self):
        e = Extension()
        assert e.module == ""
        assert e.config == {}
        assert e.enabled is True

    def test_empty_lists_default(self):
        m = validate_manifest({"version": 1})
        assert m.apt_packages == []
        assert m.git_repos == []
        assert m.downloads == []
        assert m.conditional_downloads == []
        assert m.extensions == []
        assert m.services == []
        assert m.post_commands == []
        assert m.write_files == []
        assert m.write_files_late == []

    def test_on_failure_defaults(self):
        m = validate_manifest({"version": 1})
        assert m.on_failure.action == "continue"
        assert m.on_failure.max_retries == 3
        assert m.on_failure.webhook == ""

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
        assert g.post_commands == []

    def test_pip_packages_block_defaults(self):
        p = PipPackages()
        assert p.venv == ""
        assert p.python == ""
        assert p.tool == "uv"

    def test_conda_packages_defaults(self):
        m = validate_manifest({"version": 1})
        assert m.conda_packages.packages == []
        assert m.conda_packages.channels == []
        assert m.conda_packages.args == ""


# ---------- Nested construction ----------

class TestBuildNested:
    def test_settings_override(self):
        m = validate_manifest({
            "version": 1,
            "settings": {
                "concurrency": {"hf_downloads": 10},
            },
        })
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
            "settings": {"venv": "/venv/custom", "also_unknown": True},
        })
        assert m.settings.venv == "/venv/custom"

    def test_pip_packages_list_format(self):
        m = validate_manifest({
            "version": 1,
            "pip_packages": [
                {
                    "tool": "pip",
                    "packages": ["numpy", "scipy"],
                    "args": "--no-cache-dir",
                    "requirements": ["/a/req.txt", "/b/req.txt"],
                    "venv": "/venv/custom",
                    "python": "3.11",
                },
                {
                    "packages": ["torch"],
                },
            ],
        })
        assert len(m.pip_packages) == 2
        assert m.pip_packages[0].tool == "pip"
        assert m.pip_packages[0].packages == ["numpy", "scipy"]
        assert m.pip_packages[0].venv == "/venv/custom"
        assert m.pip_packages[0].python == "3.11"
        assert m.pip_packages[1].packages == ["torch"]
        assert m.pip_packages[1].venv == ""

    def test_on_failure_construction(self):
        m = validate_manifest({
            "version": 1,
            "on_failure": {
                "action": "retry",
                "max_retries": 5,
                "webhook": "https://hooks.example.com/fail",
            },
        })
        assert m.on_failure.action == "retry"
        assert m.on_failure.max_retries == 5
        assert m.on_failure.webhook == "https://hooks.example.com/fail"

    def test_write_files_construction(self):
        m = validate_manifest({
            "version": 1,
            "write_files": [
                {"path": "/etc/app.conf", "content": "key=val\n", "permissions": "0600", "owner": "root:root"},
            ],
            "write_files_late": [
                {"path": "/tmp/done.txt", "content": "ok"},
            ],
        })
        assert len(m.write_files) == 1
        assert isinstance(m.write_files[0], FileWrite)
        assert m.write_files[0].path == "/etc/app.conf"
        assert m.write_files[0].permissions == "0600"
        assert m.write_files[0].owner == "root:root"
        assert len(m.write_files_late) == 1
        assert m.write_files_late[0].permissions == "0644"  # default

    def test_extensions_construction(self):
        m = validate_manifest({
            "version": 1,
            "extensions": [
                {"module": "provisioner_comfyui", "config": {"workflows": ["a.json"]}, "enabled": True},
                {"module": "provisioner_other", "enabled": False},
            ],
        })
        assert len(m.extensions) == 2
        assert isinstance(m.extensions[0], Extension)
        assert m.extensions[0].module == "provisioner_comfyui"
        assert m.extensions[0].config == {"workflows": ["a.json"]}
        assert m.extensions[0].enabled is True
        assert m.extensions[1].module == "provisioner_other"
        assert m.extensions[1].enabled is False
        assert m.extensions[1].config == {}

    def test_extensions_arbitrary_nested_config(self):
        """Arbitrary nested dicts in config pass through unchanged."""
        m = validate_manifest({
            "version": 1,
            "extensions": [{
                "module": "my_ext",
                "config": {
                    "workflows": ["https://example.com/wf.json"],
                    "options": {"resolution": "1024x1024", "steps": 20},
                },
            }],
        })
        assert m.extensions[0].config["options"]["steps"] == 20
        assert m.extensions[0].config["workflows"] == ["https://example.com/wf.json"]

    def test_conda_packages_construction(self):
        m = validate_manifest({
            "version": 1,
            "conda_packages": {
                "packages": ["numpy=1.24", "scipy>=1.10"],
                "channels": ["conda-forge", "nvidia"],
                "args": "--no-update-deps",
            },
        })
        assert m.conda_packages.packages == ["numpy=1.24", "scipy>=1.10"]
        assert m.conda_packages.channels == ["conda-forge", "nvidia"]
        assert m.conda_packages.args == "--no-update-deps"


# ---------- Backward compatibility ----------

class TestBackwardCompat:
    def test_pip_packages_dict_wrapped_in_list(self):
        """Old single-dict pip_packages format is auto-wrapped in a list."""
        m = validate_manifest({
            "version": 1,
            "pip_packages": {
                "tool": "pip",
                "packages": ["numpy", "scipy"],
                "args": "--no-cache-dir",
                "requirements": ["/a/req.txt", "/b/req.txt"],
            },
        })
        assert isinstance(m.pip_packages, list)
        assert len(m.pip_packages) == 1
        assert m.pip_packages[0].tool == "pip"
        assert m.pip_packages[0].packages == ["numpy", "scipy"]
        assert m.pip_packages[0].args == "--no-cache-dir"
        assert len(m.pip_packages[0].requirements) == 2

    def test_pip_packages_dict_with_venv_fields(self):
        """Old format doesn't have venv/python, they default to empty."""
        m = validate_manifest({
            "version": 1,
            "pip_packages": {"packages": ["torch"]},
        })
        assert m.pip_packages[0].venv == ""
        assert m.pip_packages[0].python == ""


# ---------- Full manifest roundtrip ----------

class TestFullManifest:
    def test_full_manifest(self, full_manifest_data):
        m = validate_manifest(full_manifest_data)
        assert m.version == 1
        assert m.settings.concurrency.hf_downloads == 2
        assert m.settings.retry.max_attempts == 3
        assert len(m.apt_packages) == 2
        assert len(m.pip_packages) == 1
        assert m.pip_packages[0].tool == "uv"
        assert len(m.pip_packages[0].packages) == 2
        assert len(m.git_repos) == 1
        assert len(m.downloads) == 3
        assert len(m.conditional_downloads) == 1
        assert m.env_merge == {"HF_MODELS": "downloads"}
        assert len(m.extensions) == 1
        assert m.extensions[0].module == "provisioner_example"
        assert m.extensions[0].config == {"key": "value", "nested": {"a": 1}}
        assert len(m.services) == 1
        assert m.services[0].name == "test-app"
        assert len(m.write_files) == 1
        assert m.write_files[0].path == "/tmp/early.conf"
        assert len(m.write_files_late) == 1
        assert m.write_files_late[0].permissions == "0600"
        assert len(m.post_commands) == 1
