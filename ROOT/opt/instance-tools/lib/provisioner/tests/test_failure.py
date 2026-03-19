"""Tests for provisioner.failure -- failure actions, webhook, stop-once sentinel."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from provisioner.failure import (
    STOP_SENTINEL,
    _call_webhook,
    _vastai_command,
    handle_failure,
    notify_success,
)
from provisioner.schema import OnFailure


class TestWebhook:
    @patch("provisioner.failure.urllib.request.urlopen")
    def test_call_webhook_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        _call_webhook("https://hooks.example.com", {"test": "data"})
        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "https://hooks.example.com"
        assert json.loads(req.data) == {"test": "data"}

    @patch("provisioner.failure.urllib.request.urlopen", side_effect=Exception("network error"))
    def test_call_webhook_failure_does_not_raise(self, mock_urlopen):
        # Should log warning but not raise
        _call_webhook("https://hooks.example.com", {"test": "data"})


class TestVastaiCommand:
    @patch("provisioner.failure.subprocess.run")
    def test_destroy_command(self, mock_run, monkeypatch):
        monkeypatch.setenv("CONTAINER_ID", "12345")
        monkeypatch.setenv("CONTAINER_API_KEY", "key123")
        _vastai_command("destroy")
        cmd = mock_run.call_args[0][0]
        assert cmd == ["vastai", "destroy", "instance", "12345", "--api-key", "key123"]

    @patch("provisioner.failure.subprocess.run")
    def test_stop_command(self, mock_run, monkeypatch):
        monkeypatch.setenv("CONTAINER_ID", "12345")
        monkeypatch.setenv("CONTAINER_API_KEY", "key123")
        _vastai_command("stop")
        cmd = mock_run.call_args[0][0]
        assert cmd == ["vastai", "stop", "instance", "12345", "--api-key", "key123"]

    def test_missing_container_id(self, monkeypatch):
        monkeypatch.delenv("CONTAINER_ID", raising=False)
        # Should not raise, just log error
        _vastai_command("destroy")

    def test_missing_api_key(self, monkeypatch):
        monkeypatch.setenv("CONTAINER_ID", "12345")
        monkeypatch.delenv("CONTAINER_API_KEY", raising=False)
        _vastai_command("destroy")


class TestHandleFailure:
    def test_continue_action(self):
        on_failure = OnFailure(action="continue")
        # Should not raise
        handle_failure(on_failure, "/path/manifest.yaml")

    @patch("provisioner.failure._call_webhook")
    def test_webhook_from_manifest(self, mock_webhook):
        on_failure = OnFailure(action="continue", webhook="https://hooks.example.com")
        handle_failure(on_failure, "/path/manifest.yaml", error="test error")
        mock_webhook.assert_called_once()
        payload = mock_webhook.call_args[0][1]
        assert payload["action"] == "continue"
        assert payload["error"] == "test error"
        assert payload["manifest"] == "/path/manifest.yaml"

    @patch("provisioner.failure._call_webhook")
    def test_webhook_env_overrides_manifest(self, mock_webhook, monkeypatch):
        monkeypatch.setenv("PROVISIONER_WEBHOOK_URL", "https://env-hook.example.com")
        on_failure = OnFailure(action="continue", webhook="https://manifest-hook.example.com")
        handle_failure(on_failure, "/path/manifest.yaml")
        url = mock_webhook.call_args[0][0]
        assert url == "https://env-hook.example.com"

    @patch("provisioner.failure._call_webhook")
    def test_no_webhook_when_not_configured(self, mock_webhook):
        on_failure = OnFailure(action="continue")
        handle_failure(on_failure, "/path/manifest.yaml")
        mock_webhook.assert_not_called()

    @patch("provisioner.failure._vastai_command")
    def test_destroy_action(self, mock_vastai):
        on_failure = OnFailure(action="destroy")
        handle_failure(on_failure, "/path/manifest.yaml")
        mock_vastai.assert_called_once_with("destroy")

    @patch("provisioner.failure._vastai_command")
    def test_stop_action(self, mock_vastai, tmp_path, monkeypatch):
        monkeypatch.setattr("provisioner.failure.STOP_SENTINEL", str(tmp_path / "sentinel"))
        on_failure = OnFailure(action="stop")
        handle_failure(on_failure, "/path/manifest.yaml")
        mock_vastai.assert_called_once_with("stop")

    def test_unknown_action(self):
        on_failure = OnFailure(action="invalid")
        # Should not raise
        handle_failure(on_failure, "/path/manifest.yaml")


class TestStopOnceSentinel:
    """Verify stop-once semantics via /.provisioner_stopped sentinel."""

    @patch("provisioner.failure._vastai_command")
    def test_stop_writes_sentinel(self, mock_vastai, tmp_path, monkeypatch):
        """First stop should create sentinel and call _vastai_command."""
        sentinel = str(tmp_path / "provisioner_stopped")
        monkeypatch.setattr("provisioner.failure.STOP_SENTINEL", sentinel)
        on_failure = OnFailure(action="stop")
        handle_failure(on_failure, "/path/manifest.yaml")
        mock_vastai.assert_called_once_with("stop")
        assert os.path.exists(sentinel)

    @patch("provisioner.failure._vastai_command")
    def test_stop_skips_when_sentinel_exists(self, mock_vastai, tmp_path, monkeypatch):
        """Second stop should skip _vastai_command when sentinel present."""
        sentinel = str(tmp_path / "provisioner_stopped")
        # Pre-create sentinel
        open(sentinel, "w").close()
        monkeypatch.setattr("provisioner.failure.STOP_SENTINEL", sentinel)
        on_failure = OnFailure(action="stop")
        handle_failure(on_failure, "/path/manifest.yaml")
        mock_vastai.assert_not_called()

    @patch("provisioner.failure._vastai_command")
    def test_destroy_ignores_sentinel(self, mock_vastai, tmp_path, monkeypatch):
        """Destroy should NOT check sentinel -- a destroyed instance can't restart."""
        sentinel = str(tmp_path / "provisioner_stopped")
        open(sentinel, "w").close()
        monkeypatch.setattr("provisioner.failure.STOP_SENTINEL", sentinel)
        on_failure = OnFailure(action="destroy")
        handle_failure(on_failure, "/path/manifest.yaml")
        mock_vastai.assert_called_once_with("destroy")

    @patch("provisioner.failure._vastai_command")
    def test_stop_still_fires_when_sentinel_touch_fails(self, mock_vastai, monkeypatch):
        """If sentinel can't be written (e.g. read-only fs), stop command still fires."""
        monkeypatch.setattr("provisioner.failure.STOP_SENTINEL", "/nonexistent_dir/sentinel")
        on_failure = OnFailure(action="stop")
        handle_failure(on_failure, "/path/manifest.yaml")
        mock_vastai.assert_called_once_with("stop")


