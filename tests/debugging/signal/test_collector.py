import inspect
import sys
import threading
from uuid import uuid4

import mock

from ddtrace.debugging._probe.model import ProbeEvalTiming
from ddtrace.debugging._signal.collector import SignalCollector
from ddtrace.debugging._signal.log import LogSignal
from ddtrace.debugging._signal.model import SignalState
from ddtrace.debugging._signal.snapshot import Snapshot
from tests.debugging.utils import create_log_function_probe
from tests.debugging.utils import create_snapshot_line_probe


def mock_encoder(wraps=None):
    encoder = mock.Mock(wraps=wraps)
    snapshot_encoder = mock.Mock()
    encoder._encoders = {Snapshot: snapshot_encoder}

    return encoder, snapshot_encoder


def test_collector_collect_enqueue_only_commit_state():
    class MockLogSignal(LogSignal):
        def __init__(self, *args, **kwargs):
            super(MockLogSignal, self).__init__(*args, **kwargs)
            self.exit_call_count = 0
            self.enter_call_count = 0

        def enter(self, scope):
            self.enter_call_count += 1

        def exit(self, retval, exc_info, duration, scope):
            self.exit_call_count += 1

        def line(self, scope):
            return

        @property
        def message(self):
            return "test"

        def has_message(self):
            return True

    c = 0
    for i in range(10):
        mocked_signal = MockLogSignal(
            create_log_function_probe(
                probe_id="test",
                template=None,
                segments=[],
                module="foo",
                func_qname="bar",
                evaluate_at=ProbeEvalTiming.ENTRY,
            ),
            sys._getframe(),
            threading.current_thread(),
        )
        mocked_signal.do_enter()

        assert mocked_signal.enter_call_count == 1
        done = 1 - (i % 2)
        mocked_signal.state = SignalState.DONE if done else SignalState.SKIP_COND

        mocked_signal.do_exit(None, sys.exc_info(), 0)

        assert mocked_signal.exit_call_count == 0
        c += done

    assert c == 5


def test_collector_push_enqueue():
    encoder, _ = mock_encoder()

    collector = SignalCollector(encoder=encoder)
    frame = inspect.currentframe()
    assert frame is not None

    for _ in range(10):
        snapshot = Snapshot(
            probe=create_snapshot_line_probe(probe_id=uuid4(), source_file="file.py", line=123),
            frame=frame,
            thread=threading.current_thread(),
        )
        snapshot.line({})
        snapshot.state = SignalState.DONE
        collector.push(snapshot)

    assert len(encoder.put.mock_calls) == 10
