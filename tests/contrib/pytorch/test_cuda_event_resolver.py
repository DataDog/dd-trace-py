import time
from unittest import mock

from ddtrace.contrib.internal.pytorch._distributed import CudaEventResolver


class FakeEvent:
    def __init__(self):
        self._ready_after = 0.0
        self._created = time.monotonic()

    def schedule_ready(self, after_seconds: float):
        self._ready_after = after_seconds

    def query(self) -> bool:
        return (time.monotonic() - self._created) >= self._ready_after

    def elapsed_time(self, other: "FakeEvent") -> float:
        # ms between create timestamps
        return max(0.0, (other._created - self._created) * 1000.0)


def make_span():
    span = mock.MagicMock()
    span.finished = False

    def finish():
        span.finished = True

    span.finish.side_effect = finish
    return span


def test_resolver_resolves_completed_pair():
    resolver = CudaEventResolver(poll_interval=0.01)
    resolver.start()
    try:
        start, end = FakeEvent(), FakeEvent()
        end.schedule_ready(0.01)
        span = make_span()
        resolver.submit(span, start, end)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not span.finished:
            time.sleep(0.01)
        assert span.finished is True
        span._set_attribute.assert_any_call("gpu.duration_ms", mock.ANY)
    finally:
        resolver.stop(timeout=2.0)


def test_resolver_overflow_drops_oldest_and_finishes_with_reason(caplog):
    resolver = CudaEventResolver(poll_interval=0.01, capacity=2)
    try:
        spans = [make_span() for _ in range(3)]
        with caplog.at_level("WARNING"):
            for s in spans:
                start, end = FakeEvent(), FakeEvent()
                end.schedule_ready(60.0)  # never ready
                resolver.submit(s, start, end)
        # First submitted span was evicted on the third submit.
        assert spans[0].finished is True
        spans[0].set_tag.assert_any_call("_dd.error_reason", "cuda_event_overflow")
        # The other two are still queued (not finished yet).
        assert spans[1].finished is False
        assert spans[2].finished is False
        assert any("queue overflow" in rec.message.lower() for rec in caplog.records)
    finally:
        resolver.stop(timeout=2.0)


def test_resolver_shutdown_marks_unresolved_spans():
    resolver = CudaEventResolver(poll_interval=0.01)
    resolver.start()
    span = make_span()
    start, end = FakeEvent(), FakeEvent()
    end.schedule_ready(60.0)  # never ready in test
    resolver.submit(span, start, end)
    resolver.stop(timeout=2.0)
    assert span.finished is True
    span.set_tag.assert_any_call("_dd.error_reason", "cuda_event_unresolved")
