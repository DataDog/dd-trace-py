from envier import En


class ExceptionDebuggingConfig(En):
    __prefix__ = "dd.exception_debugging"

    enabled = En.v(
        bool,
        "enabled",
        default=False,
        help_type="Boolean",
        help="Enable automatic capturing of exception debugging information",
    )


config = ExceptionDebuggingConfig()
