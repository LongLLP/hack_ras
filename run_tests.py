"""
Run tests from Spyder by pressing Run (F5).
- Tries pytest first (nicer output, uses your existing tests as-is).
- Falls back to unittest discovery if pytest isn't available.
"""

from __future__ import annotations
import sys
from pathlib import Path

# Ensure repo root on sys.path so `from hack_ras...` works
REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

def _run_pytest() -> int:
    try:
        import pytest  # type: ignore
    except Exception:
        return -1
    # Quiet output; remove "-q" if you want more detail
    return pytest.main(["-q", "tests"])

def _run_unittest() -> int:
    import unittest
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=str(REPO_ROOT / "tests"))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1

if __name__ == "__main__":
    code = _run_pytest()
    if code == -1:
        print("pytest not found; falling back to unittest discovery...")
        code = _run_unittest()
    raise SystemExit(code)