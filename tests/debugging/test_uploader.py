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
