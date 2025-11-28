from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning


def __getattr__(name):
    if name in set(
        [
            "ConfigException",
            "HttpConfig",
            "Hooks",
            "IntegrationConfig",
        ]
    ):
        # Import here to avoid circular imports chain.
        # ddtrace.internal.logger → ddtrace.settings._env → ddtrace.settings
        # → ddtrace.vendor → ddtrace.internal.module → back to ddtrace.internal.logger
        from ddtrace.vendor.debtcollector import deprecate

        deprecate(
            ("%s.%s is deprecated" % (__name__, name)),
            removal_version="4.0.0",  # TODO: update this to the correct version
            category=DDTraceDeprecationWarning,
        )
        if name == "ConfigException":
            from ddtrace.settings.exceptions import ConfigException

            return ConfigException
        elif name == "HttpConfig":
            from .http import HttpConfig

            return HttpConfig
        elif name == "Hooks":
            from .._hooks import Hooks

            return Hooks
        elif name == "IntegrationConfig":
            from .integration import IntegrationConfig

            return IntegrationConfig
    raise AttributeError("'%s' has no attribute '%s'" % (__name__, name))
