import json

import pytest

from ddtrace.debugging._encoding import BatchJsonEncoder
from ddtrace.debugging._encoding import BufferFull
from ddtrace.debugging._uploader import LogsIntakeUploaderV1
from ddtrace.internal.compat import PY2
from ddtrace.internal.compat import Queue


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
    with ActiveBatchJsonEncoder(interval=float("inf")) as uploader:
        for _ in range(5):
            uploader._encoder.put("hello")
            uploader._encoder.put("world")
            uploader.periodic()

        for _ in range(5):
            assert uploader.queue.get(timeout=1) == "[hello,world]", "iteration %d" % _


@pytest.mark.xfail(condition=PY2, reason="This test is flaky on Python 2")
def test_uploader_full_buffer():
    size = 1 << 8
    with ActiveBatchJsonEncoder(size=size, interval=float("inf")) as uploader:
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
