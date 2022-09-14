import sys


sys.path.insert(0, "../../..")  # to allow importing from the tests module.
from ddtrace.debugging._probe.model import LineProbe  # noqa
from tests.debugging.mocking import TestDebugger  # noqa


TestDebugger.enable()

debugger = TestDebugger._instance

debugger.add_probes(
    LineProbe(
        probe_id="run_module_test",
        source_file="target.py",
        line=9,
    )
)
