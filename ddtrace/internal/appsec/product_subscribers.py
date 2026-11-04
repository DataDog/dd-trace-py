"""AppSec-owned framework listeners and operation subscriber bundles."""

from typing import Callable

from ddtrace.internal import core
from ddtrace.internal.products import SubscriberBundle
from ddtrace.internal.settings.asm import config


EXPLOIT_PREVENTION_SUBSCRIBERS = (
    SubscriberBundle(
        module="ddtrace.appsec._contrib.dbapi.subscribers",
        subscribers=("AppSecDbApiSubscriber",),
    ),
    SubscriberBundle(
        module="ddtrace.appsec._contrib.httpx.subscribers",
        subscribers=("AppSecHttpxRequestContextSubscriber", "AppSecHttpxSingleRequestContextSubscriber"),
    ),
    SubscriberBundle(
        module="ddtrace.appsec._contrib.subprocess.subscribers",
        subscribers=("AppSecSubprocessCommandSubscriber",),
    ),
)


_listeners_registered = False


def register(asm_switch_state: Callable[[], None]) -> None:
    if not config._asm_enabled:
        return

    global _listeners_registered
    if not _listeners_registered:
        _register_listeners(asm_switch_state)
        _listeners_registered = True

    if config._ep_enabled:
        for bundle in EXPLOIT_PREVENTION_SUBSCRIBERS:
            bundle.register()


def unregister() -> None:
    # These listeners inspect operations; request cleanup is owned by asm_listen().
    for bundle in EXPLOIT_PREVENTION_SUBSCRIBERS:
        bundle.unregister()


def _register_listeners(asm_switch_state: Callable[[], None]) -> None:
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
    # from ddtrace.appsec._contrib.grpc import listen as grpc_listen

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
    # grpc_listen()

    core.on("asm.switch_state", asm_switch_state)
