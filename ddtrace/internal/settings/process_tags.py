from ddtrace.internal.settings._core import DDConfig


class ProcessTagsConfig(DDConfig):
    __prefix__ = "dd"

    enabled = DDConfig.v(
        bool,
        "experimental.propagate.process.tags.enabled",
        default=False,
        help="Enables process tags in products payload",
    )


process_tags_config = ProcessTagsConfig()
