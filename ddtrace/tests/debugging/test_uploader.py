import json
from queue import Queue
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from ddtrace.debugging._encoding import BufferFull
from ddtrace.debugging._encoding import SignalQueue
from ddtrace.debugging._signal.model import SignalTrack
from ddtrace.debugging._uploader import SignalUploader
from ddtrace.debugging._uploader import SignalUploaderError
from ddtrace.debugging._uploader import UploaderProduct


# DEV: Using float('inf') with lock wait intervals may cause an OverflowError
# so we use a large enough integer as an approximation instead.
LONG_INTERVAL = 2147483647.0


class MockSignalUploader(SignalUploader):
    def __init__(self, *args, **kwargs):
        super(MockSignalUploader, self).__init__(*args, **kwargs)
        self.queue = Queue()
        self._state = self._online

    def _write(self, payload, endpoint):
        self.queue.put(payload.decode())

    @property
    def payloads(self):
        return [json.loads(data) for data in self.queue]


class ActiveBatchJsonEncoder(MockSignalUploader):
    def __init__(self, size=1 << 10, interval=1):
        super(ActiveBatchJsonEncoder, self).__init__(interval)

        # Override the signal queue
        for track in self._tracks.values():
            track.queue = SignalQueue(None, size, self.on_full)

    def on_full(self, item, encoded):
        self.periodic()


def test_uploader_batching():
    with ActiveBatchJsonEncoder(interval=LONG_INTERVAL) as uploader:
        queue = uploader._tracks.values().__iter__().__next__().queue
        for _ in range(5):
            queue.put_encoded(None, "hello".encode("utf-8"))
            queue.put_encoded(None, "world".encode("utf-8"))
            uploader.periodic()

        for _ in range(5):
            assert uploader.queue.get(timeout=1) == "[hello,world]", "iteration %d" % _


def test_uploader_full_buffer():
    size = 1 << 8
    with ActiveBatchJsonEncoder(size=size, interval=LONG_INTERVAL) as uploader:
        item = "hello" * 10
        n = size // len(item)
        assert n

        with pytest.raises(BufferFull):
            queue = uploader._tracks.values().__iter__().__next__().queue
            for _ in range(2 * n):
                queue.put_encoded(None, item.encode("utf-8"))

        # The full buffer forces a flush
        uploader.queue.get(timeout=0.5)
        assert uploader.queue.qsize() == 0

        # wakeup to mimic next interval
        uploader.periodic()
        assert uploader.queue.qsize() == 0


def test_uploader_502_error():
    """Test that _write raises SignalUploaderError for 502 Bad Gateway errors."""
    from ddtrace.debugging._uploader import SignalUploader
    from ddtrace.debugging._uploader import SignalUploaderError

    class MockResponse:
        status = 502

        def read(self):
            return b"Bad Gateway"

    class MockConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return MockResponse()

    uploader = SignalUploader(interval=LONG_INTERVAL)
    uploader._connect = lambda: MockConnection()

    # Assert that 502 errors raise SignalUploaderError
    with pytest.raises(SignalUploaderError):
        uploader._write(b'{"test": "data"}', "/debugger/v1/input")


