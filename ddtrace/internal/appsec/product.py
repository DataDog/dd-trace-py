from ddtrace.settings.asm import config as asm_config


requires = ["remote-configuration"]


def post_preload():
    pass


def start():
    if asm_config._asm_rc_enabled:
        from ddtrace.appsec._remoteconfiguration import enable_appsec_rc

        enable_appsec_rc()


def restart(join=False):
    if asm_config._asm_rc_enabled:
        from ddtrace.appsec._remoteconfiguration import _forksafe_appsec_rc

        _forksafe_appsec_rc()


def stop(join=False):
    pass


def at_exit(join=False):
    pass