class TestNotifySuccess:
    """Verify opt-in success webhook."""

    @patch("provisioner.failure._call_webhook")
    def test_sends_webhook_when_enabled(self, mock_webhook):
        on_failure = OnFailure(webhook="https://hooks.example.com", webhook_on_success=True)
        notify_success(on_failure, "/path/manifest.yaml")
        mock_webhook.assert_called_once()
        payload = mock_webhook.call_args[0][1]
        assert payload["action"] == "success"
        assert payload["error"] == ""

    @patch("provisioner.failure._call_webhook")
    def test_skips_when_disabled(self, mock_webhook):
        on_failure = OnFailure(webhook="https://hooks.example.com", webhook_on_success=False)
        notify_success(on_failure, "/path/manifest.yaml")
        mock_webhook.assert_not_called()

    @patch("provisioner.failure._call_webhook")
    def test_skips_when_no_webhook_url(self, mock_webhook):
        on_failure = OnFailure(webhook_on_success=True)
        notify_success(on_failure, "/path/manifest.yaml")
        mock_webhook.assert_not_called()

    @patch("provisioner.failure._call_webhook")
    def test_env_url_overrides_manifest(self, mock_webhook, monkeypatch):
        monkeypatch.setenv("PROVISIONER_WEBHOOK_URL", "https://env-hook.example.com")
        on_failure = OnFailure(webhook="https://manifest-hook.example.com", webhook_on_success=True)
        notify_success(on_failure, "/path/manifest.yaml")
        url = mock_webhook.call_args[0][0]
        assert url == "https://env-hook.example.com"
