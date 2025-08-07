from ddtrace.settings.asm import config


requires = ["remote-configuration"]


def post_preload():
    pass


def set_main_listener():
    """Set the main appsec listener to be used by the event hub."""
    from ddtrace.appsec._listeners import set_processor_listener

    set_processor_listener()


def start():
    if config._asm_rc_enabled:
        from ddtrace.appsec._remoteconfiguration import enable_appsec_rc

        enable_appsec_rc()
    set_main_listener()


def restart(join=False):
    if config._asm_rc_enabled:
        from ddtrace.appsec._remoteconfiguration import _forksafe_appsec_rc

        _forksafe_appsec_rc()


def stop(join=False):
    pass


def at_exit(join=False):
    pass
