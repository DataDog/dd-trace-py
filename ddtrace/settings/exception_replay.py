from ddtrace.settings._core import DDConfig
from ddtrace.settings._telemetry import report_telemetry as _report_telemetry


class ExceptionReplayConfig(DDConfig):
    __prefix__ = "dd.exception"

    enabled = DDConfig.v(
        bool,
        "replay.enabled",
        default=False,
        help_type="Boolean",
        help="Enable automatic capturing of exception debugging information",
        deprecations=[("debugging.enabled", None, "3.0")],
    )
    max_frames = DDConfig.v(
        int,
        "replay.capture_max_frames",
        default=8,
        help_type="int",
        help="The maximum number of frames to capture for each exception",
    )


config = ExceptionReplayConfig()
_report_telemetry(config)
