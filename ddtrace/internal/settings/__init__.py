from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

from ..vendor.debtcollector import deprecate


def __getattr__(name):
    if name in set(
        [
            "ConfigException",
            "HttpConfig",
            "Hooks",
            "IntegrationConfig",
        ]
    ):
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
