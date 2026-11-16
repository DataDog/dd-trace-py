# The package-split harness lives under scripts/ (it is tooling, not a
# shipped ddtrace module), so make it importable for the test suite before
# any test module is collected.

from pathlib import Path
import sys


_SCRIPTS = str(Path(__file__).resolve().parents[2] / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
