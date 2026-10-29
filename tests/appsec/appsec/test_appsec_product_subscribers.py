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
def subscribers():
    was_enabled = asm_config._asm_enabled
    _listeners.disable_appsec()
    with ExitStack() as stack:
        yield {
            subscriber: (
                stack.enter_context(mock.patch.object(subscriber, "register")),
                stack.enter_context(mock.patch.object(subscriber, "unregister")),
            )
            for subscriber in EXPLOIT_PREVENTION_SUBSCRIBERS
        }
        _listeners.disable_appsec()
    if was_enabled:
        _listeners.load_appsec()


def test_appsec_activation_registers_exploit_prevention_subscribers_once(subscribers):
    with mock.patch.object(asm_config, "_ep_enabled", True):
        _listeners.load_appsec()
        # The product manager registers again after start(), which already loaded AppSec.
        product.register_subscribers()

        for register, unregister in subscribers.values():
            register.assert_called_once_with()
            unregister.assert_not_called()

        _listeners.disable_appsec()
        product.unregister_subscribers()

        for register, unregister in subscribers.values():
            register.assert_called_once_with()
            unregister.assert_called_once_with()


def test_appsec_without_exploit_prevention_registers_no_subscribers(subscribers):
    with mock.patch.object(asm_config, "_ep_enabled", False):
        _listeners.load_appsec()
        _listeners.disable_appsec()

    for register, unregister in subscribers.values():
        register.assert_not_called()
        unregister.assert_not_called()


def test_inactive_appsec_registers_no_subscribers(subscribers):
    with mock.patch.object(asm_config, "_ep_enabled", True):
        product.register_subscribers()

    for register, _ in subscribers.values():
        register.assert_not_called()
