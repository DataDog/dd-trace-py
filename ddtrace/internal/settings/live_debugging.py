from ddtrace.settings._core import DDConfig


class LiveDebuggerConfig(DDConfig):
    __prefix__ = "dd.live_debugging"

    enabled = DDConfig.v(
        bool,
        "enabled",
        default=False,
        help_type="Boolean",
        help="Enable the live debugger.",
    )


config = LiveDebuggerConfig()
