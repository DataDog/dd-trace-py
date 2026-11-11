import pytest


@pytest.mark.subprocess(
    parametrize={"PROCESSOR_SCOPE": ["local", "global"], "RETRY_WITH": ["public", "product"]},
)
def test_shutdown_retry_skips_completed_processors() -> None:
    import os
    from unittest import mock

    import pytest

    from ddtrace._trace import product
    from ddtrace._trace.processor import SpanProcessor
    from ddtrace.internal import core
    from ddtrace.trace import tracer

    class Processor(SpanProcessor):
        def __init__(self, fail_once=False):
            self.calls = []
            self.fail_once = fail_once

        def on_span_start(self, span):
            pass

        def on_span_finish(self, span):
            pass

        def __eq__(self, other):
            # Distinct processors may compare equal and need not be hashable.
            return isinstance(other, Processor)

        def shutdown(self, timeout):
            self.calls.append(timeout)
            if self.fail_once and len(self.calls) == 1:
                raise RuntimeError("retry cleanup")
            if not self.fail_once and len(self.calls) > 1:
                raise RuntimeError("cleanup repeated")

    completed = Processor()
    retrying = Processor(fail_once=True)
    product.start()
    if os.environ["PROCESSOR_SCOPE"] == "local":
        tracer._span_processors.extend([completed, retrying])
    else:
        completed.register()
        retrying.register()

    aggregator = tracer._span_aggregator
    with mock.patch.object(aggregator, "shutdown", wraps=aggregator.shutdown) as shutdown_aggregator:
        with pytest.raises(RuntimeError, match="retry cleanup"):
            product.stop()
        assert completed.calls == [None]
        assert retrying.calls == [None]
        shutdown_aggregator.assert_not_called()
        assert core.has_listeners("context.ended.web.request")

        if os.environ["RETRY_WITH"] == "public":
            tracer.shutdown()
        else:
            product.stop()
        assert completed.calls == [None]
        assert retrying.calls == [None, None]
        shutdown_aggregator.assert_called_once_with(None)
        assert not core.has_listeners("context.started.web.request")
        assert not core.has_listeners("context.ended.web.request")
        assert not core.has_listeners("tracer.shutdown")
        assert tracer.start_span == tracer._start_span_after_shutdown
        assert aggregator._shutdown

        tracer.shutdown()
        assert completed.calls == [None]
        assert retrying.calls == [None, None]
        shutdown_aggregator.assert_called_once_with(None)
