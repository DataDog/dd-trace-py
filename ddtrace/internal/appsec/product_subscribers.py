"""Shared AppSec subscriber state for product and listener activation paths."""

from typing import Protocol

from ddtrace.internal.settings.asm import config


class _Subscriber(Protocol):
    def register(self) -> None: ...

    def unregister(self) -> None: ...


_registered = False


def register() -> None:
    global _registered
    if _registered or not (config._asm_enabled and config._ep_enabled):
        return
    for subscriber in _exploit_prevention_subscribers():
        subscriber.register()
    _registered = True


def unregister() -> None:
    global _registered
    if not _registered:
        return
    for subscriber in _exploit_prevention_subscribers():
        subscriber.unregister()
    _registered = False


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
