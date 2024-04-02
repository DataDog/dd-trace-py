from envier import En

from ddtrace.internal.logger import get_logger


logger = get_logger(__name__)


class InjectionConfig(En):
    __prefix__ = "dd.injection"

    enabled = En.v(
        bool,
        "enabled",
        default=False,
        help_type="Boolean",
        help="Whether the ddtrace library was injected into the application. This configuration will be applied "
        "automatically if it is needed. Setting this manually does not enable any features, it is for internal use "
        "only.",
    )


config = InjectionConfig()
