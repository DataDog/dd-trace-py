from typing import TYPE_CHECKING
from typing import Any
from typing import Union

from ddtrace.internal.settings.asm import config


if TYPE_CHECKING:
    from ddtrace.internal.core.subscriber import ContextSubscriber
    from ddtrace.internal.core.subscriber import Subscriber


requires = ["remote-configuration"]

_listeners_registered = False
_exploit_prevention_registered = False


def post_preload() -> None:
    pass


def enabled() -> bool:
    return bool(config._asm_enabled or config._asm_can_be_enabled or config._asm_rc_enabled)


def activated() -> bool:
    # Eligibility for remote activation starts the lifecycle but does not make AppSec active.
    return bool(config._asm_enabled)


def start() -> None:
    if config._asm_enabled or config._asm_can_be_enabled:
        # The product owns common-patch setup for both static and remote activation.
        from ddtrace.appsec._listeners import load_common_appsec_modules

        load_common_appsec_modules()

    if config._asm_rc_enabled:
        from ddtrace.appsec._remoteconfiguration import AppSecCallback
        from ddtrace.appsec._remoteconfiguration import enable_appsec_rc

        enable_appsec_rc(AppSecCallback(_enable_asm, _disable_asm))

    if config._asm_enabled:
        from ddtrace.appsec._listeners import load_appsec

        load_appsec(reconfigure_tracer=False)


def register_subscribers() -> None:
    # Remote configuration activates AppSec outside of start(), so _enable_asm() calls this too.
    global _listeners_registered, _exploit_prevention_registered
    if not config._asm_enabled:
        return
    if not _listeners_registered:
        _register_listeners()
        _listeners_registered = True
    if config._ep_enabled and not _exploit_prevention_registered:
        for subscriber in _exploit_prevention_subscribers():
            subscriber.register()
        _exploit_prevention_registered = True


def unregister_subscribers() -> None:
    # The listeners stay registered: their handlers check whether AppSec is enabled.
    global _exploit_prevention_registered
    if not _exploit_prevention_registered:
        return
    for subscriber in _exploit_prevention_subscribers():
        subscriber.unregister()
    _exploit_prevention_registered = False


def restart(join: bool = False) -> None:
    pass


def stop(join: bool = False) -> None:
    pass


def _register_listeners() -> None:
    from ddtrace.appsec._asm_request_context import asm_listen
    from ddtrace.appsec._contrib.aiohttp import listen as aiohttp_listen
    from ddtrace.appsec._contrib.aws_lambda import listen as aws_lambda_listen
    from ddtrace.appsec._contrib.django import listen as django_listen
    from ddtrace.appsec._contrib.fastapi import listen as fastapi_listen
    from ddtrace.appsec._contrib.flask import listen as flask_listen
    from ddtrace.appsec._contrib.openai.handlers import listen as openai_listen
    from ddtrace.appsec._contrib.stripe.handlers import listen as stripe_listen
    from ddtrace.appsec._contrib.tornado import listen as tornado_listen
    from ddtrace.appsec._handlers import listen
    from ddtrace.appsec._listeners import _asm_switch_state
    from ddtrace.internal import core

    listen()
    asm_listen()
    aws_lambda_listen()
    flask_listen()
    django_listen()
    fastapi_listen()
    openai_listen()
    stripe_listen()
    tornado_listen()
    aiohttp_listen()
    # GRPC integration was disabled in commit 5fe1c163738c9e6d13127067f8eceee2302bcb67, deemed too unreliable
    # from ddtrace.appsec._contrib.grpc import listen as grpc_listen
    # grpc_listen()

    core.on("asm.switch_state", _asm_switch_state)


def _exploit_prevention_subscribers() -> tuple[Union[type["Subscriber"], type["ContextSubscriber[Any]"]], ...]:
    from ddtrace.appsec._contrib.dbapi.subscribers import AppSecDbApiSubscriber
    from ddtrace.appsec._contrib.httpx.subscribers import AppSecHttpxRequestContextSubscriber
    from ddtrace.appsec._contrib.httpx.subscribers import AppSecHttpxSingleRequestContextSubscriber
    from ddtrace.appsec._contrib.subprocess.subscribers import AppSecSubprocessCommandSubscriber

    return (
        AppSecDbApiSubscriber,
        AppSecHttpxRequestContextSubscriber,
        AppSecHttpxSingleRequestContextSubscriber,
        AppSecSubprocessCommandSubscriber,
    )


def _disable_asm() -> None:
    if config._asm_enabled:
        from ddtrace.appsec._listeners import disable_appsec
        from ddtrace.internal.telemetry import telemetry_writer
        from ddtrace.internal.telemetry.constants import TELEMETRY_APM_PRODUCT

        disable_appsec(reconfigure_tracer=True)
        unregister_subscribers()
        if not config._asm_enabled:
            telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.APPSEC, False)


def _enable_asm() -> None:
    if config._asm_can_be_enabled and not config._asm_enabled:
        from ddtrace.appsec._constants import APPSEC
        from ddtrace.appsec._listeners import load_appsec
        from ddtrace.internal.telemetry import telemetry_writer
        from ddtrace.internal.telemetry.constants import TELEMETRY_APM_PRODUCT

        if load_appsec(reconfigure_tracer=True, origin=APPSEC.ENABLED_ORIGIN_RC):
            register_subscribers()
            telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.APPSEC, True)
