from typing import Any
from typing import Optional  # noqa:F401

from ddtrace.internal.runtime import runtime_metrics
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.internal.utils.deprecations import deprecate


TELEMETRY_RUNTIMEMETRICS_ENABLED = "DD_RUNTIME_METRICS_ENABLED"

_TRACER_NOT_SET = object()


class _RuntimeMetricsStatus(type):
    @property
    def _enabled(_):
        # type: () -> bool
        """Runtime metrics enabled status."""
        return runtime_metrics.RuntimeWorker.enabled


class RuntimeMetrics(metaclass=_RuntimeMetricsStatus):
    """
    Runtime metrics service API.

    This is normally started automatically by ``ddtrace-run`` when the
    ``DD_RUNTIME_METRICS_ENABLED`` variable is set.

    To start the service manually, invoke the ``enable`` static method::

        from ddtrace.runtime import RuntimeMetrics
        RuntimeMetrics.enable()
    """

    @staticmethod
    def enable(
        tracer: Any = _TRACER_NOT_SET,
        dogstatsd_url: Optional[str] = None,
    ) -> None:
        """
        If the service has already been activated before, this method does
        nothing. Use ``disable`` to turn off the runtime metric collection
        service.

        :param tracer: Deprecated and unused.
        """
        if tracer is not _TRACER_NOT_SET:
            deprecate(
                prefix="The tracer parameter to RuntimeMetrics.enable is deprecated",
                message="It is not used and will be removed in a future version.",
                removal_version="5.0.0",
                category=DDTraceDeprecationWarning,
            )
        telemetry_writer.add_configuration(TELEMETRY_RUNTIMEMETRICS_ENABLED, True, origin="code")
        runtime_metrics.RuntimeWorker.enable(dogstatsd_url=dogstatsd_url)

    @staticmethod
    def disable() -> None:
        """
        Disable the runtime metrics collection service.

        Once disabled, runtime metrics can be re-enabled by calling ``enable``
        again.
        """
        telemetry_writer.add_configuration(TELEMETRY_RUNTIMEMETRICS_ENABLED, False, origin="code")
        runtime_metrics.RuntimeWorker.disable()


__all__ = ["RuntimeMetrics"]
