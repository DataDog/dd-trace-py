import json

import pytest

from ddtrace.debugging._encoding import BatchJsonEncoder
from ddtrace.debugging._encoding import BufferFull
from ddtrace.debugging._uploader import LogsIntakeUploaderV1
from ddtrace.internal.compat import Queue


# DEV: Using float('inf') with lock wait intervals may cause an OverflowError
# so we use a large enough integer as an approximation instead.
LONG_INTERVAL = 2147483647.0


class MockLogsIntakeUploaderV1(LogsIntakeUploaderV1):
    def __init__(self, *args, **kwargs):
        super(MockLogsIntakeUploaderV1, self).__init__(*args, **kwargs)
        self.queue = Queue()

    def _write(self, payload):
        self.queue.put(payload.decode())

    @property
    def payloads(self):
        return [json.loads(data) for data in self.queue]


class ActiveBatchJsonEncoder(MockLogsIntakeUploaderV1):
    def __init__(self, size=1 << 10, interval=1):
        super(ActiveBatchJsonEncoder, self).__init__(
            BatchJsonEncoder({str: str}, size, self.on_full), interval=interval
        )

    def on_full(self, item, encoded):
        self.periodic()


def test_uploader_batching():
    with ActiveBatchJsonEncoder(interval=LONG_INTERVAL) as uploader:
        for _ in range(5):
            uploader._encoder.put("hello")
            uploader._encoder.put("world")
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
            for _ in range(2 * n):
                uploader._encoder.put(item)

        # The full buffer forces a flush
        uploader.queue.get(timeout=0.5)
        assert uploader.queue.qsize() == 0

        # wakeup to mimic next interval
        uploader.periodic()
        assert uploader.queue.qsize() == 0
