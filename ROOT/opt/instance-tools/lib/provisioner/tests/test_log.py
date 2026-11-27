"""Tests for provisioner.log -- logging setup."""

from __future__ import annotations

import logging
import os

import pytest

from provisioner.log import setup_logging


class TestSetupLogging:
    def test_returns_logger(self):
        logger = setup_logging()
        assert isinstance(logger, logging.Logger)
        assert logger.name == "provisioner"

    def test_has_stdout_handler(self):
        logger = setup_logging()
        assert any(isinstance(h, logging.StreamHandler) for h in logger.handlers)

    def test_no_duplicate_handlers(self):
        """Calling setup_logging multiple times without a log file doesn't add handlers."""
        logger = setup_logging()
        count = len(logger.handlers)
        setup_logging()
        assert len(logger.handlers) == count

    def test_file_handler_added(self, tmp_path):
        log_file = str(tmp_path / "test.log")
        logger = setup_logging(log_file)
        assert any(isinstance(h, logging.FileHandler) for h in logger.handlers)

    def test_file_handler_not_duplicated(self, tmp_path):
        log_file = str(tmp_path / "test.log")
        logger = setup_logging(log_file)
        file_count = sum(1 for h in logger.handlers if isinstance(h, logging.FileHandler))
        setup_logging(log_file)
        file_count2 = sum(1 for h in logger.handlers if isinstance(h, logging.FileHandler))
        assert file_count == file_count2

    def test_creates_parent_directory(self, tmp_path):
        log_file = str(tmp_path / "subdir" / "deep" / "test.log")
        setup_logging(log_file)
        assert os.path.isdir(os.path.dirname(log_file))

    def test_writes_to_file(self, tmp_path):
        log_file = str(tmp_path / "output.log")
        logger = setup_logging(log_file)
        logger.info("test message 12345")
        # Flush handlers
        for h in logger.handlers:
            h.flush()
        content = open(log_file).read()
        assert "test message 12345" in content

    def test_permission_error_handled(self, tmp_path):
        """If log file can't be opened, still returns a working logger."""
        logger = setup_logging("/root/impossible/path/test.log")
        assert logger is not None
        # Should have at least the stdout handler
        assert len(logger.handlers) >= 1

    def test_debug_level(self):
        logger = setup_logging()
        assert logger.level == logging.DEBUG


SECRET = "s3cr3t-container-key"


class TestRedactSecrets:
    """The provisioning log is shown in the portal and leaves the box in support
    bundles and QA evidence, so secret env values must never reach any handler."""

    def test_masked_in_every_handler(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("CONTAINER_API_KEY", SECRET)
        portal_log = tmp_path / "portal" / "provisioning.log"
        logger = setup_logging(str(portal_log))
        logger.info("Running: vastai show instance 1 --api-key %s", SECRET)
        # Extension loggers propagate; a logger-level filter would miss them.
        logging.getLogger("provisioner.ext.demo").info("ext saw %s", SECRET)
        try:
            raise RuntimeError(f"failed with {SECRET}")
        except RuntimeError:
            logger.exception("command failed")

        outputs = {
            "portal": portal_log.read_text(),
            "clean": (tmp_path / "provisioning.log").read_text(),
            "stdout": capsys.readouterr().out,
        }
        for name, text in outputs.items():
            assert SECRET not in text, name
            assert "--api-key ***" in text and "ext saw ***" in text, name

    def test_expanded_post_command_not_logged(self, tmp_path, monkeypatch, tmp_manifest):
        from provisioner.__main__ import run
        from provisioner.manifest import load_manifest

        monkeypatch.setenv("CONTAINER_API_KEY", SECRET)
        log_file = tmp_path / "portal" / "provisioning.log"
        path = tmp_manifest({
            "version": 1,
            "settings": {"log_file": str(log_file)},
            "post_commands": ["vastai show instance 1 --api-key ${CONTAINER_API_KEY}"],
        })
        run(path, load_manifest(path), dry_run=True)
        text = log_file.read_text()
        assert "Would run: vastai show instance 1 --api-key ***" in text
        assert SECRET not in text
