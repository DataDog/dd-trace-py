from ddtrace.vendor.debtcollector import deprecate
_deprecated_names = [
    "ConfigException",
    "HttpConfig",
    "Hooks",
    "IntegrationConfig",
]


def __getattr__(name):
    if name in _deprecated_names:
        deprecate(
            ("%s.%s is deprecated" % (__name__, name)),
            removal_version="4.0.0", # TODO: update this to the correct version
        )
        return _deprecated_names[name]
    raise AttributeError("'%s' has no attribute '%s'", __name__, name)