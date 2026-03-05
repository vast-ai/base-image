"""Tests for provisioner.manifest -- env expansion, YAML loading, env_merge, conditionals."""

from __future__ import annotations

import os

import pytest
import yaml

from unittest.mock import MagicMock, patch

from provisioner.manifest import (
    _expand_recursive,
    _is_url,
    _parse_env_merge_entries,
    apply_env_merge,
    expand_env,
    load_manifest,
    resolve_conditionals,
    resolve_manifest_source,
)
from provisioner.schema import DownloadEntry, validate_manifest


# ---------- expand_env ----------

class TestExpandEnv:
    def test_simple_var(self, monkeypatch):
        monkeypatch.setenv("MY_VAR", "hello")
        assert expand_env("${MY_VAR}") == "hello"

    def test_unset_var_empty(self):
        assert expand_env("${DEFINITELY_NOT_SET_12345}") == ""

    def test_default_with_colon_dash(self):
        assert expand_env("${UNSET_VAR:-fallback}") == "fallback"

    def test_default_with_colon_equals(self):
        assert expand_env("${UNSET_VAR:=fallback}") == "fallback"

    def test_set_var_ignores_default(self, monkeypatch):
        monkeypatch.setenv("SET_VAR", "real")
        assert expand_env("${SET_VAR:-default}") == "real"

    def test_empty_default(self):
        assert expand_env("${UNSET:-}") == ""

    def test_no_expansion(self):
        assert expand_env("no vars here") == "no vars here"

    def test_multiple_vars(self, monkeypatch):
        monkeypatch.setenv("A", "1")
        monkeypatch.setenv("B", "2")
        assert expand_env("${A}/${B}") == "1/2"

    def test_embedded_in_path(self, monkeypatch):
        monkeypatch.setenv("WS", "/workspace")
        assert expand_env("${WS}/models/file.bin") == "/workspace/models/file.bin"

    def test_default_with_slashes(self):
        result = expand_env("${UNSET:-/opt/default/path}")
        assert result == "/opt/default/path"

    def test_adjacent_vars(self, monkeypatch):
        monkeypatch.setenv("X", "a")
        monkeypatch.setenv("Y", "b")
        assert expand_env("${X}${Y}") == "ab"


# ---------- _expand_recursive ----------

class TestExpandRecursive:
    def test_string(self, monkeypatch):
        monkeypatch.setenv("V", "val")
        assert _expand_recursive("${V}") == "val"

    def test_dict(self, monkeypatch):
        monkeypatch.setenv("V", "val")
        result = _expand_recursive({"key": "${V}", "num": 42})
        assert result == {"key": "val", "num": 42}

    def test_list(self, monkeypatch):
        monkeypatch.setenv("V", "val")
        result = _expand_recursive(["${V}", "literal"])
        assert result == ["val", "literal"]

    def test_nested(self, monkeypatch):
        monkeypatch.setenv("V", "val")
        result = _expand_recursive({"outer": {"inner": "${V}"}})
        assert result == {"outer": {"inner": "val"}}

    def test_non_string_passthrough(self):
        assert _expand_recursive(42) == 42
        assert _expand_recursive(True) is True
        assert _expand_recursive(None) is None


# ---------- _parse_env_merge_entries ----------

