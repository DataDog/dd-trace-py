"""
OpenFeature configuration settings.
"""

from typing import Optional

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

    # Experimental APM span enrichment with feature-flag evaluation metadata.
    # DISTINCT from DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED and OFF by default.
    # When enabled, the provider attaches ffe_* tags to the root APM span.
    experimental_flagging_provider_span_enrichment_enabled = DDConfig.var(
        bool,
        "DD_EXPERIMENTAL_FLAGGING_PROVIDER_SPAN_ENRICHMENT_ENABLED",
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

    # Provider initialization timeout in milliseconds. Controls how long initialize()
    # blocks waiting for the first configuration payload, from either configuration
    # source. Expiry is not an error; the provider stays NOT_READY and becomes READY when
    # configuration arrives.
    # Default is 10000ms: long enough for a healthy delivery path, and short enough that a
    # pre-fork worker boots inside gunicorn's 30s default worker timeout. Raising it much
    # further risks the worker being killed before it finishes starting.
    initialization_timeout_ms = DDConfig.var(
        int,
        "DD_EXPERIMENTAL_FLAGGING_PROVIDER_INITIALIZATION_TIMEOUT_MS",
        default=10000,
    )

    # Stable Feature Flagging kill switch. When False, the provider is disabled
    # regardless of the configured source. Default on.
    feature_flags_enabled = DDConfig.var(
        bool,
        "DD_FEATURE_FLAGS_ENABLED",
        default=True,
    )

    # Where Feature Flagging loads Universal Flag Configuration from.
    # Supported: "agentless" (default) and "remote_config"; "offline" is reserved
    # and currently unsupported. Normalized to trimmed lowercase; validity and
    # grandfathering are resolved by the source-selection layer.
    configuration_source = DDConfig.var(
        str,
        "DD_FEATURE_FLAGS_CONFIGURATION_SOURCE",
        default="agentless",
        parser=lambda v: v.strip().lower(),
    )

    # Optional override of the agentless UFC endpoint or base URL. A root/origin
    # URL receives the standard rules-based path; a non-root URL is used verbatim.
    configuration_source_agentless_base_url = DDConfig.var(
        Optional[str],
        "DD_FEATURE_FLAGS_CONFIGURATION_SOURCE_AGENTLESS_BASE_URL",
        default=None,
        parser=lambda v: v.strip() or None,
    )

    # Agentless UFC polling interval in seconds, capped at one hour by the source.
    configuration_source_agentless_poll_interval_seconds = DDConfig.var(
        int,
        "DD_FEATURE_FLAGS_CONFIGURATION_SOURCE_AGENTLESS_POLL_INTERVAL_SECONDS",
        default=30,
    )

    # Agentless UFC per-request timeout in seconds.
    configuration_source_agentless_request_timeout_seconds = DDConfig.var(
        int,
        "DD_FEATURE_FLAGS_CONFIGURATION_SOURCE_AGENTLESS_REQUEST_TIMEOUT_SECONDS",
        default=5,
    )

    _openfeature_config_keys = [
        "experimental_flagging_provider_enabled",
        "experimental_flagging_provider_span_enrichment_enabled",
        "flagging_evaluation_counts_enabled",
        "ffe_intake_enabled",
        "ffe_intake_heartbeat_interval",
        "initialization_timeout_ms",
        "feature_flags_enabled",
        "configuration_source",
        "configuration_source_agentless_base_url",
        "configuration_source_agentless_poll_interval_seconds",
        "configuration_source_agentless_request_timeout_seconds",
    ]


# Global config instance
config = OpenFeatureConfig()
