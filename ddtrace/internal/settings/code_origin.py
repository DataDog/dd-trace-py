from ddtrace.internal.settings._core import DDConfig


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

    file_path_rewrite = DDConfig.v(
        str,
        "file_path_rewrite",
        default="",
        help_type="String",
        help="Rewrite file paths in code origin tags. Format: 'old_prefix=new_prefix' "
        "(pipe-delimited for multiple rules, e.g. "
        "'/opt/app/site-packages/=src/'). First matching rule wins.",
    )

    class SpanCodeOriginConfig(DDConfig):
        __prefix__ = "for_spans"
        __item__ = "span"

        enabled = DDConfig.v(
            bool,
            "enabled",
            default=True,
            help_type="Boolean",
            help="Enable code origin for spans",
        )


config = CodeOriginConfig()
