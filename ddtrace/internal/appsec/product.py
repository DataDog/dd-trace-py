from ddtrace.settings.asm import config


requires = ["remote-configuration"]


def post_preload():
    pass


def start():
    if config._asm_rc_enabled:
        from ddtrace.appsec._remoteconfiguration import enable_appsec_rc

        enable_appsec_rc()
    if config._asm_enabled:
        from ddtrace.appsec._listeners import load_appsec

        load_appsec()
        if config._api_security_enabled:
            from ddtrace.appsec._api_security.api_manager import APIManager

            APIManager.enable()

    if config._iast_enabled:
        from ddtrace.appsec._iast.processor import AppSecIastSpanProcessor

        AppSecIastSpanProcessor.enable()


def restart(join=False):
    if config._asm_rc_enabled:
        from ddtrace.appsec._remoteconfiguration import _forksafe_appsec_rc

        _forksafe_appsec_rc()


def stop(join=False):
    pass


def at_exit(join=False):
    pass
