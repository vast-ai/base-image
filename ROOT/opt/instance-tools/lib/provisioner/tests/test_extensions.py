"""Tests for provisioner.extensions -- loading, running, dry-run, error handling."""

from __future__ import annotations

import types
from unittest.mock import MagicMock, patch

import pytest

from provisioner.extensions import ExtensionContext, run_extensions
from provisioner.schema import Extension, Manifest, validate_manifest


class TestRunExtensions:
    def test_empty_list(self):
        """No extensions is a no-op."""
        run_extensions([], manifest=Manifest(), dry_run=False)

    def test_disabled_extension_skipped(self):
        """Extensions with enabled=False are skipped."""
        ext = Extension(module="nonexistent_module", config={}, enabled=False)
        # Should not raise even though module doesn't exist
        run_extensions([ext], manifest=Manifest(), dry_run=False)

    def test_empty_module_skipped(self):
        """Extensions with empty module name are skipped with a warning."""
        ext = Extension(module="", config={"key": "val"}, enabled=True)
        run_extensions([ext], manifest=Manifest(), dry_run=False)

    def test_dry_run_does_not_import(self):
        """Dry run logs intent but does not import the module."""
        ext = Extension(module="nonexistent_module", config={"a": 1}, enabled=True)
        # Should not raise -- module is never imported in dry-run
        run_extensions([ext], manifest=Manifest(), dry_run=True)

    @patch("provisioner.extensions.importlib.import_module")
    def test_calls_run_function(self, mock_import):
        """Extension's run() is called with config, context, and dry_run."""
        mock_mod = MagicMock()
        mock_import.return_value = mock_mod

        manifest = Manifest()
        ext = Extension(module="my_ext", config={"key": "val"}, enabled=True)
        run_extensions([ext], manifest=manifest, dry_run=False)

        mock_import.assert_called_once_with("my_ext")
        mock_mod.run.assert_called_once()
        call_kwargs = mock_mod.run.call_args
        assert call_kwargs.kwargs["config"] == {"key": "val"}
        assert isinstance(call_kwargs.kwargs["context"], ExtensionContext)
        assert call_kwargs.kwargs["context"].manifest is manifest
        assert call_kwargs.kwargs["dry_run"] is False

    @patch("provisioner.extensions.importlib.import_module")
    def test_multiple_extensions_run_in_order(self, mock_import):
        """Multiple extensions are called sequentially."""
        call_order = []
        mod_a = MagicMock()
        mod_a.run.side_effect = lambda **kw: call_order.append("a")
        mod_b = MagicMock()
        mod_b.run.side_effect = lambda **kw: call_order.append("b")

        mock_import.side_effect = lambda name: {"ext_a": mod_a, "ext_b": mod_b}[name]

        exts = [
            Extension(module="ext_a", config={}, enabled=True),
            Extension(module="ext_b", config={}, enabled=True),
        ]
        run_extensions(exts, manifest=Manifest(), dry_run=False)
        assert call_order == ["a", "b"]

    @patch("provisioner.extensions.importlib.import_module")
    def test_extension_error_propagates(self, mock_import):
        """An extension that raises should propagate the error."""
        mock_mod = MagicMock()
        mock_mod.run.side_effect = RuntimeError("extension broke")
        mock_import.return_value = mock_mod

        ext = Extension(module="bad_ext", config={}, enabled=True)
        with pytest.raises(RuntimeError, match="extension broke"):
            run_extensions([ext], manifest=Manifest(), dry_run=False)

    @patch("provisioner.extensions.importlib.import_module")
    def test_import_error_gives_descriptive_message(self, mock_import):
        """A missing module raises RuntimeError with module name and cause."""
        mock_import.side_effect = ImportError("No module named 'missing'")

        ext = Extension(module="missing", config={}, enabled=True)
        with pytest.raises(RuntimeError, match="Extension module 'missing' not found"):
            run_extensions([ext], manifest=Manifest(), dry_run=False)

    @patch("provisioner.extensions.importlib.import_module")
    def test_missing_run_function_gives_descriptive_error(self, mock_import):
        """A module without run() raises RuntimeError with clear message."""
        import types
        mock_mod = types.ModuleType("no_run_ext")
        mock_import.return_value = mock_mod

        ext = Extension(module="no_run_ext", config={}, enabled=True)
        with pytest.raises(RuntimeError, match="has no run\\(\\) function"):
            run_extensions([ext], manifest=Manifest(), dry_run=False)

    @patch("provisioner.extensions.importlib.import_module")
    def test_arbitrary_nested_config(self, mock_import):
        """Config dict with arbitrary nesting is passed through."""
        mock_mod = MagicMock()
        mock_import.return_value = mock_mod

        config = {
            "workflows": ["https://example.com/wf.json", "/local/wf.json"],
            "options": {"resolution": "1024x1024", "steps": 20},
        }
        ext = Extension(module="my_ext", config=config, enabled=True)
        run_extensions([ext], manifest=Manifest(), dry_run=False)

        passed_config = mock_mod.run.call_args.kwargs["config"]
        assert passed_config == config
        assert passed_config["workflows"][0] == "https://example.com/wf.json"
        assert passed_config["options"]["steps"] == 20


class TestExtensionContext:
    def test_context_fields(self):
        """ExtensionContext holds manifest and logger."""
        import logging
        manifest = Manifest()
        logger = logging.getLogger("test")
        ctx = ExtensionContext(manifest=manifest, log=logger)
        assert ctx.manifest is manifest
        assert ctx.log is logger
