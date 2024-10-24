import sys
from threading import current_thread
import typing as t

from ddtrace.debugging._signal.context import SignalContext
from ddtrace.debugging._signal.model import Signal
from tests.debugging.utils import create_log_function_probe


class MockSignal(Signal):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope = {}

    def enter(self, scope):
        self.scope = scope

    def exit(self, retval, exc_info, duration, scope):
        self.scope = scope

    def line(self, scope):
        self.scope = scope


def test_enriched_args_locals_globals():
    duration = 123456
    exit_scope = t.cast(
        MockSignal,
        SignalContext(
            MockSignal(
                probe=create_log_function_probe(
                    probe_id="test_duration_millis",
                    module="foo",
                    func_qname="bar",
                    template="",
                    segments=[],
                ),
                frame=sys._getframe(),
                thread=current_thread(),
            )
        ).exit(None, (None, None, None), duration),
    ).scope

    # Check for globals
    assert "__file__" in exit_scope

    # Check for locals
    assert "duration" in exit_scope

    # Check for the correct duration units
    assert exit_scope["@duration"] == duration / 1e6
