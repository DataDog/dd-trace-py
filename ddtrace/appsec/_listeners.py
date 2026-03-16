import sys

from ddtrace.internal import core
from ddtrace.internal.settings.asm import config as asm_config


_APPSEC_TO_BE_LOADED = True


def stop_appsec() -> None:
    from ddtrace.appsec._processor import AppSecSpanProcessor

    AppSecSpanProcessor.disable()

    if asm_config._api_security_active:
        from ddtrace.appsec._api_security.api_manager import APIManager

        APIManager.disable()


def load_appsec() -> None:
    """Lazily load the appsec module listeners."""
    from ddtrace.appsec._asm_request_context import asm_listen
    from ddtrace.appsec._contrib.aws_lambda import listen as aws_lambda_listen
    from ddtrace.appsec._contrib.django import listen as django_listen
    from ddtrace.appsec._contrib.fastapi import listen as fastapi_listen
    from ddtrace.appsec._contrib.flask import listen as flask_listen
    from ddtrace.appsec._contrib.httpx import listen as httpx_listen
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
        httpx_listen()
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


def load_common_appsec_modules() -> None:
    """Lazily load the common module patches."""
    from ddtrace.internal.settings.asm import config as asm_config

    if asm_config._load_modules:
        from ddtrace.appsec._common_module_patches import patch_common_modules

        patch_common_modules()


# for tests that needs to load the appsec module later
core.on("test.config.override", load_common_appsec_modules)


def _asm_switch_state() -> None:
    if asm_config._asm_enabled:
        load_appsec()
    elif "ddtrace.appsec._processor" in sys.modules:
        stop_appsec()
