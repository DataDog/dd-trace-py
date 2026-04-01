import sys

from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.trace import tracer


_APPSEC_TO_BE_LOADED = True
log = get_logger(__name__)


def _abort_appsec(failure_msg: str) -> None:
    """Disable AppSec and prevent it from being enabled through remote configuration

    This is called in case of non-recoverable AppSec load-time failure, such as a libddwaf loading error.
    """
    from ddtrace.trace import tracer

    log.warning("Disabling AppSec: libddwaf failed to load (%s)", failure_msg or "unknown error")

    if asm_config._asm_enabled:
        from ddtrace.internal.telemetry import telemetry_writer
        from ddtrace.internal.telemetry.constants import TELEMETRY_APM_PRODUCT

        telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.APPSEC, False)

    asm_config._asm_enabled = False
    asm_config._asm_can_be_enabled = False
    asm_config._asm_libddwaf_available = False
    asm_config._asm_rc_enabled = False
    asm_config._load_modules = False
    asm_config._ddwaf_version = "error"

    from ddtrace.appsec._remoteconfiguration import disable_appsec_rc

    disable_appsec_rc()

    tracer.configure(appsec_enabled=False)


def disable_appsec(reconfigure_tracer: bool = False) -> None:
    try:
        from ddtrace.appsec._processor import AppSecSpanProcessor
    except Exception as e:
        _abort_appsec(str(e))
        return

    AppSecSpanProcessor.disable()

    if asm_config._api_security_active:
        from ddtrace.appsec._api_security.api_manager import APIManager

        APIManager.disable()

    if reconfigure_tracer:
        tracer.configure(appsec_enabled=False)
    else:
        asm_config._asm_enabled = False

    return


def load_appsec(reconfigure_tracer: bool = False, origin: str = "") -> bool:
    """Lazily load the appsec module listeners."""
    try:
        from ddtrace.appsec._processor import AppSecSpanProcessor
    except Exception as e:
        _abort_appsec(str(e))
        return False

    from ddtrace.appsec._asm_request_context import asm_listen
    from ddtrace.appsec._contrib.aws_lambda import listen as aws_lambda_listen
    from ddtrace.appsec._contrib.django import listen as django_listen
    from ddtrace.appsec._contrib.fastapi import listen as fastapi_listen
    from ddtrace.appsec._contrib.flask import listen as flask_listen
    from ddtrace.appsec._contrib.stripe.handlers import listen as stripe_listen
    from ddtrace.appsec._contrib.tornado import listen as tornado_listen
    from ddtrace.appsec._handlers import listen
    # from ddtrace.appsec._contrib.grpc import listen as grpc_listen

    global _APPSEC_TO_BE_LOADED
    if _APPSEC_TO_BE_LOADED:
        listen()
        asm_listen()
        aws_lambda_listen()
        flask_listen()
        django_listen()
        fastapi_listen()
        import ddtrace.appsec._contrib.dbapi.subscribers  # noqa: F401
        import ddtrace.appsec._contrib.httpx.subscribers  # noqa: F401
        import ddtrace.appsec._contrib.open.subscribers  # noqa: F401
        import ddtrace.appsec._contrib.subprocess.subscribers  # noqa: F401

        stripe_listen()
        tornado_listen()

        # GRPC integration was disabled in commit 5fe1c163738c9e6d13127067f8eceee2302bcb67, deemed too unreliable
        # grpc_listen()

        core.on("asm.switch_state", _asm_switch_state)
        _APPSEC_TO_BE_LOADED = False

    from ddtrace.appsec._processor import AppSecSpanProcessor

    AppSecSpanProcessor.enable()
    if asm_config._api_security_enabled and not asm_config._api_security_active:
        from ddtrace.appsec._api_security.api_manager import APIManager

        APIManager.enable()

    if reconfigure_tracer:
        tracer.configure(appsec_enabled=True, appsec_enabled_origin=origin)
    else:
        asm_config._asm_enabled = True

    return True


def load_common_appsec_modules() -> None:
    """Lazily load the common module patches."""
    from ddtrace.internal.settings.asm import config as asm_config

    if asm_config._load_modules:
        from ddtrace.appsec._common_module_patches import patch_common_modules

        patch_common_modules()


# Test only helpers
# for tests that needs to load the appsec module later
core.on("test.config.override", load_common_appsec_modules)


def _asm_switch_state() -> None:
    if asm_config._asm_enabled:
        load_appsec()
    elif "ddtrace.appsec._processor" in sys.modules:
        disable_appsec()
