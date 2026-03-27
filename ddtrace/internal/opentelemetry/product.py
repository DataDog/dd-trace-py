requires = ["tracer"]


def post_preload() -> None:
    pass


def enabled() -> bool:
    from ddtrace import config

    return config._otel_enabled


def start() -> None:
    from ddtrace import config
    from ddtrace.internal.module import ModuleWatchdog

    @ModuleWatchdog.after_module_imported("opentelemetry")
    def _(_):
        if config._otel_trace_enabled:
            from opentelemetry.trace import set_tracer_provider

            from ddtrace.opentelemetry import TracerProvider

            set_tracer_provider(TracerProvider())

        if config._otel_logs_enabled:
            from ddtrace.internal.opentelemetry.logs import set_otel_logs_provider

            set_otel_logs_provider()

        if config._otel_metrics_enabled:
            from ddtrace.internal.opentelemetry.metrics import set_otel_meter_provider

            set_otel_meter_provider()


def restart(join: bool = False) -> None:
    pass


def stop(join: bool = False) -> None:
    pass
