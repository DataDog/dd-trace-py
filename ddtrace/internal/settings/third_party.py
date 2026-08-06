from envier.env import EnvVariable

from ddtrace.internal.settings._core import DDConfig


class ThirdPartyDetectionConfig(DDConfig):
    __prefix__ = "dd.third_party_detection"

    excludes: EnvVariable[set[str]] = DDConfig.v(
        set,
        "excludes",
        help="List of packages that should not be treated as third-party",
        help_type="List",
        default=set(),
    )
    includes: EnvVariable[set[str]] = DDConfig.v(
        set,
        "includes",
        help="Additional packages to treat as third-party",
        help_type="List",
        default=set(),
    )


config = ThirdPartyDetectionConfig()
