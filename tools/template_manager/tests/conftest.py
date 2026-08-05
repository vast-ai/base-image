"""Put the flat tool modules (models/template_manager/create) on sys.path.

Run: cd tools/template_manager && PYTHONPATH=. python -m pytest -q
(requires the dev deps from requirements.txt: pydantic, pyyaml, python-dotenv)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