def test_info_check_endpoint_selection():
    """Test info_check endpoint selection and fallback behavior for both LOGS and SNAPSHOT tracks."""
    from ddtrace.debugging._signal.model import SignalTrack
    from ddtrace.debugging._uploader import SignalUploader

    # Test 1: v2 endpoint available - both tracks use v2
    uploader = SignalUploader(interval=LONG_INTERVAL)
    agent_info = {"endpoints": ["/debugger/v2/input", "/debugger/v1/diagnostics"]}
    assert uploader.info_check(agent_info) is True
    assert uploader._tracks[SignalTrack.LOGS].enabled is True
    assert uploader._tracks[SignalTrack.SNAPSHOT].enabled is True
    assert "/debugger/v2/input" in uploader._tracks[SignalTrack.LOGS].endpoint
    assert "/debugger/v2/input" in uploader._tracks[SignalTrack.SNAPSHOT].endpoint

    # Test 2: Only diagnostics available - both tracks fallback to diagnostics
    uploader = SignalUploader(interval=LONG_INTERVAL)
    agent_info = {"endpoints": ["/debugger/v1/diagnostics"]}
    assert uploader.info_check(agent_info) is True
    assert "/debugger/v1/diagnostics" in uploader._tracks[SignalTrack.LOGS].endpoint
    assert "/debugger/v1/diagnostics" in uploader._tracks[SignalTrack.SNAPSHOT].endpoint

    # Test 3: No supported endpoints - both tracks disabled
    uploader = SignalUploader(interval=LONG_INTERVAL)
    agent_info = {"endpoints": ["/some/other/endpoint"]}
    assert uploader.info_check(agent_info) is True
    assert uploader._tracks[SignalTrack.LOGS].enabled is False
    assert uploader._tracks[SignalTrack.SNAPSHOT].enabled is False

    # Test 4: No endpoints key or agent unreachable - returns False
    uploader = SignalUploader(interval=LONG_INTERVAL)
    assert uploader.info_check({"version": "7.48.0"}) is False
    assert uploader.info_check(None) is False

    # Test 5: _downgrade_to_diagnostics updates both tracks
    uploader = SignalUploader(interval=LONG_INTERVAL)
    assert "/debugger/v2/input" in uploader._tracks[SignalTrack.LOGS].endpoint
    uploader._downgrade_to_diagnostics()
    assert "/debugger/v1/diagnostics" in uploader._tracks[SignalTrack.LOGS].endpoint
    assert "/debugger/v1/diagnostics" in uploader._tracks[SignalTrack.SNAPSHOT].endpoint


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conn(status=200, read_body=b""):
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = read_body

    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.getresponse.return_value = resp
    return conn


def _put_data(uploader, track=SignalTrack.LOGS, data=b"data"):
    uploader._tracks[track].queue.put_encoded(None, data)


def test_write_success_meters():
    uploader = SignalUploader(interval=LONG_INTERVAL)
    uploader._connect = lambda: _make_conn(status=200)

    with patch("ddtrace.debugging._uploader.meter") as mock_meter:
        uploader._write(b"payload", "/debugger/v1/input")

    mock_meter.increment.assert_called_with("upload.success")
    mock_meter.distribution.assert_called_with("upload.size", len(b"payload"))


def test_write_connection_exception_is_caught():
    uploader = SignalUploader(interval=LONG_INTERVAL)

    conn = MagicMock()
    conn.__enter__ = MagicMock(side_effect=OSError("refused"))
    conn.__exit__ = MagicMock(return_value=False)
    uploader._connect = lambda: conn

    with patch("ddtrace.debugging._uploader.meter") as mock_meter, patch("ddtrace.debugging._uploader.log") as mock_log:
        uploader._write(b"payload", "/debugger/v1/input")

    mock_meter.increment.assert_called_with("error")
    mock_log.error.assert_called_once()


def test_on_buffer_full_sets_flag_and_wakes():
    uploader = SignalUploader(interval=LONG_INTERVAL)

    with patch.object(uploader, "upload") as mock_upload:
        uploader._on_buffer_full(None, b"")

    assert uploader._flush_full is True
    mock_upload.assert_called_once()


def test_upload_calls_awake():
    uploader = SignalUploader(interval=LONG_INTERVAL)

    with patch.object(uploader, "awake") as mock_awake:
        uploader.upload()

    mock_awake.assert_called_once()


def test_reset_replaces_queues_and_updates_collector():
    uploader = SignalUploader(interval=LONG_INTERVAL)
    old_queues = {t: ut.queue for t, ut in uploader._tracks.items()}

    uploader.reset()

    for track, ut in uploader._tracks.items():
        assert ut.queue is not old_queues[track], "queue should be a new instance after reset"
        assert uploader._collector._tracks[track] is ut.queue


