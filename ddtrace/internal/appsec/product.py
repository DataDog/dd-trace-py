from ddtrace.internal.settings.asm import config


requires = ["remote-configuration"]


def post_preload():
    pass


def enabled():
    return config._asm_enabled or config._asm_can_be_enabled or config._asm_rc_enabled


def start():
    if config._asm_enabled or config._asm_can_be_enabled:
        from ddtrace.appsec._listeners import load_common_appsec_modules

        load_common_appsec_modules()

    if config._asm_rc_enabled:
        from ddtrace.appsec._remoteconfiguration import enable_appsec_rc

        enable_appsec_rc()

    if config._asm_enabled:
        from ddtrace.appsec._listeners import load_appsec

        load_appsec(reconfigure_tracer=False)


def restart(join=False):
    pass


def stop(join=False):
    pass
