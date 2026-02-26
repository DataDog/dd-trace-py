from ddtrace.internal.settings.openfeature import config as ffe_config


requires = ["remote-configuration"]


def post_preload():
    pass


def enabled():
    return ffe_config.experimental_flagging_provider_enabled


def start():
    from ddtrace.internal.openfeature._remoteconfiguration import enable_featureflags_rc

    enable_featureflags_rc()


def restart(join=False):
    pass


def stop(join=False):
    from ddtrace.internal.openfeature._remoteconfiguration import disable_featureflags_rc

    disable_featureflags_rc()


def at_exit(join=False):
    pass
