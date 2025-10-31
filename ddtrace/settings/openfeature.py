"""
OpenFeature configuration settings.
"""

from ddtrace.settings._core import DDConfig


class OpenFeatureConfig(DDConfig):
    """
    Configuration for OpenFeature provider and exposure reporting.
    """

    # Experimental flagging provider
    experimental_flagging_provider_enabled = DDConfig.var(
        bool,
        "DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED",
        default=False,
    )

    # Feature flag exposure intake configuration
    ffe_intake_enabled = DDConfig.var(
        bool,
        "DD_FFE_INTAKE_ENABLED",
        default=True,
    )

    ffe_intake_heartbeat_interval = DDConfig.var(
        float,
        "DD_FFE_INTAKE_HEARTBEAT_INTERVAL",
        default=1.0,
    )

    _openfeature_config_keys = [
        "experimental_flagging_provider_enabled",
        "ffe_intake_enabled",
        "ffe_intake_heartbeat_interval",
    ]


# Global config instance
config = OpenFeatureConfig()
