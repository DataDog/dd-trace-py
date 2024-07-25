from envier import En


class ExceptionReplayConfig(En):
    __prefix__ = "dd.exception"

    enabled = En.v(
        bool,
        "replay.enabled",
        default=False,
        help_type="Boolean",
        help="Enable automatic capturing of exception debugging information",
        deprecations=[("debugging.enabled", None, "3.0")],
    )


config = ExceptionReplayConfig()