class TestParseEnvMergeEntries:
    def test_basic(self):
        entries = _parse_env_merge_entries("https://a.com/f|/d/f;https://b.com/g|/d/g")
        assert len(entries) == 2
        assert entries[0].url == "https://a.com/f"
        assert entries[0].dest == "/d/f"
        assert entries[1].url == "https://b.com/g"
        assert entries[1].dest == "/d/g"

    def test_single_entry(self):
        entries = _parse_env_merge_entries("https://a.com/f|/d/f")
        assert len(entries) == 1

    def test_whitespace_trimmed(self):
        entries = _parse_env_merge_entries("  https://a.com/f | /d/f  ")
        assert entries[0].url == "https://a.com/f"
        assert entries[0].dest == "/d/f"

    def test_empty_string(self):
        assert _parse_env_merge_entries("") == []

    def test_comments_skipped(self):
        entries = _parse_env_merge_entries("#comment;https://a.com/f|/d/f;#another")
        assert len(entries) == 1

    def test_empty_entries_skipped(self):
        entries = _parse_env_merge_entries(";;https://a.com/f|/d/f;;")
        assert len(entries) == 1

    def test_no_pipe_skipped(self):
        entries = _parse_env_merge_entries("https://a.com/f")
        assert len(entries) == 0

    def test_pipe_in_url(self):
        """Only first pipe splits url|dest."""
        entries = _parse_env_merge_entries("https://a.com/f?a=1|/d/f")
        assert entries[0].url == "https://a.com/f?a=1"
        assert entries[0].dest == "/d/f"

    def test_multiline_entry(self):
        """Entries can have leading/trailing whitespace from multiline definitions."""
        entries = _parse_env_merge_entries(
            "\n  https://huggingface.co/org/repo/resolve/main/file.bin\n"
            "  | /workspace/models/file.bin  \n"
        )
        assert len(entries) == 1
        assert "huggingface.co" in entries[0].url


# ---------- apply_env_merge ----------

class TestApplyEnvMerge:
    def test_merges_env_var(self, monkeypatch):
        monkeypatch.setenv("MY_MODELS", "https://a.com/f|/d/f;https://b.com/g|/d/g")
        m = validate_manifest({
            "version": 1,
            "downloads": [{"url": "https://existing.com/x", "dest": "/d/x"}],
            "env_merge": {"MY_MODELS": "downloads"},
        })
        apply_env_merge(m)
        assert len(m.downloads) == 3

    def test_empty_env_var_no_change(self):
        m = validate_manifest({
            "version": 1,
            "downloads": [{"url": "https://existing.com/x", "dest": "/d/x"}],
            "env_merge": {"NONEXISTENT_VAR": "downloads"},
        })
        apply_env_merge(m)
        assert len(m.downloads) == 1

    def test_unsupported_target_warns(self, monkeypatch, caplog):
        monkeypatch.setenv("MY_VAR", "https://a.com/f|/d/f")
        m = validate_manifest({
            "version": 1,
            "env_merge": {"MY_VAR": "something_else"},
        })
        import logging
        with caplog.at_level(logging.WARNING, logger="provisioner"):
            apply_env_merge(m)
        assert "not supported" in caplog.text

    def test_multiple_env_vars(self, monkeypatch):
        monkeypatch.setenv("HF_MODELS", "https://hf.co/a|/d/a")
        monkeypatch.setenv("WGET_DOWNLOADS", "https://example.com/b|/d/b")
        m = validate_manifest({
            "version": 1,
            "env_merge": {
                "HF_MODELS": "downloads",
                "WGET_DOWNLOADS": "downloads",
            },
        })
        apply_env_merge(m)
        assert len(m.downloads) == 2


# ---------- resolve_conditionals ----------

