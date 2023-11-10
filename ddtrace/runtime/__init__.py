from typing import Optional

import six

import ddtrace.internal.runtime.runtime_metrics


class _RuntimeMetricsStatus(type):
    @property
    def _enabled(_) -> bool:
        """Runtime metrics enabled status."""
        return ddtrace.internal.runtime.runtime_metrics.RuntimeWorker.enabled


class RuntimeMetrics(six.with_metaclass(_RuntimeMetricsStatus)):
    """
    Runtime metrics service API.

    This is normally started automatically by ``ddtrace-run`` when the
    ``DD_RUNTIME_METRICS_ENABLED`` variable is set.

    To start the service manually, invoke the ``enable`` static method::

        from ddtrace.runtime import RuntimeMetrics
        RuntimeMetrics.enable()
    """

    @staticmethod
    def enable(tracer: Optional[ddtrace.Tracer] = None, dogstatsd_url: Optional[str] = None, flush_interval: Optional[float] = None) -> None:
        """
        Enable the runtime metrics collection service.

        If the service has already been activated before, this method does
        nothing. Use ``disable`` to turn off the runtime metric collection
        service.

        :param tracer: The tracer instance to correlate with.
        :param dogstatsd_url: The DogStatsD URL.
        :param flush_interval: The flush interval.
        """

        ddtrace.internal.runtime.runtime_metrics.RuntimeWorker.enable(
            tracer=tracer, dogstatsd_url=dogstatsd_url, flush_interval=flush_interval
        )

    @staticmethod
    def disable() -> None:
        """
        Disable the runtime metrics collection service.

        Once disabled, runtime metrics can be re-enabled by calling ``enable``
        again.
        """
        ddtrace.internal.runtime.runtime_metrics.RuntimeWorker.disable()


__all__ = ["RuntimeMetrics"]
