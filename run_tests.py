"""Test runner that works with or without pytest installed:  python run_tests.py"""
import importlib.util
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
failures = 0
total = 0

for test_file in sorted((ROOT / "tests").glob("test_*.py")):
    spec = importlib.util.spec_from_file_location(test_file.stem, test_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in dir(module):
        if not name.startswith("test_"):
            continue
        total += 1
        try:
            getattr(module, name)()
            print(f"PASS  {test_file.stem}.{name}")
        except Exception:  # noqa: BLE001
            failures += 1
            print(f"FAIL  {test_file.stem}.{name}")
            traceback.print_exc(limit=3)

print(f"\n{total - failures}/{total} passed")
sys.exit(1 if failures else 0)
