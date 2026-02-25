"""
OpenFeature configuration settings.
"""

from ddtrace.internal.settings._core import DDConfig


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

    # Provider initialization timeout in milliseconds.
    # Controls how long initialize() blocks waiting for the first Remote Config payload.
    # Default is 30000ms (30 seconds), matching Java, Go, and Node.js SDKs.
    initialization_timeout_ms = DDConfig.var(
        int,
        "DD_EXPERIMENTAL_FLAGGING_PROVIDER_INITIALIZATION_TIMEOUT_MS",
        default=30000,
    )

    _openfeature_config_keys = [
        "experimental_flagging_provider_enabled",
        "ffe_intake_enabled",
        "ffe_intake_heartbeat_interval",
        "initialization_timeout_ms",
    ]


# Global config instance
config = OpenFeatureConfig()
