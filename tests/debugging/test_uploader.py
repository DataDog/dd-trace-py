import json
from time import sleep

import pytest

from ddtrace.debugging._encoding import BatchJsonEncoder
from ddtrace.debugging._encoding import BufferFull
from ddtrace.debugging._uploader import LogsIntakeUploaderV1


class MockLogsIntakeUploaderV1(LogsIntakeUploaderV1):
    def __init__(self, *args, **kwargs):
        super(MockLogsIntakeUploaderV1, self).__init__(*args, **kwargs)
        self.queue = []

    def _write(self, payload):
        self.queue.append(payload.decode())

    @property
    def payloads(self):
        return [json.loads(data) for data in self.queue]


class ActiveBatchJsonEncoder(MockLogsIntakeUploaderV1):
    def __init__(self, size=1 << 10, interval=1):
        super(ActiveBatchJsonEncoder, self).__init__(
            BatchJsonEncoder({str: str}, size, self.on_full), interval=interval
        )

    def on_full(self, item, encoded):
        self.upload()


def test_uploader_batching():
    with ActiveBatchJsonEncoder(interval=0.1) as uploader:
        for _ in range(5):
            uploader._encoder.put("hello")
            uploader._encoder.put("world")
            sleep(0.15)
        assert uploader.queue == ["[hello,world]"] * 5


def test_uploader_full_buffer():
    size = 1 << 8
    with ActiveBatchJsonEncoder(size=size, interval=1) as uploader:
        item = "hello" * 10
        n = size // len(item)
        assert n

        with pytest.raises(BufferFull):
            for _ in range(2 * n):
                uploader._encoder.put(item)

        # The full buffer forces a flush
        sleep(0.01)
        assert len(uploader.queue) == 1

        # wakeup to mimik next interval
        uploader.awake()
        assert len(uploader.queue) == 1
