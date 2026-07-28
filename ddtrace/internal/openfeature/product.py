from ddtrace.internal.settings.openfeature import config as ffe_config


requires = ["remote-configuration"]


def post_preload():
    pass


def enabled():
    from ddtrace.internal.openfeature._source_selection import DISABLED
    from ddtrace.internal.openfeature._source_selection import resolve_configuration_source

    return resolve_configuration_source(ffe_config) != DISABLED


def start():
    # Agent Remote Config delivery is activated only when it is the resolved
    # source. The agentless source is started from the provider lifecycle
    # (mirroring dd-trace-js), so there is nothing to start here for agentless.
    from ddtrace.internal.openfeature._source_selection import REMOTE_CONFIG
    from ddtrace.internal.openfeature._source_selection import resolve_configuration_source

    if resolve_configuration_source(ffe_config) == REMOTE_CONFIG:
        from ddtrace.internal.openfeature._remoteconfiguration import enable_featureflags_rc

        enable_featureflags_rc()


def restart(join=False):
    pass


def stop(join=False):
    from ddtrace.internal.openfeature._source_selection import REMOTE_CONFIG
    from ddtrace.internal.openfeature._source_selection import resolve_configuration_source

    if resolve_configuration_source(ffe_config) == REMOTE_CONFIG:
        from ddtrace.internal.openfeature._remoteconfiguration import disable_featureflags_rc

        disable_featureflags_rc()
