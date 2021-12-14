from typing import Optional

import six

import ddtrace.internal.telemetry.telemetry_writer


# TO DO: USE AGENT ENDPOINT
DEFAULT_TELEMETRY_ENDPOINT = "https://instrumentation-telemetry-intake.datadoghq.com"
DEFAULT_TELEMETRY_ENDPOINT_TEST = "https://all-http-intake.logs.datad0g.com/api/v2/apmtelemetry"


class _InstrumentationTelemetryStatus(type):
    @property
    def _enabled(_):
        # type: () -> bool
        """Instrumentation Telemetry enabled status."""
        return ddtrace.internal.telemetry.telemetry_writer.TelemetryWriter.enabled


class InstrumentationTelemetry(six.with_metaclass(_InstrumentationTelemetryStatus)):
    """
    Instrumentation Telemetry service API.
    This is normally started automatically by ``ddtrace-run`` when the
    ``DD_INSTRUMENTATION_TELEMETRY_ENABLED`` variable is set.
    To start the service manually, invoke the ``enable`` static method::
        from ddtrace.telemetry import InstrumentationTelemetry
        InstrumentationTelemetry.enable()
    """

    @staticmethod
    def enable(tracer=None, dogstatsd_url=None, flush_interval=None):
        # type: (Optional[ddtrace.Tracer], Optional[str], Optional[float]) -> None
        """
        Enable the instrumentation telemetry collection service.
        If the service has already been activated before, this method does
        nothing. Use ``disable`` to turn off the telemetry collection service.
        :param endpoint: instrumentation-telemetry-intake public api (will be replaced with an agent endpoint)
        """

        ddtrace.internal.telemetry.telemetry_writer.TelemetryWriter.enable(endpoint=DEFAULT_TELEMETRY_ENDPOINT)

    @staticmethod
    def disable():
        # type: () -> None
        """
        Disable the telemetry collection service.
        Once disabled, telemetry collection can be re-enabled by calling ``enable``
        again.
        """
        ddtrace.internal.telemetry.telemetry_writer.TelemetryWriter.disable()


__all__ = ["InstrumentationTelemetry"]
