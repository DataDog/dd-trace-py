import pytest


@pytest.mark.subprocess(
    parametrize={
        "AWS_LAMBDA_MICROVM_IMAGE_ARN": ["", "arn:aws:lambda:us-east-1::runtime:python3.12"],
        "PAUSE_AT": ["processor", "writer", "start"],
    },
)
def test_shutdown_rejects_inflight_trace_publication() -> None:
    import os
    import threading
    from unittest import mock

    from ddtrace._trace.processor import SpanAggregator
    from ddtrace.internal.service import ServiceStatus
    from ddtrace.internal.service import ServiceStatusError
    from ddtrace.internal.writer import NativeWriter
    from ddtrace.trace import Span

    entered = threading.Event()
    release = threading.Event()
    errors = []
    pause_at = os.environ["PAUSE_AT"]

    def process_trace(spans):
        if pause_at == "processor":
            entered.set()
            assert release.wait(10)
        return spans

    processor = mock.Mock(process_trace=mock.Mock(side_effect=process_trace))
    with mock.patch.object(NativeWriter, "_create_exporter", return_value=mock.Mock(send=lambda _: "{}")):
        aggregator = SpanAggregator(partial_flush_enabled=False, partial_flush_min_spans=1, dd_processors=[processor])
    writer = aggregator.writer
    assert isinstance(writer, NativeWriter)
    assert writer.status is ServiceStatus.STOPPED
    assert writer._worker is None
    original_write = writer.write
    original_start = writer.start

    def write(spans):
        if pause_at == "writer":
            entered.set()
            assert release.wait(10)
        original_write(spans)

    def start():
        if pause_at == "start":
            entered.set()
            assert release.wait(10)
        original_start()

    span = Span("shutdown.inflight", on_finish=[aggregator.on_span_finish])
    span.context.sampling_priority = 2
    aggregator.on_span_start(span)

    def finish():
        try:
            span.finish()
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=finish)
    try:
        with (
            mock.patch.object(writer, "write", side_effect=write),
            mock.patch.object(writer, "start", side_effect=start),
        ):
            thread.start()
            try:
                assert entered.wait(10)
                # writer/start pauses can hold the aggregation or writer lock. Shutdown must not wait for them.
                aggregator.shutdown(0)
            finally:
                release.set()
                thread.join(10)
        assert not thread.is_alive()
        assert not errors, errors
        assert not aggregator._traces
        assert writer.status is ServiceStatus.STOPPED
        assert writer._worker is None, "an in-flight finish started the periodic writer after shutdown"
        assert len(writer._encoder) == 0
    finally:
        release.set()
        if thread.ident is not None:
            thread.join(10)
        try:
            writer.stop(1)
        except ServiceStatusError:
            pass
        writer.shutdown_exporter()