class TestResolveConditionals:
    def test_hf_token_valid_true(self):
        m = validate_manifest({
            "version": 1,
            "conditional_downloads": [{
                "when": "hf_token_valid",
                "downloads": [{"url": "https://a", "dest": "/a"}],
                "else_downloads": [{"url": "https://b", "dest": "/b"}],
            }],
        })
        resolve_conditionals(m, hf_token_valid=True)
        assert len(m.downloads) == 1
        assert m.downloads[0].url == "https://a"
        assert m.conditional_downloads == []

    def test_hf_token_valid_false(self):
        m = validate_manifest({
            "version": 1,
            "conditional_downloads": [{
                "when": "hf_token_valid",
                "downloads": [{"url": "https://a", "dest": "/a"}],
                "else_downloads": [{"url": "https://b", "dest": "/b"}],
            }],
        })
        resolve_conditionals(m, hf_token_valid=False)
        assert len(m.downloads) == 1
        assert m.downloads[0].url == "https://b"

    def test_appends_to_existing_downloads(self):
        m = validate_manifest({
            "version": 1,
            "downloads": [{"url": "https://existing", "dest": "/e"}],
            "conditional_downloads": [{
                "when": "hf_token_valid",
                "downloads": [{"url": "https://a", "dest": "/a"}],
                "else_downloads": [],
            }],
        })
        resolve_conditionals(m, hf_token_valid=True)
        assert len(m.downloads) == 2

    def test_unknown_condition_warns(self, caplog):
        m = validate_manifest({
            "version": 1,
            "conditional_downloads": [{
                "when": "unknown_condition",
                "downloads": [{"url": "https://a", "dest": "/a"}],
                "else_downloads": [],
            }],
        })
        import logging
        with caplog.at_level(logging.WARNING, logger="provisioner"):
            resolve_conditionals(m, hf_token_valid=True)
        assert "Unknown condition" in caplog.text
        assert len(m.downloads) == 0

    def test_multiple_conditionals(self):
        m = validate_manifest({
            "version": 1,
            "conditional_downloads": [
                {
                    "when": "hf_token_valid",
                    "downloads": [{"url": "https://a", "dest": "/a"}],
                    "else_downloads": [{"url": "https://b", "dest": "/b"}],
                },
                {
                    "when": "hf_token_valid",
                    "downloads": [{"url": "https://c", "dest": "/c"}],
                    "else_downloads": [{"url": "https://d", "dest": "/d"}],
                },
            ],
        })
        resolve_conditionals(m, hf_token_valid=True)
        assert len(m.downloads) == 2
        urls = {d.url for d in m.downloads}
        assert urls == {"https://a", "https://c"}

    def test_empty_else_downloads(self):
        m = validate_manifest({
            "version": 1,
            "conditional_downloads": [{
                "when": "hf_token_valid",
                "downloads": [{"url": "https://a", "dest": "/a"}],
                "else_downloads": [],
            }],
        })
        resolve_conditionals(m, hf_token_valid=False)
        assert len(m.downloads) == 0


# ---------- load_manifest ----------

