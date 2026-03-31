import json
from queue import Queue

import pytest

from ddtrace.debugging._encoding import BufferFull
from ddtrace.debugging._encoding import SignalQueue
from ddtrace.debugging._uploader import SignalUploader


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
