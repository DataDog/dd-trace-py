from ddtrace.internal.settings._core import DDConfig

class ThirdPartyDetectionConfig(DDConfig):
    excludes: set[str]
    includes: set[str]

config: ThirdPartyDetectionConfig
