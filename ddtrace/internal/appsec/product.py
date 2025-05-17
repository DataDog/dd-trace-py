from ddtrace.internal.logger import get_logger
from ddtrace.settings._config import config as global_config
from ddtrace.settings.asm import config


requires = ["remote-configuration"]


log = get_logger(__name__)


def _start_appsec_processor():
    try:
        from ddtrace.appsec._processor import AppSecSpanProcessor

        AppSecSpanProcessor().register()
    except Exception as e:
        # DDAS-001-01
        log.error(
            "[DDAS-001-01] "
            "AppSec could not start because of an unexpected error. No security activities will "
            "be collected. "
            "Please contact support at https://docs.datadoghq.com/help/ for help. Error details: "
            "\n%s",
            repr(e),
        )
        if global_config._raise:
            raise


def post_preload():
    pass


def start():
    if config._asm_rc_enabled:
        from ddtrace.appsec._remoteconfiguration import enable_appsec_rc

        enable_appsec_rc()

    if config._asm_libddwaf_available:
        if config._asm_enabled:
            if config._api_security_enabled:
                from ddtrace.appsec._api_security.api_manager import APIManager

                APIManager.enable()

            _start_appsec_processor()
        else:
            # api_security_active will keep track of the service status of APIManager
            # we don't want to import the module if it was not started before due to
            # one click activation of ASM via Remote Config
            if config._api_security_active:
                from ddtrace.appsec._api_security.api_manager import APIManager

                APIManager.disable()

    if config._iast_enabled:
        from ddtrace.appsec._iast.processor import AppSecIastSpanProcessor

        AppSecIastSpanProcessor().register()


def restart(join=False):
    if config._asm_rc_enabled:
        from ddtrace.appsec._remoteconfiguration import _forksafe_appsec_rc

        _forksafe_appsec_rc()


def stop(join=False):
    pass


def at_exit(join=False):
    pass