def test_flush_track_downgrades_and_retries_on_signal_uploader_error():
    uploader = SignalUploader(interval=LONG_INTERVAL)
    _put_data(uploader)

    calls = []

    def write_side_effect(payload, endpoint):
        calls.append(endpoint)
        if len(calls) == 1:
            raise SignalUploaderError("first attempt")

    with (
        patch.object(uploader, "_write_with_backoff", side_effect=write_side_effect),
        patch("ddtrace.debugging._uploader.meter"),
    ):
        uploader._flush_track(uploader._tracks[SignalTrack.LOGS])

    assert len(calls) == 2
    assert calls[1].startswith("/debugger/v1/diagnostics")


def test_flush_track_reraises_when_already_on_diagnostics():
    uploader = SignalUploader(interval=LONG_INTERVAL)
    uploader._downgrade_to_diagnostics()
    _put_data(uploader)

    with (
        patch.object(uploader, "_write_with_backoff", side_effect=SignalUploaderError("still failing")),
        pytest.raises(SignalUploaderError),
    ):
        uploader._flush_track(uploader._tracks[SignalTrack.LOGS])


def test_flush_track_swallows_generic_exception():
    uploader = SignalUploader(interval=LONG_INTERVAL)
    _put_data(uploader)

    with (
        patch.object(uploader, "_write_with_backoff", side_effect=RuntimeError("oops")),
        patch("ddtrace.debugging._uploader.log") as mock_log,
    ):
        uploader._flush_track(uploader._tracks[SignalTrack.LOGS])

    mock_log.debug.assert_called_once()


def test_online_flushes_full_track():
    uploader = SignalUploader(interval=LONG_INTERVAL)
    uploader._flush_full = True

    # Fill the queue so is_full() is True
    track = uploader._tracks[SignalTrack.LOGS]
    while not track.queue.is_full():
        try:
            track.queue.put_encoded(None, b"x" * 256)
        except BufferFull:
            break

    flushed = []
    original_flush_track = uploader._flush_track

    def _capturing_flush_track(t):
        flushed.append(t)
        # avoid actual HTTP
        with patch.object(uploader, "_write_with_backoff"):
            original_flush_track(t)

    with patch.object(uploader, "_flush_track", side_effect=_capturing_flush_track):
        try:
            uploader.online()
        except ValueError:
            pass  # tracks-not-enabled; irrelevant here

    assert any(t.track == SignalTrack.LOGS for t in flushed)
    assert uploader._flush_full is False


def test_online_raises_when_tracks_disabled():
    uploader = SignalUploader(interval=LONG_INTERVAL)
    uploader._tracks[SignalTrack.LOGS].enabled = False
    uploader._tracks[SignalTrack.SNAPSHOT].enabled = False

    with pytest.raises(ValueError, match="not enabled"):
        uploader.online()


class _IsolatedUploader(MockSignalUploader):
    """Subclass to isolate class-level _products/_instance state from production code."""

    _instance = None
    _products: set = set()


def test_register_ignores_duplicate():
    try:
        _IsolatedUploader.register(UploaderProduct.DEBUGGER)
        instance_after_first = _IsolatedUploader._instance

        _IsolatedUploader.register(UploaderProduct.DEBUGGER)
        # Instance must not be replaced on second registration
        assert _IsolatedUploader._instance is instance_after_first
    finally:
        _IsolatedUploader.unregister(UploaderProduct.DEBUGGER)


def test_unregister_ignores_unknown_product():
    # Should not raise, and should not change _instance
    _IsolatedUploader._instance = None
    _IsolatedUploader._products = set()

    _IsolatedUploader.unregister(UploaderProduct.EXCEPTION_REPLAY)  # was never registered

    assert _IsolatedUploader._instance is None
