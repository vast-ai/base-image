"""Put the flat tool modules (models/template_manager/create) on sys.path.

Run: cd tools/template_manager && PYTHONPATH=. python -m pytest -q
(requires the dev deps from requirements.txt: pydantic, pyyaml, python-dotenv)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "contract: an external-checks test that runs the real contract (contract.py) "
                   "instead of the autouse stub")


import pytest


@pytest.fixture(autouse=True)
def _proofs_in_tmp(tmp_path, monkeypatch):
    """A test that drives test_template.main() must not leave proofs in the checkout:
    the default --proof-dir points into the test's tmp_path."""
    import test_template
    monkeypatch.setattr(test_template, "DEFAULT_PROOF_DIR", str(tmp_path / "qa-proofs"))


@pytest.fixture(autouse=True)
def _no_atexit_handlers_leak(monkeypatch):
    """test_template.main() registers teardown and its proof writer with atexit. In a
    test those must never reach the real atexit: they would fire when pytest exits,
    after the summary, against state the test has long dropped. Every registration
    during a test lands here instead; a test that wants to run them patches
    atexit.register itself (its patch wins). Guarded by
    test_main_leaves_no_atexit_handler_in_the_pytest_process."""
    import atexit
    registered = []
    monkeypatch.setattr(atexit, "register", lambda fn, *a, **k: registered.append(fn) or fn)
    return registered
