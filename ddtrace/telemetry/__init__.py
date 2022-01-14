import ddtrace.internal.telemetry


class InstrumentationTelemetry:
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

        ddtrace.internal.telemetry.enable()

    @staticmethod
    def disable():
        # type: () -> None
        """
        Disable the telemetry collection service.
        Once disabled, telemetry collection can be re-enabled by calling ``enable``
        again.
        """
        ddtrace.internal.telemetry.disable()


__all__ = ["InstrumentationTelemetry"]
