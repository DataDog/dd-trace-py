"""Shared AppSec subscriber state for product and listener activation paths."""

from typing import Callable
from typing import Protocol

from ddtrace.internal.settings.asm import config


class _Subscriber(Protocol):
    def register(self) -> None: ...

    def unregister(self) -> None: ...


_listeners_registered = False
_exploit_prevention_registered = False


def register(asm_switch_state: Callable[[], None]) -> None:
    if not config._asm_enabled:
        return

    global _listeners_registered
    if not _listeners_registered:
        _register_listeners(asm_switch_state)
        _listeners_registered = True

    if config._ep_enabled:
        global _exploit_prevention_registered
        if _exploit_prevention_registered:
            return
        for subscriber in _exploit_prevention_subscribers():
            subscriber.register()
        _exploit_prevention_registered = True


def unregister() -> None:
    # Framework listeners stay registered; their handlers check whether AppSec is enabled.
    global _exploit_prevention_registered
    if not _exploit_prevention_registered:
        return
    for subscriber in _exploit_prevention_subscribers():
        subscriber.unregister()
    _exploit_prevention_registered = False


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

    core.on("asm.switch_state", asm_switch_state)


def _exploit_prevention_subscribers() -> tuple[_Subscriber, ...]:
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
