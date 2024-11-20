#!/usr/bin/env python3

from http.client import HTTPResponse
from io import BytesIO
import logging
from unittest import mock

from ddtrace.internal.http import HTTPConnection
from ddtrace.settings.endpoint_config import fetch_config_from_endpoint
from tests.utils import override_env


def mock_getresponse_enabled_after_4_retries(self):
    if not hasattr(mock_getresponse_enabled_after_4_retries, "call_count"):
        mock_getresponse_enabled_after_4_retries.call_count = 0

    mock_getresponse_enabled_after_4_retries.call_count += 1

    response = mock.Mock(spec=HTTPResponse)
    response.chunked = False
    if mock_getresponse_enabled_after_4_retries.call_count < 4:
        response.read.return_value = b"{}"
        response.status = 500
        response.reason = "KO"
    else:
        response.read.return_value = b'{"dd_iast_enabled": true}'
        response.status = 200
        response.reason = "OK"
    response.fp = BytesIO(response.read.return_value)
    response.length = len(response.fp.getvalue())
    response.msg = {"Content-Length": response.length}
    return response


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
    with caplog.at_level(logging.DEBUG, logger="ddtrace"):
        assert fetch_config_from_endpoint() == {}
    if caplog.text:
        assert "Configuration endpoint not set. Skipping fetching configuration." in caplog.text


def test_set_config_endpoint_enabled(caplog):
    with caplog.at_level(logging.DEBUG, logger="ddtrace"), override_env(
        {"_DD_CONFIG_ENDPOINT": "http://localhost:80"}
    ), mock.patch.object(HTTPConnection, "connect", new=mock_pass), mock.patch.object(
        HTTPConnection, "send", new=mock_pass
    ), mock.patch.object(
        HTTPConnection, "getresponse", new=mock_getresponse_enabled
    ):
        assert fetch_config_from_endpoint() == {"dd_iast_enabled": True}
    if caplog.text:
        assert "Configuration endpoint not set. Skipping fetching configuration." not in caplog.text
        assert "Failed to fetch configuration from endpoint" not in caplog.text


def test_set_config_endpoint_500(caplog):
    with caplog.at_level(logging.DEBUG, logger="ddtrace"), override_env(
        {"_DD_CONFIG_ENDPOINT": "http://localhost:80"}
    ), mock.patch.object(HTTPConnection, "connect", new=mock_pass), mock.patch.object(
        HTTPConnection, "send", new=mock_pass
    ), mock.patch.object(
        HTTPConnection, "getresponse", new=mock_getresponse_500
    ):
        assert fetch_config_from_endpoint() == {}
    if caplog.text:
        assert "Failed to fetch configuration from endpoint" in caplog.text
        assert "RetryError: Response(status=500" in caplog.text


def test_set_config_endpoint_403(caplog):
    with caplog.at_level(logging.DEBUG, logger="ddtrace"), override_env(
        {"_DD_CONFIG_ENDPOINT": "http://localhost:80"}
    ), mock.patch.object(HTTPConnection, "connect", new=mock_pass), mock.patch.object(
        HTTPConnection, "send", new=mock_pass
    ), mock.patch.object(
        HTTPConnection, "getresponse", new=mock_getresponse_403
    ):
        assert fetch_config_from_endpoint() == {}
    if caplog.text:
        assert "Failed to fetch configuration from endpoint" in caplog.text
        assert "RetryError: Response(status=403" in caplog.text


def test_set_config_endpoint_malformed(caplog):
    with caplog.at_level(logging.DEBUG, logger="ddtrace"), override_env(
        {"_DD_CONFIG_ENDPOINT": "http://localhost:80"}
    ), mock.patch.object(HTTPConnection, "connect", new=mock_pass), mock.patch.object(
        HTTPConnection, "send", new=mock_pass
    ), mock.patch.object(
        HTTPConnection, "getresponse", new=mock_getresponse_malformed
    ):
        assert fetch_config_from_endpoint() == {}
    if caplog.text:
        assert "Unable to parse Datadog Agent JSON" in caplog.text


def test_set_config_endpoint_connection_refused(caplog):
    with caplog.at_level(logging.DEBUG, logger="ddtrace"), override_env({"_DD_CONFIG_ENDPOINT": "http://localhost:80"}):
        assert fetch_config_from_endpoint() == {}

    if caplog.text:
        assert "Failed to fetch configuration from endpoint" in caplog.text
        assert any(
            message in caplog.text for message in ("Connection refused", "Address family not supported by protocol")
        ), "None of the expected connection error log messages were found"


def test_set_config_endpoint_timeout_error(caplog):
    with caplog.at_level(logging.DEBUG, logger="ddtrace"), override_env(
        {"_DD_CONFIG_ENDPOINT": "http://localhost:80"}
    ), mock.patch("ddtrace.internal.utils.http.get_connection", side_effect=TimeoutError):
        assert fetch_config_from_endpoint() == {}

    if caplog.text:
        assert "Configuration endpoint not set. Skipping fetching configuration." not in caplog.text
        assert "Failed to fetch configuration from endpoint" in caplog.text
        assert any(
            message in caplog.text for message in ("Connection refused", "Address family not supported by protocol")
        ), "None of the expected connection error log messages were found"


def test_set_config_endpoint_retries(caplog):
    with caplog.at_level(logging.DEBUG, logger="ddtrace"), override_env(
        {"_DD_CONFIG_ENDPOINT": "http://localhost:80"}
    ), mock.patch.object(HTTPConnection, "connect", new=mock_pass), mock.patch.object(
        HTTPConnection, "send", new=mock_pass
    ), mock.patch.object(
        HTTPConnection, "getresponse", new=mock_getresponse_enabled_after_4_retries
    ), mock.patch(
        "ddtrace.settings.endpoint_config._get_retries", return_value=5
    ):
        assert fetch_config_from_endpoint() == {"dd_iast_enabled": True}
