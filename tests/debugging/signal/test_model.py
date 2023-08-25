import sys
from threading import current_thread

from ddtrace.debugging._signal.snapshot import Snapshot
from tests.debugging.utils import create_log_function_probe


def test_duration_millis():
    duration = 123456
    args = Snapshot(
        probe=create_log_function_probe(
            probe_id="test_duration_millis",
            module="foo",
            func_qname="bar",
            template="",
            segments=[],
        ),
        frame=sys._getframe(),
        thread=current_thread(),
    )._enrich_args(None, (None, None, None), duration)

    assert args["@duration"] == duration / 1e6
