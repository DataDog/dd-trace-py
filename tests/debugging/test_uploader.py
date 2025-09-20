import json
from queue import Queue

import pytest

from ddtrace.debugging._encoding import BufferFull
from ddtrace.debugging._encoding import SignalQueue
from ddtrace.debugging._uploader import LogsIntakeUploaderV1


# DEV: Using float('inf') with lock wait intervals may cause an OverflowError
# so we use a large enough integer as an approximation instead.
LONG_INTERVAL = 2147483647.0


class MockLogsIntakeUploaderV1(LogsIntakeUploaderV1):
    def __init__(self, *args, **kwargs):
        super(MockLogsIntakeUploaderV1, self).__init__(*args, **kwargs)
        self.queue = Queue()

    def _write(self, payload, endpoint):
        self.queue.put(payload.decode())

    @property
    def payloads(self):
        return [json.loads(data) for data in self.queue]


class ActiveBatchJsonEncoder(MockLogsIntakeUploaderV1):
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


def test_uploader_preserves_queue_metadata_on_agent_endpoint_refresh():
    """Test that track queue metadata is preserved when agent endpoints are refreshed."""
    import mock

    from ddtrace.debugging._signal.model import SignalTrack
    from ddtrace.internal import agent

    # Mock agent.info to return initial endpoints
    initial_agent_info = {"endpoints": ["/debugger/v1/input", "/debugger/v1/diagnostics"]}
    updated_agent_info = {"endpoints": ["/debugger/v1/input", "/debugger/v2/input"]}

    with mock.patch.object(agent, "info", return_value=initial_agent_info):
        uploader = MockLogsIntakeUploaderV1(interval=LONG_INTERVAL)

        # Add some data to the queues
        logs_queue = uploader._tracks[SignalTrack.LOGS].queue
        snapshot_queue = uploader._tracks[SignalTrack.SNAPSHOT].queue

        # Put some encoded data in the queues
        logs_queue.put_encoded(None, "log_data".encode("utf-8"))
        snapshot_queue.put_encoded(None, "snapshot_data".encode("utf-8"))

        # Store queue references and verify they have data
        original_logs_queue = logs_queue
        original_snapshot_queue = snapshot_queue
        original_logs_count = logs_queue.count
        original_snapshot_count = snapshot_queue.count

        assert original_logs_count > 0, "Logs queue should have data"
        assert original_snapshot_count > 0, "Snapshot queue should have data"

        # Force the cache to expire by mocking trickling to return False
        with mock.patch.object(uploader._agent_endpoints_cache, "trickling", return_value=False):
            # Mock agent.info to return updated endpoints (v2 instead of v1 diagnostics)
            with mock.patch.object(agent, "info", return_value=updated_agent_info):
                # This should trigger set_track_endpoints to refresh but preserve queue metadata
                uploader.set_track_endpoints()

        # Verify that the track queues are the same objects (not recreated)
        assert uploader._tracks[SignalTrack.LOGS].queue is original_logs_queue
        assert uploader._tracks[SignalTrack.SNAPSHOT].queue is original_snapshot_queue

        # Verify that queue counts are preserved
        assert uploader._tracks[SignalTrack.LOGS].queue.count == original_logs_count
        assert uploader._tracks[SignalTrack.SNAPSHOT].queue.count == original_snapshot_count

        # Verify that the endpoint was updated for snapshot track
        assert "/debugger/v2/input" in uploader._tracks[SignalTrack.SNAPSHOT].endpoint

        # Verify we can still flush without BufferFull errors
        uploader.periodic()

        # The data should have been uploaded
        assert uploader.queue.qsize() == 2  # One payload for logs, one for snapshots


def test_uploader_agent_endpoint_refresh_multiple_calls():
    """Test that multiple calls to set_track_endpoints with cache expiry work correctly."""
    import mock

    from ddtrace.debugging._signal.model import SignalTrack
    from ddtrace.internal import agent

    agent_responses = [
        {"endpoints": ["/debugger/v1/input"]},
        {"endpoints": ["/debugger/v1/input", "/debugger/v1/diagnostics"]},
        {"endpoints": ["/debugger/v1/input", "/debugger/v2/input"]},
    ]

    with mock.patch.object(agent, "info", return_value=agent_responses[0]):
        uploader = MockLogsIntakeUploaderV1(interval=LONG_INTERVAL)

        # Add data to track buffer state
        snapshot_queue = uploader._tracks[SignalTrack.SNAPSHOT].queue
        snapshot_queue.put_encoded(None, "test_data".encode("utf-8"))
        original_count = snapshot_queue.count

        # Track the original queue object
        original_queue = snapshot_queue

        # Simulate multiple agent endpoint updates
        for i, agent_response in enumerate(agent_responses[1:], 1):
            with mock.patch.object(uploader._agent_endpoints_cache, "trickling", return_value=False):
                with mock.patch.object(agent, "info", return_value=agent_response):
                    uploader.set_track_endpoints()

            # Queue should be preserved across all updates
            assert uploader._tracks[SignalTrack.SNAPSHOT].queue is original_queue
            assert uploader._tracks[SignalTrack.SNAPSHOT].queue.count == original_count

            # Add more data to ensure buffer state is maintained
            snapshot_queue.put_encoded(None, f"test_data_{i}".encode("utf-8"))
            original_count = snapshot_queue.count

        # Final verification - queue should still be functional
        uploader.periodic()
        assert uploader.queue.qsize() > 0
