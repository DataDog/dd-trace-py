from ddtrace.settings._core import DDConfig


class CodeOriginConfig(DDConfig):
    __prefix__ = "dd.code_origin"

    max_user_frames = DDConfig.v(
        int,
        "max_user_frames",
        default=8,
        help_type="Integer",
        help="Maximum number of user frames to capture for code origin",
        private=True,
    )

    class SpanCodeOriginConfig(DDConfig):
        __prefix__ = "for_spans"
        __item__ = "span"

        enabled = DDConfig.v(
            bool,
            "enabled",
            default=False,
            help_type="Boolean",
            help="Enable code origin for spans",
        )


config = CodeOriginConfig()
