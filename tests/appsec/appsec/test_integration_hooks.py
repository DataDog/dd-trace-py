"""AppSec installs its downstream-request wrappers through the integrations' patch hooks."""

import http.client
from unittest import mock

import pytest
import wrapt

from ddtrace.internal import integration_hooks


@pytest.fixture(autouse=True)
def _reset_hooks(monkeypatch):
    monkeypatch.setattr(integration_hooks, "_entry_points", None)
    monkeypatch.setattr(integration_hooks, "_loaded", {})


def _wrapper_of(cls, attr):
    # Read the class dict: attribute access on the class returns a bound wrapper instead.
    function = vars(cls)[attr]
    return function._self_wrapper if isinstance(function, wrapt.FunctionWrapper) else None


@pytest.mark.parametrize("load_modules", [True, False])
def test_requests_patch_wraps_session_request_for_appsec(load_modules):
    import requests

    from ddtrace.appsec._common_module_patches import wrapped_request_D8CB81E472AF98A2
    from ddtrace.contrib.internal.requests.patch import patch
    from ddtrace.contrib.internal.requests.patch import unpatch
    from ddtrace.internal.settings.asm import config as asm_config

    unpatch()
    try:
        with mock.patch.object(asm_config, "_load_modules", load_modules):
            patch()
        wrapper = _wrapper_of(requests.Session, "request")
        assert (wrapper is wrapped_request_D8CB81E472AF98A2) is load_modules
    finally:
        unpatch()
    assert _wrapper_of(requests.Session, "request") is None


def test_httplib_patch_wraps_request_outside_the_integration():
    from ddtrace.appsec._contrib.httplib import _wrap_request as appsec_wrap_request
    from ddtrace.contrib.internal.httplib.patch import _wrap_request as httplib_wrap_request
    from ddtrace.contrib.internal.httplib.patch import patch
    from ddtrace.contrib.internal.httplib.patch import unpatch

    unpatch()
    try:
        patch()
        assert _wrapper_of(http.client.HTTPConnection, "request") is appsec_wrap_request
        assert vars(http.client.HTTPConnection)["request"].__wrapped__._self_wrapper is httplib_wrap_request
    finally:
        unpatch()
    # AppSec unwraps first, so unpatching removes both layers.
    assert _wrapper_of(http.client.HTTPConnection, "request") is None


@pytest.mark.parametrize("enabled", [True, False])
def test_httplib_appsec_wrapper_checks_config_per_call(enabled):
    from ddtrace.appsec._contrib.httplib import _wrap_request
    from ddtrace.internal.settings.asm import config as asm_config

    wrapped = mock.Mock(return_value=None)
    instance = mock.Mock()
    with (
        mock.patch.object(asm_config, "_asm_enabled", enabled),
        mock.patch.object(asm_config, "_ep_enabled", True),
        mock.patch("ddtrace.appsec._common_module_patches.wrapped_http_client_request") as analyze,
    ):
        _wrap_request(wrapped, instance, ("GET", "/"), {})

    if enabled:
        analyze.assert_called_once_with(wrapped, instance, ("GET", "/"), {})
        wrapped.assert_not_called()
    else:
        analyze.assert_not_called()
        wrapped.assert_called_once_with("GET", "/")
