from unittest import mock

from ddtrace.appsec import _listeners
from ddtrace.appsec._contrib.dbapi.subscribers import AppSecDbApiSubscriber
from ddtrace.appsec._contrib.httpx.subscribers import AppSecHttpxRequestContextSubscriber
from ddtrace.appsec._contrib.httpx.subscribers import AppSecHttpxSingleRequestContextSubscriber
from ddtrace.appsec._contrib.subprocess.subscribers import AppSecSubprocessCommandSubscriber
from ddtrace.internal.products import manager
from ddtrace.internal.settings.asm import config as asm_config


EXPLOIT_PREVENTION_SUBSCRIBERS = (
    AppSecDbApiSubscriber,
    AppSecHttpxRequestContextSubscriber,
    AppSecHttpxSingleRequestContextSubscriber,
    AppSecSubprocessCommandSubscriber,
)


def _restore(was_enabled):
    if was_enabled:
        _listeners.load_appsec()
    else:
        _listeners.disable_appsec()


def test_appsec_activation_registers_exploit_prevention_subscribers():
    was_enabled = asm_config._asm_enabled
    try:
        with mock.patch.object(asm_config, "_ep_enabled", True):
            _listeners.disable_appsec()
            assert "appsec" not in manager._registered

            _listeners.load_appsec()
            assert manager._registered["appsec"] == EXPLOIT_PREVENTION_SUBSCRIBERS

            _listeners.disable_appsec()
            assert "appsec" not in manager._registered
    finally:
        _restore(was_enabled)


def test_appsec_without_exploit_prevention_registers_no_subscribers():
    was_enabled = asm_config._asm_enabled
    try:
        with mock.patch.object(asm_config, "_ep_enabled", False):
            _listeners.disable_appsec()
            _listeners.load_appsec()
            assert "appsec" not in manager._registered
    finally:
        _restore(was_enabled)
