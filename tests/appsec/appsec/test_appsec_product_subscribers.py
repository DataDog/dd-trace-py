from contextlib import ExitStack
from unittest import mock

import pytest

from ddtrace.appsec import _listeners
from ddtrace.appsec._contrib.dbapi.subscribers import AppSecDbApiSubscriber
from ddtrace.appsec._contrib.httpx.subscribers import AppSecHttpxRequestContextSubscriber
from ddtrace.appsec._contrib.httpx.subscribers import AppSecHttpxSingleRequestContextSubscriber
from ddtrace.appsec._contrib.subprocess.subscribers import AppSecSubprocessCommandSubscriber
from ddtrace.internal.appsec import product
from ddtrace.internal.settings.asm import config as asm_config


EXPLOIT_PREVENTION_SUBSCRIBERS = (
    AppSecDbApiSubscriber,
    AppSecHttpxRequestContextSubscriber,
    AppSecHttpxSingleRequestContextSubscriber,
    AppSecSubprocessCommandSubscriber,
)


@pytest.fixture
def subscribers(monkeypatch):
    was_enabled = asm_config._asm_enabled
    monkeypatch.setattr(asm_config, "_asm_can_be_enabled", True)
    monkeypatch.setattr(asm_config, "_asm_enabled_origin", asm_config._asm_enabled_origin)
    _listeners.disable_appsec()
    product.unregister_subscribers()
    with ExitStack() as stack:
        yield {
            subscriber: (
                stack.enter_context(mock.patch.object(subscriber, "register")),
                stack.enter_context(mock.patch.object(subscriber, "unregister")),
            )
            for subscriber in EXPLOIT_PREVENTION_SUBSCRIBERS
        }
        _listeners.disable_appsec()
        product.unregister_subscribers()
    if was_enabled:
        _listeners.load_appsec()
        product.register_subscribers()


def test_remote_activation_registers_exploit_prevention_subscribers_once(subscribers):
    with mock.patch.object(asm_config, "_ep_enabled", True):
        product._enable_asm()
        # Registering again, as the product manager does after start(), does nothing.
        product.register_subscribers()

        for register, unregister in subscribers.values():
            register.assert_called_once_with()
            unregister.assert_not_called()

        product._disable_asm()
        product.unregister_subscribers()

        for register, unregister in subscribers.values():
            register.assert_called_once_with()
            unregister.assert_called_once_with()


def test_remote_activation_without_exploit_prevention_registers_no_subscribers(subscribers):
    with mock.patch.object(asm_config, "_ep_enabled", False):
        product._enable_asm()
        product._disable_asm()

    for register, unregister in subscribers.values():
        register.assert_not_called()
        unregister.assert_not_called()


def test_inactive_appsec_registers_no_subscribers(subscribers):
    with mock.patch.object(asm_config, "_ep_enabled", True):
        product.register_subscribers()

    for register, _ in subscribers.values():
        register.assert_not_called()


def test_appsec_activation_registers_listeners_once(subscribers, monkeypatch):
    monkeypatch.setattr(product, "_listeners_registered", False)
    with mock.patch.object(product, "_register_listeners") as register_listeners:
        product.register_subscribers()
        register_listeners.assert_not_called()

        product._enable_asm()
        product.register_subscribers()
        product._disable_asm()
        product._enable_asm()

    register_listeners.assert_called_once_with()
