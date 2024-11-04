#!/usr/bin/env python3

from http.client import HTTPResponse
from io import BytesIO
from unittest import mock

from ddtrace.internal.http import HTTPConnection
from ddtrace.settings.endpoint_config import fetch_config_from_endpoint
from tests.utils import override_env


def test_ensure_this_is_running():
    assert False


def mock_getresponse_enabled(self):
    response = mock.Mock(spec=HTTPResponse)
    response.read.return_value = b'{"dd_iast_enabled": true}'
    response.status = 200
    response.reason = "OK"
    response.chunked = False
    response.fp = BytesIO(response.read.return_value)
    response.length = len(response.fp.getvalue())
    response.msg = {"Content-Length": response.length}
    return response


def mock_getresponse_403(self):
    response = mock.Mock(spec=HTTPResponse)
    response.read.return_value = b'{"dd_iast_enabled": true}'
    response.status = 403
    response.reason = "KO"
    response.chunked = False
    response.fp = BytesIO(response.read.return_value)
    response.length = len(response.fp.getvalue())
    response.msg = {"Content-Length": response.length}
    return response


def mock_getresponse_500(self):
    response = mock.Mock(spec=HTTPResponse)
    response.read.return_value = b'{"dd_iast_enabled": true}'
    response.status = 500
    response.reason = "KO"
    response.chunked = False
    response.fp = BytesIO(response.read.return_value)
    response.length = len(response.fp.getvalue())
    response.msg = {"Content-Length": response.length}
    return response


def mock_getresponse_malformed(self):
    response = mock.Mock(spec=HTTPResponse)
    response.read.return_value = b"{"
    response.status = 200
    response.reason = "OK"
    response.chunked = False
    response.fp = BytesIO(response.read.return_value)
    response.length = len(response.fp.getvalue())
    response.msg = {"Content-Length": response.length}
    return response


def mock_pass(self, *args, **kwargs):
    pass


def test_unset_config_endpoint(caplog):
    caplog.set_level(10)
    with override_env({"DD_TRACE_DEBUG": "true"}):
        assert fetch_config_from_endpoint() == {}
    assert "Configuration endpoint not set. Skipping fetching configuration." in caplog.text


def test_set_config_endpoint_enabled(caplog):
    caplog.set_level(10)
    with override_env({"_DD_CONFIG_ENDPOINT": "http://localhost:80", "DD_TRACE_DEBUG": "true"}), mock.patch.object(
        HTTPConnection, "connect", new=mock_pass
    ), mock.patch.object(HTTPConnection, "send", new=mock_pass), mock.patch.object(
        HTTPConnection, "getresponse", new=mock_getresponse_enabled
    ):
        assert fetch_config_from_endpoint() == {"dd_iast_enabled": True}
    assert "Configuration endpoint not set. Skipping fetching configuration." not in caplog.text
    assert "Failed to fetch configuration from endpoint" not in caplog.text


def test_set_config_endpoint_500(caplog):
    caplog.set_level(10)
    with override_env({"_DD_CONFIG_ENDPOINT": "http://localhost:80"}), mock.patch.object(
        HTTPConnection, "connect", new=mock_pass
    ), mock.patch.object(HTTPConnection, "send", new=mock_pass), mock.patch.object(
        HTTPConnection, "getresponse", new=mock_getresponse_500
    ):
        assert fetch_config_from_endpoint() == {}
    assert "Failed to fetch configuration from endpoint, status code: 500" in caplog.text


def test_set_config_endpoint_403(caplog):
    caplog.set_level(10)
    with override_env({"_DD_CONFIG_ENDPOINT": "http://localhost:80"}), mock.patch.object(
        HTTPConnection, "connect", new=mock_pass
    ), mock.patch.object(HTTPConnection, "send", new=mock_pass), mock.patch.object(
        HTTPConnection, "getresponse", new=mock_getresponse_403
    ):
        assert fetch_config_from_endpoint() == {}
    assert "Failed to fetch configuration from endpoint, status code: 403" in caplog.text


def test_set_config_endpoint_malformed(caplog):
    caplog.set_level(10)
    with override_env({"_DD_CONFIG_ENDPOINT": "http://localhost:80"}), mock.patch.object(
        HTTPConnection, "connect", new=mock_pass
    ), mock.patch.object(HTTPConnection, "send", new=mock_pass), mock.patch.object(
        HTTPConnection, "getresponse", new=mock_getresponse_malformed
    ):
        assert fetch_config_from_endpoint() == {}
    assert "Failed to fetch configuration from endpoint" in caplog.text
    assert "Expecting property name enclosed in double quotes" in caplog.text


def test_set_config_endpoint_connection_refused(caplog):
    caplog.set_level(10)
    with override_env({"_DD_CONFIG_ENDPOINT": "http://localhost:80"}):
        assert fetch_config_from_endpoint() == {}
    assert "Failed to fetch configuration from endpoint" in caplog.text
    assert "Connection refused" in caplog.text
