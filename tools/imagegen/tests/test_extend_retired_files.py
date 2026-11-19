"""Dockerfile.extend lays ROOT/ over an already-built image, and COPY never deletes: a file ROOT
no longer ships survives in the source image unless the extend build removes it (ADR 0059)."""
from __future__ import annotations

from pathlib import Path

from imagegen.dockerfile import parse

REPO = Path(__file__).resolve().parents[3]
RETIRED = ("/etc/vast_boot.d/48-venv-backup.sh", "/opt/instance-tools/bin/venv-backup.sh")


def test_extend_removes_what_root_no_longer_ships():
    runs = [i.exec for i in parse((REPO / "Dockerfile.extend").read_text()) if i.cmd == "RUN"]
    for path in RETIRED:
        assert not (REPO / "ROOT" / path.lstrip("/")).exists(), f"{path} is shipped again; drop it from RETIRED"
        assert any("rm " in r and path in r for r in runs), f"Dockerfile.extend keeps {path} from the source image"
