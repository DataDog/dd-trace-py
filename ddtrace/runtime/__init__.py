from typing import Optional

import ddtrace


class RuntimeMetrics(object):
    """
    Runtime metrics service API.

    This is normally started automatically by ``ddtrace-run`` when the
    DD_RUNTIME_METRICS_ENABLED variable is set.

    To start the service manually, invoke the ``enable`` static method::

        from ddtrace.runtime import RuntimeMetrics
        RuntimeMetrics.enable()
    """

    @staticmethod
    def enable(tracer=None, dogstatsd_url=None, flush_interval=None):
        """
        Enable the runtime metrics collection service.

        :param tracer: The tracer instance to correlate with.
        :param dogstatsd_url: The DogStatsD URL.
        :param flush_interval: The flush interval.
        """
        # type: (Optional[ddtrace.Tracer], Optional[str], Optional[float]) -> None

        ddtrace.internal.runtime.runtime_metrics.RuntimeWorker.enable(
            tracer=tracer, dogstatsd_url=dogstatsd_url, flush_interval=flush_interval
        )

    @staticmethod
    def disable():
        """
        Disable the runtime metrics collection service.
        """
        # type: () -> None
        runtime_worker = ddtrace.internal.runtime.runtime_metrics.RuntimeWorker._instance
        runtime_worker.stop()
        runtime_worker.join()
        ddtrace.internal.runtime.runtime_metrics.RuntimeWorker._instance = None


__all__ = ["RuntimeMetrics"]
