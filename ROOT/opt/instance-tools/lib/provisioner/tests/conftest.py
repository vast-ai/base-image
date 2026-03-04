"""Shared fixtures for provisioner tests."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest
import yaml


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure tests don't leak env vars and don't have stale logger handlers."""
    # Remove tokens so auth tests are predictable
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("CIVITAI_TOKEN", raising=False)

    # Clean up provisioner logger handlers between tests
    import logging
    logger = logging.getLogger("provisioner")
    logger.handlers.clear()


@pytest.fixture
def tmp_manifest(tmp_path):
    """Factory fixture: write a dict as YAML to a temp file and return the path."""
    def _write(data: dict) -> str:
        path = tmp_path / "manifest.yaml"
        path.write_text(yaml.dump(data, default_flow_style=False))
        return str(path)
    return _write


@pytest.fixture
def minimal_manifest_data():
    """The smallest valid manifest."""
    return {"version": 1}


@pytest.fixture
def full_manifest_data():
    """A manifest exercising every section."""
    return {
        "version": 1,
        "settings": {
            "workspace": "/workspace",
            "venv": "/venv/main",
            "log_file": "/tmp/test_provisioner.log",
            "concurrency": {"hf_downloads": 2, "wget_downloads": 4},
            "retry": {"max_attempts": 3, "initial_delay": 1, "backoff_multiplier": 2},
        },
        "auth": {
            "huggingface": {"token_env": "HF_TOKEN"},
            "civitai": {"token_env": "CIVITAI_TOKEN"},
        },
        "apt_packages": ["vim", "curl"],
        "pip_packages": {
            "tool": "uv",
            "packages": ["torch", "torchaudio"],
            "args": "--extra-index-url https://example.com",
            "requirements": ["/workspace/app/requirements.txt"],
        },
        "git_repos": [
            {
                "url": "https://github.com/example/repo",
                "dest": "/workspace/repo",
                "ref": "main",
                "recursive": True,
                "pull_if_exists": False,
                "requirements": "requirements.txt",
                "pip_install_editable": False,
            }
        ],
        "downloads": [
            {
                "url": "https://huggingface.co/org/model/resolve/main/weights.safetensors",
                "dest": "/workspace/models/weights.safetensors",
            },
            {
                "url": "https://civitai.com/api/download/models/12345",
                "dest": "/workspace/models/Lora/",
            },
            {
                "url": "https://example.com/file.bin",
                "dest": "/workspace/other/file.bin",
            },
        ],
        "conditional_downloads": [
            {
                "when": "hf_token_valid",
                "downloads": [
                    {
                        "url": "https://huggingface.co/org/gated/resolve/main/gated.bin",
                        "dest": "/workspace/models/gated.bin",
                    }
                ],
                "else_downloads": [
                    {
                        "url": "https://huggingface.co/org/open/resolve/main/open.bin",
                        "dest": "/workspace/models/open.bin",
                    }
                ],
            }
        ],
        "env_merge": {"HF_MODELS": "downloads"},
        "services": [
            {
                "name": "test-app",
                "portal_search_term": "Test App",
                "skip_on_serverless": True,
                "venv": "/venv/main",
                "workdir": "/workspace/test-app",
                "command": "python app.py --port 7860",
                "wait_for_provisioning": True,
                "environment": {"GRADIO_SERVER_NAME": "127.0.0.1"},
            }
        ],
        "post_commands": ["echo done"],
    }
