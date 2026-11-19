"""Feature flag provider for other products.

Loaded through the ddtrace.feature_flag_providers entry point; see ddtrace/internal/feature_flags.py.
"""


def enable_remote_config() -> None:
    from ddtrace.internal.openfeature._remoteconfiguration import enable_featureflags_rc

    enable_featureflags_rc()


def set_domain_provider(domain: str) -> None:
    # The OpenFeature SDK is optional, so it is imported only when a provider is requested.
    from openfeature import api

    from ddtrace.internal.openfeature._provider import DataDogProvider

    # Non-blocking: registers for configuration callbacks without waiting for delivery.
    api.set_provider(DataDogProvider(initialization_timeout=0), domain)
