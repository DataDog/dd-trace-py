import sys
from threading import current_thread

from ddtrace.debugging._signal.snapshot import Snapshot
from tests.debugging.utils import create_log_function_probe


def test_enriched_args_locals_globals():
    duration = 123456
    _locals = dict(
        Snapshot(
            probe=create_log_function_probe(
                probe_id="test_duration_millis",
                module="foo",
                func_qname="bar",
                template="",
                segments=[],
            ),
            frame=sys._getframe(),
            thread=current_thread(),
        )._enrich_locals(None, (None, None, None), duration)
    )

    # Check for globals
    assert "__file__" in _locals

    # Check for locals
    assert "duration" in _locals


def test_duration_millis():
    duration = 123456
    _locals = Snapshot(
        probe=create_log_function_probe(
            probe_id="test_duration_millis",
            module="foo",
            func_qname="bar",
            template="",
            segments=[],
        ),
        frame=sys._getframe(),
        thread=current_thread(),
    )._enrich_locals(None, (None, None, None), duration)

    assert _locals["@duration"] == duration / 1e6
