# tests/conftest.py
from pathlib import Path
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# Tests that launch HEC-RAS (marker `ras_compute`) run by default and take
# about a minute; `pytest --skip-ras` leaves them out. They also skip
# themselves when the HEC-RAS version they need is not installed.
def pytest_addoption(parser):
    parser.addoption("--skip-ras", action="store_true", default=False,
                     help="skip tests that launch HEC-RAS (marker ras_compute)")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "ras_compute: launches HEC-RAS; skipped with --skip-ras")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--skip-ras"):
        return
    skip = pytest.mark.skip(reason="--skip-ras given")
    for item in items:
        if "ras_compute" in item.keywords:
            item.add_marker(skip)