class TestLoadManifest:
    def test_loads_minimal(self, tmp_manifest, minimal_manifest_data):
        path = tmp_manifest(minimal_manifest_data)
        m = load_manifest(path)
        assert m.version == 1

    def test_loads_full(self, tmp_manifest, full_manifest_data):
        path = tmp_manifest(full_manifest_data)
        m = load_manifest(path)
        assert m.settings.concurrency.hf_downloads == 2
        assert len(m.services) == 1

    def test_expands_env_vars(self, tmp_manifest, monkeypatch):
        monkeypatch.setenv("MY_VENV", "/venv/custom")
        path = tmp_manifest({
            "version": 1,
            "settings": {"venv": "${MY_VENV}"},
        })
        m = load_manifest(path)
        assert m.settings.venv == "/venv/custom"

    def test_expands_defaults(self, tmp_manifest):
        path = tmp_manifest({
            "version": 1,
            "settings": {"venv": "${UNSET_12345:-/venv/fallback}"},
        })
        m = load_manifest(path)
        assert m.settings.venv == "/venv/fallback"

    def test_empty_file_raises(self, tmp_path):
        path = tmp_path / "empty.yaml"
        path.write_text("")
        with pytest.raises(ValueError, match="Empty"):
            load_manifest(str(path))

    def test_invalid_yaml_raises(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text(":::not yaml:::")
        with pytest.raises(Exception):
            load_manifest(str(path))

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_manifest("/nonexistent/path/manifest.yaml")

    def test_env_expansion_in_downloads(self, tmp_manifest, monkeypatch):
        monkeypatch.setenv("MODEL_DIR", "/workspace/models")
        path = tmp_manifest({
            "version": 1,
            "downloads": [{
                "url": "https://huggingface.co/org/repo/resolve/main/f.bin",
                "dest": "${MODEL_DIR}/f.bin",
            }],
        })
        m = load_manifest(path)
        assert m.downloads[0].dest == "/workspace/models/f.bin"

    def test_env_expansion_in_services(self, tmp_manifest, monkeypatch):
        monkeypatch.setenv("APP_DIR", "/workspace/app")
        path = tmp_manifest({
            "version": 1,
            "services": [{
                "name": "svc",
                "command": "python main.py",
                "workdir": "${APP_DIR}",
            }],
        })
        m = load_manifest(path)
        assert m.services[0].workdir == "/workspace/app"


# ---------- _is_url ----------

class TestIsUrl:
    def test_https(self):
        assert _is_url("https://example.com/manifest.yaml") is True

    def test_http(self):
        assert _is_url("http://example.com/manifest.yaml") is True

    def test_file_path(self):
        assert _is_url("/path/to/manifest.yaml") is False

    def test_relative_path(self):
        assert _is_url("manifest.yaml") is False

    def test_empty(self):
        assert _is_url("") is False


# ---------- resolve_manifest_source ----------

class TestResolveManifestSource:
    def test_local_path_returned_unchanged(self):
        result = resolve_manifest_source("/path/to/manifest.yaml")
        assert result == "/path/to/manifest.yaml"

    def test_relative_path_returned_unchanged(self):
        result = resolve_manifest_source("manifest.yaml")
        assert result == "manifest.yaml"

    @patch("provisioner.manifest.urllib.request.urlopen")
    def test_url_downloaded_to_cache_path(self, mock_urlopen, tmp_path):
        yaml_content = b"version: 1\napt_packages:\n  - vim\n"
        mock_resp = MagicMock()
        mock_resp.read.return_value = yaml_content
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        cache = str(tmp_path / "manifest.yaml")
        result = resolve_manifest_source("https://example.com/manifest.yaml", cache_path=cache)

        assert result == cache
        assert os.path.isfile(cache)
        with open(cache) as f:
            assert f.read() == yaml_content.decode()

    @patch("provisioner.manifest.urllib.request.urlopen")
    def test_url_download_failure_raises_runtime_error(self, mock_urlopen, tmp_path):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("connection refused")

        cache = str(tmp_path / "manifest.yaml")
        with pytest.raises(RuntimeError, match="Failed to download manifest"):
            resolve_manifest_source("https://example.com/manifest.yaml", cache_path=cache)

    @patch("provisioner.manifest.urllib.request.urlopen")
    def test_url_http_error_raises_runtime_error(self, mock_urlopen, tmp_path):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://example.com/manifest.yaml", 404, "Not Found", {}, None,
        )

        cache = str(tmp_path / "manifest.yaml")
        with pytest.raises(RuntimeError, match="Failed to download manifest"):
            resolve_manifest_source("https://example.com/manifest.yaml", cache_path=cache)

    @patch("provisioner.manifest.urllib.request.urlopen")
    def test_downloaded_manifest_is_loadable(self, mock_urlopen, tmp_path):
        """End-to-end: download URL then load_manifest on the result."""
        yaml_content = b"version: 1\napt_packages:\n  - curl\n"
        mock_resp = MagicMock()
        mock_resp.read.return_value = yaml_content
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        cache = str(tmp_path / "manifest.yaml")
        path = resolve_manifest_source("https://example.com/manifest.yaml", cache_path=cache)
        m = load_manifest(path)
        assert m.apt_packages == ["curl"]

    @patch("provisioner.manifest.urllib.request.urlopen")
    def test_write_failure_raises_runtime_error(self, mock_urlopen):
        yaml_content = b"version: 1\n"
        mock_resp = MagicMock()
        mock_resp.read.return_value = yaml_content
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        with pytest.raises(RuntimeError, match="Failed to write manifest"):
            resolve_manifest_source(
                "https://example.com/manifest.yaml",
                cache_path="/nonexistent_dir/sub/manifest.yaml",
            )
