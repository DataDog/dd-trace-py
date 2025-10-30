from ddtrace.internal.settings.asm import ai_guard_config
from ddtrace.internal.settings.asm import config


requires = ["remote-configuration"]


def post_preload():
    pass


def start():
    if config._asm_enabled or config._asm_can_be_enabled:
        from ddtrace.appsec._listeners import load_common_appsec_modules

        load_common_appsec_modules()

    if config._asm_rc_enabled:
        from ddtrace.appsec._remoteconfiguration import enable_appsec_rc

        enable_appsec_rc()

    if config._asm_enabled:
        from ddtrace.appsec._listeners import load_appsec

        load_appsec()

    if ai_guard_config._ai_guard_enabled:
        from ddtrace.appsec._ai_guard import init_ai_guard

        init_ai_guard()


def restart(join=False):
    if config._asm_rc_enabled:
        from ddtrace.appsec._remoteconfiguration import _forksafe_appsec_rc

        _forksafe_appsec_rc()


def stop(join=False):
    pass


def at_exit(join=False):
    pass
