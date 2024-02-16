import sys


sys.path.insert(0, "../../..")  # to allow importing from the tests module.

from tests.debugging.mocking import TestDebugger  # noqa:E402,F401
from tests.debugging.utils import create_snapshot_line_probe  # noqa:E402,F401


TestDebugger.enable()

debugger = TestDebugger._instance

debugger.add_probes(
    create_snapshot_line_probe(
        probe_id="run_module_test",
        source_file="target.py",
        line=9,
        condition=None,
    )
)
