import six

import ddtrace.internal.telemetry.telemetry_writer


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
    def enable():
        # type: () -> None
        """
        Enable the instrumentation telemetry collection service.
        If the service has already been activated before, this method does
        nothing. Use ``disable`` to turn off the telemetry collection service.
        :param endpoint: instrumentation-telemetry-intake public api (will be replaced with an agent endpoint)
        """

        ddtrace.internal.telemetry.telemetry_writer.TelemetryWriter.enable()

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
