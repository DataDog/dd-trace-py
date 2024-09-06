import sys
from threading import current_thread

from ddtrace.debugging._signal.snapshot import Snapshot
from tests.debugging.utils import create_log_function_probe


def test_enriched_args_locals_globals():
    duration = 123456
    full_scope = dict(
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
        ).get_full_scope(None, (None, None, None), duration)
    )

    # Check for globals
    assert "__file__" in full_scope

    # Check for locals
    assert "duration" in full_scope


def test_duration_millis():
    duration = 123456
    full_scope = Snapshot(
        probe=create_log_function_probe(
            probe_id="test_duration_millis",
            module="foo",
            func_qname="bar",
            template="",
            segments=[],
        ),
        frame=sys._getframe(),
        thread=current_thread(),
    ).get_full_scope(None, (None, None, None), duration)

    assert full_scope["@duration"] == duration / 1e6
