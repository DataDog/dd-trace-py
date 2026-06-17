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

    # Killswitch for the EVP `flagevaluation` evaluation-counts path. Default on; gates
    # ONLY the EVP flagevaluation writer/hook. The existing OTel `feature_flag.evaluations`
    # path is unaffected by this flag.
    flagging_evaluation_counts_enabled = DDConfig.var(
        bool,
        "DD_FLAGGING_EVALUATION_COUNTS_ENABLED",
        default=True,
    )

    flagging_evaluation_counts_flush_interval = DDConfig.var(
        float,
        "DD_FLAGGING_EVALUATION_COUNTS_FLUSH_INTERVAL_SECONDS",
        default=10.0,
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
    # Default is 10000ms (10 seconds).
    initialization_timeout_ms = DDConfig.var(
        int,
        "DD_EXPERIMENTAL_FLAGGING_PROVIDER_INITIALIZATION_TIMEOUT_MS",
        default=10000,
    )

    _openfeature_config_keys = [
        "experimental_flagging_provider_enabled",
        "flagging_evaluation_counts_enabled",
        "flagging_evaluation_counts_flush_interval",
        "ffe_intake_enabled",
        "ffe_intake_heartbeat_interval",
        "initialization_timeout_ms",
    ]


# Global config instance
config = OpenFeatureConfig()
