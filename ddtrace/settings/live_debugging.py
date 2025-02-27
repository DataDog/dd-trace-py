from ddtrace.settings._core import DDConfig as En


class LiveDebuggerConfig(En):
    __prefix__ = "dd.live_debugging"

    enabled = En.v(
        bool,
        "enabled",
        default=False,
        help_type="Boolean",
        help="Enable the live debugger.",
    )


config = LiveDebuggerConfig()
