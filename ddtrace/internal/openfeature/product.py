from ddtrace.internal.settings.openfeature import config as ffe_config


requires = ["remote-configuration"]


def post_preload():
    pass


def start():
    if ffe_config.experimental_flagging_provider_enabled:
        from ddtrace.internal.openfeature._remoteconfiguration import enable_featureflags_rc

        enable_featureflags_rc()


def restart(join=False):
    if ffe_config.experimental_flagging_provider_enabled:
        from ddtrace.internal.openfeature._remoteconfiguration import _forksafe_featureflags_rc

        _forksafe_featureflags_rc()


def stop(join=False):
    if ffe_config.experimental_flagging_provider_enabled:
        from ddtrace.internal.openfeature._remoteconfiguration import disable_featureflags_rc

        disable_featureflags_rc()


def at_exit(join=False):
    pass
