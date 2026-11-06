"""Tests for ddtrace.testing.internal.http module."""

import logging
import os
import socket
import subprocess
import sys
import tempfile
import types
import typing as t
from unittest.mock import Mock
from unittest.mock import call
from unittest.mock import patch

import pytest

from ddtrace.internal.native import ConnectionFailedError
from ddtrace.internal.native import HttpIoError
from ddtrace.internal.native import TimedOutError
from ddtrace.testing.internal.errors import SetupError
import ddtrace.testing.internal.http as _http_module
from ddtrace.testing.internal.http import DEFAULT_TIMEOUT_SECONDS
from ddtrace.testing.internal.http import MAX_RETRY_AFTER_SECONDS
from ddtrace.testing.internal.http import BackendConnector
from ddtrace.testing.internal.http import BackendConnectorAgentlessSetup
from ddtrace.testing.internal.http import BackendConnectorEVPProxySetup
from ddtrace.testing.internal.http import BackendConnectorSetup
from ddtrace.testing.internal.http import FileAttachment
from ddtrace.testing.internal.http import Subdomain
from ddtrace.testing.internal.telemetry import ErrorType
from tests.testing.mocks import mock_backend_connector
from tests.utils import reinitialize_agentless_config


_TIMEOUT_MS = int(DEFAULT_TIMEOUT_SECONDS * 1000)


def _post_call(path: str, body: bytes, headers: dict):
    """The call a POST to path makes on the native client."""
    return call(path, headers=list(headers.items()), timeout_ms=_TIMEOUT_MS, body=body)


def _headers(headers: dict) -> "Mock":
    """A native response's case-insensitive header lookup, for header.side_effect."""
    lowered = {name.lower(): str(value) for name, value in headers.items()}
    return lambda name: lowered.get(name.lower())


class TestBackendConnector:
    """Tests for BackendConnector class."""

    def test_constants(self) -> None:
        """Test module constants."""
        # Verify the parsing function returns the correct default when no env var is set
        assert _http_module._parse_timeout_millis(None) == 30.0
        # Verify the module constant matches what it should be for the current environment
        # (may be overridden if env var was set at module import time)
        expected = _http_module._parse_timeout_millis(os.environ.get("DD_CIVISIBILITY_BACKEND_API_TIMEOUT_MILLIS"))
        assert DEFAULT_TIMEOUT_SECONDS == expected

    @patch("ddtrace.testing.internal.http.HTTPClient")
    def test_init_default_parameters(self, mock_http_client: Mock) -> None:
        """Test BackendConnector initialization with default parameters."""
        connector = BackendConnector(url="https://api.example.com", use_gzip=True)

        mock_http_client.assert_called_once_with(
            "https://api.example.com", timeout_ms=_TIMEOUT_MS, treat_http_errors_as_errors=False
        )
        assert connector.default_headers == {"Accept-Encoding": "gzip"}
        assert connector.base_path == ""

    @patch("ddtrace.testing.internal.http.HTTPClient")
    def test_init_custom_parameters(self, mock_http_client: Mock) -> None:
        """Test BackendConnector initialization with custom parameters."""
        connector = BackendConnector(url="https://api.example.com/some-path", use_gzip=False)

        mock_http_client.assert_called_once_with(
            "https://api.example.com", timeout_ms=_TIMEOUT_MS, treat_http_errors_as_errors=False
        )
        assert connector.default_headers == {}
        assert connector.base_path == "/some-path"

    @patch("ddtrace.testing.internal.http.HTTPClient")
    def test_init_unix_domain_socket(self, mock_http_client: Mock) -> None:
        """Test BackendConnector initialization with Unix domain socket URL."""
        connector = BackendConnector(url="unix:///some/path/name", base_path="/evp_proxy/over9000", use_gzip=False)

        mock_http_client.assert_called_once_with(
            "unix:///some/path/name", timeout_ms=_TIMEOUT_MS, treat_http_errors_as_errors=False
        )
        assert connector.default_headers == {}
        assert connector.base_path == "/evp_proxy/over9000"

    @patch("ddtrace.testing.internal.http.HTTPClient")
    def test_init_unix_domain_socket_without_base_path(self, mock_http_client: Mock) -> None:
        """The path of a unix:// URL is the socket's, not a prefix of the request paths."""
        connector = BackendConnector(url="unix:///var/run/datadog/apm.socket")

        assert connector.base_url == "unix:///var/run/datadog/apm.socket"
        assert connector.base_path == ""

    @patch("ddtrace.testing.internal.http.HTTPClient")
    def test_init_drops_user_info_and_path_from_the_base_url(self, mock_http_client: Mock) -> None:
        connector = BackendConnector(url="http://user:secret@[::1]:8126/prefix/")

        assert connector.base_url == "http://[::1]:8126"
        assert connector.base_path == "/prefix"

    def test_init_unknown_scheme(self) -> None:
        with pytest.raises(SetupError):
            BackendConnector(url="ftp://example.com")

    @patch("ddtrace.testing.internal.http.HTTPClient")
    def test_get_json_unix_domain_socket_requests_path(self, mock_http_client: Mock) -> None:
        """A GET over a unix:// URL requests its own path, without the socket's, and no body."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.header.side_effect = _headers({})
        mock_response.body.return_value = b"{}"
        mock_http_client.return_value.get.return_value = mock_response

        result = BackendConnector(url="unix:///var/run/datadog/apm.socket").get_json("/info", max_attempts=1)

        assert result.error_type is None
        assert mock_http_client.return_value.get.call_args_list == [
            call("/info", headers=[("Content-Type", "application/json")], timeout_ms=_TIMEOUT_MS)
        ]

    @patch("ddtrace.testing.internal.http.HTTPClient")
    @patch("time.perf_counter", return_value=0.0)
    def test_post_json_ok(self, mock_time: Mock, mock_http_client: Mock) -> None:
        mock_response = Mock()
        mock_response.header.side_effect = _headers({"Content-Length": 14})
        mock_response.body.return_value = b'{"answer": 42}'
        mock_response.status_code = 200

        mock_client = Mock()
        mock_client.post.return_value = mock_response
        mock_http_client.return_value = mock_client

        mock_telemetry = Mock()

        connector = BackendConnector(url="https://api.example.com")

        result = connector.post_json("/v1/some-endpoint", data={"question": 1}, telemetry=mock_telemetry)

        assert mock_client.post.call_args_list == [
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"})
        ]
        assert result.error_type is None
        assert result.error_description is None
        assert result.parsed_response == {"answer": 42}
        assert result.is_gzip_response is False
        assert result.response_length == 14
        assert result.request_length == len(b'{"question": 1}')
        assert isinstance(result.elapsed_seconds, float)

        assert mock_telemetry.record_request.call_args_list == [
            call(
                seconds=0.0,
                response_bytes=14,
                compressed_response=False,
                error=None,
                request_bytes=len(b'{"question": 1}'),
            )
        ]

    @patch("ddtrace.testing.internal.http.HTTPClient")
    @patch("time.sleep")
    @patch("time.perf_counter", return_value=0.0)
    def test_post_json_retry_then_ok(self, mock_time: Mock, mock_sleep: Mock, mock_http_client: Mock) -> None:
        mock_response_error = Mock()
        mock_response_error.header.side_effect = _headers({})
        mock_response_error.body.return_value = b"Internal Server Error :("
        mock_response_error.status_code = 500

        mock_response_ok = Mock()
        mock_response_ok.header.side_effect = _headers({"Content-Length": 14})
        mock_response_ok.body.return_value = b'{"answer": 42}'
        mock_response_ok.status_code = 200

        mock_client = Mock()
        mock_client.post.side_effect = [mock_response_error, mock_response_ok]
        mock_http_client.return_value = mock_client

        mock_telemetry = Mock()

        connector = BackendConnector(url="https://api.example.com")

        result = connector.post_json("/v1/some-endpoint", data={"question": 1}, telemetry=mock_telemetry)

        assert mock_client.post.call_args_list == [
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
        ]
        assert len(mock_sleep.call_args_list) == 1

        assert result.error_type is None
        assert result.error_description is None
        assert result.parsed_response == {"answer": 42}
        assert result.is_gzip_response is False
        assert result.response_length == 14
        assert isinstance(result.elapsed_seconds, float)

        assert mock_telemetry.record_request.call_args_list == [
            call(
                seconds=0.0,
                response_bytes=0,
                compressed_response=False,
                error=ErrorType.CODE_5XX,
                request_bytes=len(b'{"question": 1}'),
            ),
            call(
                seconds=0.0,
                response_bytes=14,
                compressed_response=False,
                error=None,
                request_bytes=len(b'{"question": 1}'),
            ),
        ]

    @patch("ddtrace.testing.internal.http.HTTPClient")
    @patch("time.sleep")
    @patch("time.perf_counter", return_value=0.0)
    def test_post_json_retry_limit(self, mock_time: Mock, mock_sleep: Mock, mock_http_client: Mock) -> None:
        mock_response_error = Mock()
        mock_response_error.header.side_effect = _headers({})
        mock_response_error.body.return_value = b"Internal Server Error :("
        mock_response_error.status_code = 500

        mock_client = Mock()
        mock_client.post.return_value = mock_response_error
        mock_http_client.return_value = mock_client

        mock_telemetry = Mock()

        connector = BackendConnector(url="https://api.example.com")

        result = connector.post_json("/v1/some-endpoint", data={"question": 1}, telemetry=mock_telemetry)

        assert mock_client.post.call_args_list == [
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
        ]

        assert len(mock_sleep.call_args_list) == 4

        assert result.error_type is ErrorType.CODE_5XX
        assert result.error_description == "500 Internal Server Error"
        assert result.parsed_response is None
        assert result.is_gzip_response is False
        assert result.response_length == 0
        assert isinstance(result.elapsed_seconds, float)

        assert mock_telemetry.record_request.call_args_list == [
            call(
                seconds=0.0,
                response_bytes=0,
                compressed_response=False,
                error=ErrorType.CODE_5XX,
                request_bytes=len(b'{"question": 1}'),
            ),
            call(
                seconds=0.0,
                response_bytes=0,
                compressed_response=False,
                error=ErrorType.CODE_5XX,
                request_bytes=len(b'{"question": 1}'),
            ),
            call(
                seconds=0.0,
                response_bytes=0,
                compressed_response=False,
                error=ErrorType.CODE_5XX,
                request_bytes=len(b'{"question": 1}'),
            ),
            call(
                seconds=0.0,
                response_bytes=0,
                compressed_response=False,
                error=ErrorType.CODE_5XX,
                request_bytes=len(b'{"question": 1}'),
            ),
            call(
                seconds=0.0,
                response_bytes=0,
                compressed_response=False,
                error=ErrorType.CODE_5XX,
                request_bytes=len(b'{"question": 1}'),
            ),
        ]

    @patch("ddtrace.testing.internal.http.HTTPClient")
    @patch("time.sleep")
    @patch("time.perf_counter", return_value=0.0)
    def test_post_json_bad_json(self, mock_time: Mock, mock_sleep: Mock, mock_http_client: Mock) -> None:
        mock_response = Mock()
        mock_response.header.side_effect = _headers({"Content-Length": 14})
        mock_response.body.return_value = b'{"answer": ???'
        mock_response.status_code = 200

        mock_client = Mock()
        mock_client.post.return_value = mock_response
        mock_http_client.return_value = mock_client

        mock_telemetry = Mock()

        connector = BackendConnector(url="https://api.example.com")

        result = connector.post_json("/v1/some-endpoint", data={"question": 1}, telemetry=mock_telemetry)

        assert mock_client.post.call_args_list == [
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
        ]
        assert result.error_type is ErrorType.BAD_JSON
        assert result.error_description == "Expecting value: line 1 column 12 (char 11)"
        assert result.parsed_response is None
        assert result.is_gzip_response is False
        assert result.response_length == 14
        assert isinstance(result.elapsed_seconds, float)

        assert mock_telemetry.record_request.call_args_list == [
            call(
                seconds=0.0,
                response_bytes=14,
                compressed_response=False,
                error=ErrorType.BAD_JSON,
                request_bytes=len(b'{"question": 1}'),
            ),
            call(
                seconds=0.0,
                response_bytes=14,
                compressed_response=False,
                error=ErrorType.BAD_JSON,
                request_bytes=len(b'{"question": 1}'),
            ),
            call(
                seconds=0.0,
                response_bytes=14,
                compressed_response=False,
                error=ErrorType.BAD_JSON,
                request_bytes=len(b'{"question": 1}'),
            ),
            call(
                seconds=0.0,
                response_bytes=14,
                compressed_response=False,
                error=ErrorType.BAD_JSON,
                request_bytes=len(b'{"question": 1}'),
            ),
            call(
                seconds=0.0,
                response_bytes=14,
                compressed_response=False,
                error=ErrorType.BAD_JSON,
                request_bytes=len(b'{"question": 1}'),
            ),
        ]

    @patch("ddtrace.testing.internal.http.HTTPClient")
    @patch("time.sleep")
    @patch("time.perf_counter", return_value=0.0)
    def test_post_json_unretriable_error(self, mock_time: Mock, mock_sleep: Mock, mock_http_client: Mock) -> None:
        mock_response_error = Mock()
        mock_response_error.header.side_effect = _headers({})
        mock_response_error.body.return_value = b"No bueno"
        mock_response_error.status_code = 400

        mock_client = Mock()
        mock_client.post.return_value = mock_response_error
        mock_http_client.return_value = mock_client

        mock_telemetry = Mock()

        connector = BackendConnector(url="https://api.example.com")

        result = connector.post_json("/v1/some-endpoint", data={"question": 1}, telemetry=mock_telemetry)

        assert mock_client.post.call_args_list == [
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
        ]
        assert len(mock_sleep.call_args_list) == 0

        assert result.error_type is ErrorType.CODE_4XX
        assert result.error_description == "400 Bad Request"
        assert result.parsed_response is None
        assert result.is_gzip_response is False
        assert result.response_length == 0
        assert isinstance(result.elapsed_seconds, float)

        assert mock_telemetry.record_request.call_args_list == [
            call(
                seconds=0.0,
                response_bytes=0,
                compressed_response=False,
                error=ErrorType.CODE_4XX,
                request_bytes=len(b'{"question": 1}'),
            ),
        ]

    @pytest.mark.parametrize(
        "error_class,error_message",
        [
            (ConnectionFailedError, "No connection for you"),
            (HttpIoError, "Connection reset by peer"),
        ],
    )
    @patch("ddtrace.testing.internal.http.HTTPClient")
    @patch("time.sleep")
    @patch("time.perf_counter", return_value=0.0)
    def test_post_json_connection_errors(
        self,
        mock_time: Mock,
        mock_sleep: Mock,
        mock_http_client: Mock,
        error_class: type[Exception],
        error_message: str,
    ) -> None:
        """Test that the native client's connection and I/O errors are handled as retriable NETWORK errors."""
        mock_client = Mock()
        mock_client.post.side_effect = error_class(error_message)
        mock_http_client.return_value = mock_client

        mock_telemetry = Mock()

        connector = BackendConnector(url="https://api.example.com")

        result = connector.post_json("/v1/some-endpoint", data={"question": 1}, telemetry=mock_telemetry)

        assert mock_client.post.call_args_list == [
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
        ]
        assert len(mock_sleep.call_args_list) == 4

        assert result.error_type == ErrorType.NETWORK
        assert result.error_description == error_message
        assert result.parsed_response is None
        assert result.is_gzip_response is False
        assert result.response_length is None
        assert isinstance(result.elapsed_seconds, float)

        assert mock_telemetry.record_request.call_args_list == [
            call(
                seconds=0.0,
                response_bytes=None,
                compressed_response=False,
                error=ErrorType.NETWORK,
                request_bytes=len(b'{"question": 1}'),
            ),
            call(
                seconds=0.0,
                response_bytes=None,
                compressed_response=False,
                error=ErrorType.NETWORK,
                request_bytes=len(b'{"question": 1}'),
            ),
            call(
                seconds=0.0,
                response_bytes=None,
                compressed_response=False,
                error=ErrorType.NETWORK,
                request_bytes=len(b'{"question": 1}'),
            ),
            call(
                seconds=0.0,
                response_bytes=None,
                compressed_response=False,
                error=ErrorType.NETWORK,
                request_bytes=len(b'{"question": 1}'),
            ),
            call(
                seconds=0.0,
                response_bytes=None,
                compressed_response=False,
                error=ErrorType.NETWORK,
                request_bytes=len(b'{"question": 1}'),
            ),
        ]

    @patch("ddtrace.testing.internal.http.HTTPClient")
    @patch("time.sleep")
    @patch("time.perf_counter", return_value=0.0)
    def test_post_json_timeout(self, mock_time: Mock, mock_sleep: Mock, mock_http_client: Mock) -> None:
        mock_client = Mock()
        mock_client.post.side_effect = TimedOutError("ars longa, vita brevis")
        mock_http_client.return_value = mock_client

        mock_telemetry = Mock()

        connector = BackendConnector(url="https://api.example.com")

        result = connector.post_json("/v1/some-endpoint", data={"question": 1}, telemetry=mock_telemetry)

        assert mock_client.post.call_args_list == [
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
        ]
        assert len(mock_sleep.call_args_list) == 4

        assert result.error_type == ErrorType.TIMEOUT
        assert result.error_description == "ars longa, vita brevis"
        assert result.parsed_response is None
        assert result.is_gzip_response is False
        assert result.response_length is None
        assert isinstance(result.elapsed_seconds, float)

        assert mock_telemetry.record_request.call_args_list == [
            call(
                seconds=0.0,
                response_bytes=None,
                compressed_response=False,
                error=ErrorType.TIMEOUT,
                request_bytes=len(b'{"question": 1}'),
            ),
            call(
                seconds=0.0,
                response_bytes=None,
                compressed_response=False,
                error=ErrorType.TIMEOUT,
                request_bytes=len(b'{"question": 1}'),
            ),
            call(
                seconds=0.0,
                response_bytes=None,
                compressed_response=False,
                error=ErrorType.TIMEOUT,
                request_bytes=len(b'{"question": 1}'),
            ),
            call(
                seconds=0.0,
                response_bytes=None,
                compressed_response=False,
                error=ErrorType.TIMEOUT,
                request_bytes=len(b'{"question": 1}'),
            ),
            call(
                seconds=0.0,
                response_bytes=None,
                compressed_response=False,
                error=ErrorType.TIMEOUT,
                request_bytes=len(b'{"question": 1}'),
            ),
        ]

    @patch("ddtrace.testing.internal.http.HTTPClient")
    @patch("time.sleep")
    @patch("time.perf_counter", return_value=0.0)
    def test_post_json_unknown_error(self, mock_time: Mock, mock_sleep: Mock, mock_http_client: Mock) -> None:
        mock_client = Mock()
        mock_client.post.side_effect = ValueError("some internal error")
        mock_http_client.return_value = mock_client

        mock_telemetry = Mock()

        connector = BackendConnector(url="https://api.example.com")

        result = connector.post_json("/v1/some-endpoint", data={"question": 1}, telemetry=mock_telemetry)

        assert mock_client.post.call_args_list == [
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
        ]
        assert len(mock_sleep.call_args_list) == 0

        assert result.error_type == ErrorType.UNKNOWN
        assert result.error_description == "some internal error"
        assert result.parsed_response is None
        assert result.is_gzip_response is False
        assert result.response_length is None
        assert isinstance(result.elapsed_seconds, float)

        assert mock_telemetry.record_request.call_args_list == [
            call(
                seconds=0.0,
                response_bytes=None,
                compressed_response=False,
                error=ErrorType.UNKNOWN,
                request_bytes=len(b'{"question": 1}'),
            ),
        ]

    @patch("ddtrace.testing.internal.http.HTTPClient")
    @patch("time.sleep")
    @patch("time.perf_counter", return_value=0.0)
    def test_post_json_rate_limited_retry_then_ok(
        self, mock_time: Mock, mock_sleep: Mock, mock_http_client: Mock
    ) -> None:
        mock_response_429 = Mock()
        mock_response_429.header.side_effect = _headers({})
        mock_response_429.body.return_value = b"Rate limited"
        mock_response_429.status_code = 429

        mock_response_ok = Mock()
        mock_response_ok.header.side_effect = _headers({"Content-Length": 14})
        mock_response_ok.body.return_value = b'{"answer": 42}'
        mock_response_ok.status_code = 200

        mock_client = Mock()
        mock_client.post.side_effect = [mock_response_429, mock_response_ok]
        mock_http_client.return_value = mock_client

        mock_telemetry = Mock()

        connector = BackendConnector(url="https://api.example.com")
        result = connector.post_json("/v1/some-endpoint", data={"question": 1}, telemetry=mock_telemetry)

        assert mock_client.post.call_args_list == [
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
        ]
        assert len(mock_sleep.call_args_list) == 1

        assert result.error_type is None
        assert result.parsed_response == {"answer": 42}

        assert mock_telemetry.record_request.call_args_list == [
            call(
                seconds=0.0,
                response_bytes=0,
                compressed_response=False,
                error=ErrorType.RATE_LIMITED,
                request_bytes=len(b'{"question": 1}'),
            ),
            call(
                seconds=0.0,
                response_bytes=14,
                compressed_response=False,
                error=None,
                request_bytes=len(b'{"question": 1}'),
            ),
        ]

    @patch("ddtrace.testing.internal.http.HTTPClient")
    @patch("time.sleep")
    @patch("time.perf_counter", return_value=0.0)
    def test_post_json_rate_limited_retry_limit(
        self, mock_time: Mock, mock_sleep: Mock, mock_http_client: Mock
    ) -> None:
        mock_response_429 = Mock()
        mock_response_429.header.side_effect = _headers({})
        mock_response_429.body.return_value = b"Rate limited"
        mock_response_429.status_code = 429

        mock_client = Mock()
        mock_client.post.return_value = mock_response_429
        mock_http_client.return_value = mock_client

        mock_telemetry = Mock()

        connector = BackendConnector(url="https://api.example.com")
        result = connector.post_json("/v1/some-endpoint", data={"question": 1}, telemetry=mock_telemetry)

        assert mock_client.post.call_args_list == [
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
            _post_call("/v1/some-endpoint", b'{"question": 1}', {"Content-Type": "application/json"}),
        ]
        assert len(mock_sleep.call_args_list) == 4

        assert result.error_type is ErrorType.RATE_LIMITED
        assert result.error_description == "429 Too Many Requests"

        assert mock_telemetry.record_request.call_args_list == [
            call(
                seconds=0.0,
                response_bytes=0,
                compressed_response=False,
                error=ErrorType.RATE_LIMITED,
                request_bytes=len(b'{"question": 1}'),
            ),
            call(
                seconds=0.0,
                response_bytes=0,
                compressed_response=False,
                error=ErrorType.RATE_LIMITED,
                request_bytes=len(b'{"question": 1}'),
            ),
            call(
                seconds=0.0,
                response_bytes=0,
                compressed_response=False,
                error=ErrorType.RATE_LIMITED,
                request_bytes=len(b'{"question": 1}'),
            ),
            call(
                seconds=0.0,
                response_bytes=0,
                compressed_response=False,
                error=ErrorType.RATE_LIMITED,
                request_bytes=len(b'{"question": 1}'),
            ),
            call(
                seconds=0.0,
                response_bytes=0,
                compressed_response=False,
                error=ErrorType.RATE_LIMITED,
                request_bytes=len(b'{"question": 1}'),
            ),
        ]

    @patch("ddtrace.testing.internal.http.HTTPClient")
    @patch("time.sleep")
    @patch("time.time", return_value=1700000000)
    @patch("time.perf_counter", return_value=0.0)
    def test_post_json_rate_limited_uses_header_unix_timestamp(
        self, mock_perf: Mock, mock_time: Mock, mock_sleep: Mock, mock_http_client: Mock
    ) -> None:
        """When X-RateLimit-Reset is a future Unix timestamp, sleep until that point."""
        reset_timestamp = 1700000000 + 60  # 60 seconds in the future

        mock_response_429 = Mock()
        mock_response_429.header.side_effect = _headers({"X-RateLimit-Reset": str(reset_timestamp)})
        mock_response_429.body.return_value = b"Rate limited"
        mock_response_429.status_code = 429

        mock_response_ok = Mock()
        mock_response_ok.header.side_effect = _headers({"Content-Length": 14})
        mock_response_ok.body.return_value = b'{"answer": 42}'
        mock_response_ok.status_code = 200

        mock_client = Mock()
        mock_client.post.side_effect = [mock_response_429, mock_response_ok]
        mock_http_client.return_value = mock_client

        connector = BackendConnector(url="https://api.example.com")
        result = connector.post_json("/v1/some-endpoint", data={"question": 1}, telemetry=Mock())

        assert result.error_type is None
        mock_sleep.assert_called_once_with(60.0)

    @patch("ddtrace.testing.internal.http.HTTPClient")
    @patch("time.sleep")
    @patch("time.time", return_value=1700000000)
    @patch("time.perf_counter", return_value=0.0)
    def test_post_json_rate_limited_uses_header_duration(
        self, mock_perf: Mock, mock_time: Mock, mock_sleep: Mock, mock_http_client: Mock
    ) -> None:
        """When X-RateLimit-Reset is a small value (≤ current time), treat it as a duration in seconds."""
        mock_response_429 = Mock()
        mock_response_429.header.side_effect = _headers({"X-RateLimit-Reset": "30"})
        mock_response_429.body.return_value = b"Rate limited"
        mock_response_429.status_code = 429

        mock_response_ok = Mock()
        mock_response_ok.header.side_effect = _headers({"Content-Length": 14})
        mock_response_ok.body.return_value = b'{"answer": 42}'
        mock_response_ok.status_code = 200

        mock_client = Mock()
        mock_client.post.side_effect = [mock_response_429, mock_response_ok]
        mock_http_client.return_value = mock_client

        connector = BackendConnector(url="https://api.example.com")
        result = connector.post_json("/v1/some-endpoint", data={"question": 1}, telemetry=Mock())

        assert result.error_type is None
        mock_sleep.assert_called_once_with(30.0)

    @patch("ddtrace.testing.internal.http.HTTPClient")
    @patch("time.sleep")
    @patch("time.time", return_value=1700000000)
    @patch("time.perf_counter", return_value=0.0)
    def test_post_json_rate_limited_caps_retry_delay(
        self, mock_perf: Mock, mock_time: Mock, mock_sleep: Mock, mock_http_client: Mock
    ) -> None:
        """Retry delay is capped at 120 seconds to avoid unreasonable waits."""
        reset_timestamp = 1700000000 + 600  # 600 seconds in the future, exceeds 120s cap

        mock_response_429 = Mock()
        mock_response_429.header.side_effect = _headers({"X-RateLimit-Reset": str(reset_timestamp)})
        mock_response_429.body.return_value = b"Rate limited"
        mock_response_429.status_code = 429

        mock_response_ok = Mock()
        mock_response_ok.header.side_effect = _headers({"Content-Length": 14})
        mock_response_ok.body.return_value = b'{"answer": 42}'
        mock_response_ok.status_code = 200

        mock_client = Mock()
        mock_client.post.side_effect = [mock_response_429, mock_response_ok]
        mock_http_client.return_value = mock_client

        connector = BackendConnector(url="https://api.example.com")
        result = connector.post_json("/v1/some-endpoint", data={"question": 1}, telemetry=Mock())

        assert result.error_type is None
        mock_sleep.assert_called_once_with(MAX_RETRY_AFTER_SECONDS)

    @patch("ddtrace.testing.internal.http.HTTPClient")
    @patch("random.uniform", return_value=0.5)
    @patch("time.sleep")
    @patch("time.perf_counter", return_value=0.0)
    def test_post_json_rate_limited_falls_back_to_exponential_backoff_without_header(
        self, mock_perf: Mock, mock_sleep: Mock, mock_uniform: Mock, mock_http_client: Mock
    ) -> None:
        """When no X-RateLimit-Reset header is present, exponential backoff is used."""
        mock_response_429 = Mock()
        mock_response_429.header.side_effect = _headers({})
        mock_response_429.body.return_value = b"Rate limited"
        mock_response_429.status_code = 429

        mock_response_ok = Mock()
        mock_response_ok.header.side_effect = _headers({"Content-Length": 14})
        mock_response_ok.body.return_value = b'{"answer": 42}'
        mock_response_ok.status_code = 200

        mock_client = Mock()
        mock_client.post.side_effect = [mock_response_429, mock_response_ok]
        mock_http_client.return_value = mock_client

        connector = BackendConnector(url="https://api.example.com")
        result = connector.post_json("/v1/some-endpoint", data={"question": 1}, telemetry=Mock())

        assert result.error_type is None
        mock_uniform.assert_called_once()
        mock_sleep.assert_called_once_with(0.5)

    @patch("ddtrace.testing.internal.http.HTTPClient")
    @patch("random.uniform", return_value=0.5)
    @patch("time.sleep")
    @patch("time.perf_counter", return_value=0.0)
    def test_post_json_rate_limited_falls_back_to_exponential_backoff_with_invalid_header(
        self, mock_perf: Mock, mock_sleep: Mock, mock_uniform: Mock, mock_http_client: Mock
    ) -> None:
        """When X-RateLimit-Reset header is non-numeric, exponential backoff is used."""
        mock_response_429 = Mock()
        mock_response_429.header.side_effect = _headers({"X-RateLimit-Reset": "not-a-number"})
        mock_response_429.body.return_value = b"Rate limited"
        mock_response_429.status_code = 429

        mock_response_ok = Mock()
        mock_response_ok.header.side_effect = _headers({"Content-Length": 14})
        mock_response_ok.body.return_value = b'{"answer": 42}'
        mock_response_ok.status_code = 200

        mock_client = Mock()
        mock_client.post.side_effect = [mock_response_429, mock_response_ok]
        mock_http_client.return_value = mock_client

        connector = BackendConnector(url="https://api.example.com")
        result = connector.post_json("/v1/some-endpoint", data={"question": 1}, telemetry=Mock())

        assert result.error_type is None
        mock_uniform.assert_called_once()
        mock_sleep.assert_called_once_with(0.5)

    @patch("ddtrace.testing.internal.http.HTTPClient")
    @patch("uuid.uuid4")
    def test_post_files_multiple_files(self, mock_uuid: Mock, mock_http_client: Mock) -> None:
        """Test post_files method with multiple files."""
        # Setup mocks
        mock_uuid_obj = Mock()
        mock_uuid_obj.hex = "boundary123"
        mock_uuid.return_value = mock_uuid_obj

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.header.side_effect = _headers({})
        mock_response.body.return_value = b"upload success"

        mock_client = Mock()
        mock_client.post.return_value = mock_response
        mock_http_client.return_value = mock_client

        # Test post_files with multiple files
        connector = BackendConnector(url="https://api.example.com")
        files = [
            FileAttachment("file1", "doc1.txt", "text/plain", b"content1"),
            FileAttachment("file2", "doc2.json", "application/json", b"content2"),
        ]

        connector.post_files("/upload", files)

        # Verify both files are in the body
        call_args = mock_client.post.call_args
        assert call_args[0] == ("/upload",)
        assert ("Content-Type", "multipart/form-data; boundary=boundary123") in call_args[1]["headers"]
        body = call_args[1]["body"]

        # Check for both files
        assert b'name="file1"' in body
        assert b'name="file2"' in body
        assert b"content1" in body
        assert b"content2" in body
        assert body.count(b"--boundary123") == 3  # 2 file separators + 1 end


def _set_environ(monkeypatch: pytest.MonkeyPatch, environ: dict) -> None:
    """Replace the environment, then re-resolve the settings derived from it.

    The agentless settings resolve once at import, so the connector-mode decision would otherwise
    keep answering for the environment this test session started with.
    """
    monkeypatch.setattr(os, "environ", environ)
    reinitialize_agentless_config()


@pytest.fixture(autouse=True)
def _restore_agentless_config(monkeypatch: pytest.MonkeyPatch):
    """Keep an in-place refresh from leaking into later tests in this process."""
    yield
    # Undo explicitly so the environment is back to normal *before* it is re-read.
    monkeypatch.undo()
    reinitialize_agentless_config()


class TestBackendConnectorSetup:
    def test_detect_agentless_setup_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_environ(monkeypatch, {"DD_CIVISIBILITY_AGENTLESS_ENABLED": "true", "DD_API_KEY": "the-key"})

        connector_setup = BackendConnectorSetup.detect_setup()
        assert isinstance(connector_setup, BackendConnectorAgentlessSetup)

        connector = connector_setup.get_connector_for_subdomain(Subdomain.API)
        assert connector.base_url == "https://api.datadoghq.com"
        assert connector.use_gzip is True
        assert connector.default_headers["dd-api-key"] == "the-key"

        connector = connector_setup.get_connector_for_subdomain(Subdomain.CITESTCYCLE)
        assert connector.base_url == "https://citestcycle-intake.datadoghq.com"
        assert connector.use_gzip is True
        assert connector.default_headers["dd-api-key"] == "the-key"

    @pytest.mark.parametrize("subdomain", list(Subdomain))
    def test_detect_agentless_setup_with_url_override_for_all_subdomains(
        self, monkeypatch: pytest.MonkeyPatch, subdomain: Subdomain
    ) -> None:
        _set_environ(
            monkeypatch,
            {
                "DD_CIVISIBILITY_AGENTLESS_ENABLED": "true",
                "DD_API_KEY": "the-key",
                "DD_CIVISIBILITY_AGENTLESS_URL": "https://localhost:33333",
            },
        )

        connector_setup = BackendConnectorSetup.detect_setup()
        assert isinstance(connector_setup, BackendConnectorAgentlessSetup)

        connector = connector_setup.get_connector_for_subdomain(subdomain)
        assert connector.base_url == "https://localhost:33333"
        assert connector.use_gzip is True
        assert connector.default_headers["dd-api-key"] == "the-key"

    def test_detect_agentless_setup_ok_with_site(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_environ(
            monkeypatch,
            {"DD_CIVISIBILITY_AGENTLESS_ENABLED": "true", "DD_API_KEY": "the-key", "DD_SITE": "datadoghq.eu"},
        )

        connector_setup = BackendConnectorSetup.detect_setup()
        assert isinstance(connector_setup, BackendConnectorAgentlessSetup)

        connector = connector_setup.get_connector_for_subdomain(Subdomain.API)
        assert connector.base_url == "https://api.datadoghq.eu"
        assert connector.use_gzip is True
        assert connector.default_headers["dd-api-key"] == "the-key"

    def test_detect_agentless_setup_no_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_environ(
            monkeypatch,
            {"DD_CIVISIBILITY_AGENTLESS_ENABLED": "true"},
        )

        with pytest.raises(SetupError, match="DD_API_KEY environment variable is not set"):
            BackendConnectorSetup.detect_setup()

    def test_detect_evp_proxy_mode_v4_via_unix_domain_socket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_environ(monkeypatch, {})

        backend_connector_mock = (
            mock_backend_connector().with_get_json_response("/info", {"endpoints": ["/evp_proxy/v4/"]}).build()
        )
        with patch("ddtrace.testing.internal.http.BackendConnector", return_value=backend_connector_mock):
            # Ensure Unix domain socket WILL be detected.
            with patch("os.path.exists", return_value=True) as mock_path_exists:
                connector_setup = BackendConnectorSetup.detect_setup()

        assert isinstance(connector_setup, BackendConnectorEVPProxySetup)

        path_exists_args, _ = mock_path_exists.call_args
        assert path_exists_args == ("/var/run/datadog/apm.socket",)

        connector = connector_setup.get_connector_for_subdomain(Subdomain.API)
        assert connector.base_url == "unix:///var/run/datadog/apm.socket"
        assert connector.base_path == "/evp_proxy/v4"
        assert connector.use_gzip is True
        assert connector.default_headers["X-Datadog-EVP-Subdomain"] == "api"

    def test_detect_evp_proxy_mode_v4_via_http(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_environ(monkeypatch, {})

        backend_connector_mock = (
            mock_backend_connector().with_get_json_response("/info", {"endpoints": ["/evp_proxy/v4/"]}).build()
        )
        with patch("ddtrace.testing.internal.http.BackendConnector", return_value=backend_connector_mock):
            # Ensure Unix domain socket WILL NOT be detected.
            with patch("os.path.exists", return_value=False) as mock_path_exists:
                connector_setup = BackendConnectorSetup.detect_setup()

        assert isinstance(connector_setup, BackendConnectorEVPProxySetup)

        path_exists_args, _ = mock_path_exists.call_args
        assert path_exists_args == ("/var/run/datadog/apm.socket",)

        connector = connector_setup.get_connector_for_subdomain(Subdomain.API)
        assert connector.base_url == "http://localhost:8126"
        assert connector.base_path == "/evp_proxy/v4"
        assert connector.use_gzip is True
        assert connector.default_headers["X-Datadog-EVP-Subdomain"] == "api"

    def test_detect_evp_proxy_mode_v2_via_http(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_environ(monkeypatch, {})

        backend_connector_mock = (
            mock_backend_connector().with_get_json_response("/info", {"endpoints": ["/evp_proxy/v2/"]}).build()
        )
        with patch("ddtrace.testing.internal.http.BackendConnector", return_value=backend_connector_mock):
            # Ensure Unix domain socket WILL NOT be detected.
            with patch("os.path.exists", return_value=False) as mock_path_exists:
                connector_setup = BackendConnectorSetup.detect_setup()

        assert isinstance(connector_setup, BackendConnectorEVPProxySetup)

        path_exists_args, _ = mock_path_exists.call_args
        assert path_exists_args == ("/var/run/datadog/apm.socket",)

        connector = connector_setup.get_connector_for_subdomain(Subdomain.API)
        assert connector.base_url == "http://localhost:8126"
        assert connector.base_path == "/evp_proxy/v2"
        assert connector.use_gzip is False
        assert connector.default_headers["X-Datadog-EVP-Subdomain"] == "api"

    def test_detect_evp_proxy_mode_no_evp_support(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_environ(monkeypatch, {})

        backend_connector_mock = mock_backend_connector().with_get_json_response("/info", {"endpoints": []}).build()
        with patch("ddtrace.testing.internal.http.BackendConnector", return_value=backend_connector_mock):
            with pytest.raises(SetupError, match="Datadog agent .* does not support EVP proxy mode"):
                BackendConnectorSetup.detect_setup()

    def test_detect_evp_proxy_mode_no_agent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_environ(monkeypatch, {})

        with patch(
            "ddtrace.testing.internal.http.BackendConnector.get_json", side_effect=ConnectionRefusedError("no bueno")
        ):
            with pytest.raises(SetupError, match="Error connecting to Datadog agent.*no bueno"):
                BackendConnectorSetup.detect_setup()

    def test_detect_evp_proxy_mode_v4_custom_dd_trace_agent_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_environ(monkeypatch, {"DD_TRACE_AGENT_URL": "http://somehost:1234"})

        backend_connector_mock = (
            mock_backend_connector().with_get_json_response("/info", {"endpoints": ["/evp_proxy/v4/"]}).build()
        )
        with patch("ddtrace.testing.internal.http.BackendConnector", return_value=backend_connector_mock):
            connector_setup = BackendConnectorSetup.detect_setup()

        assert isinstance(connector_setup, BackendConnectorEVPProxySetup)

        connector = connector_setup.get_connector_for_subdomain(Subdomain.API)
        assert connector.base_url == "http://somehost:1234"
        assert connector.base_path == "/evp_proxy/v4"
        assert connector.use_gzip is True
        assert connector.default_headers["X-Datadog-EVP-Subdomain"] == "api"

    def test_detect_evp_proxy_mode_v4_custom_dd_trace_agent_hostname(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_environ(monkeypatch, {"DD_TRACE_AGENT_HOSTNAME": "somehost", "DD_TRACE_AGENT_PORT": "5678"})

        backend_connector_mock = (
            mock_backend_connector().with_get_json_response("/info", {"endpoints": ["/evp_proxy/v4/"]}).build()
        )
        with patch("ddtrace.testing.internal.http.BackendConnector", return_value=backend_connector_mock):
            connector_setup = BackendConnectorSetup.detect_setup()

        assert isinstance(connector_setup, BackendConnectorEVPProxySetup)

        connector = connector_setup.get_connector_for_subdomain(Subdomain.API)
        assert connector.base_url == "http://somehost:5678"
        assert connector.base_path == "/evp_proxy/v4"
        assert connector.use_gzip is True
        assert connector.default_headers["X-Datadog-EVP-Subdomain"] == "api"

    def test_detect_evp_proxy_mode_v4_custom_dd_agent_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_environ(monkeypatch, {"DD_AGENT_HOST": "somehost", "DD_AGENT_PORT": "5678"})

        backend_connector_mock = (
            mock_backend_connector().with_get_json_response("/info", {"endpoints": ["/evp_proxy/v4/"]}).build()
        )
        with patch("ddtrace.testing.internal.http.BackendConnector", return_value=backend_connector_mock):
            connector_setup = BackendConnectorSetup.detect_setup()

        assert isinstance(connector_setup, BackendConnectorEVPProxySetup)

        connector = connector_setup.get_connector_for_subdomain(Subdomain.API)
        assert connector.base_url == "http://somehost:5678"
        assert connector.base_path == "/evp_proxy/v4"
        assert connector.use_gzip is True
        assert connector.default_headers["X-Datadog-EVP-Subdomain"] == "api"

    def test_detect_evp_proxy_mode_v4_custom_dd_trace_agent_unix_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_environ(monkeypatch, {"DD_TRACE_AGENT_URL": "unix:///some/file/name.socket"})

        backend_connector_mock = (
            mock_backend_connector().with_get_json_response("/info", {"endpoints": ["/evp_proxy/v4/"]}).build()
        )
        with patch("ddtrace.testing.internal.http.BackendConnector", return_value=backend_connector_mock):
            with patch("os.path.exists", return_value=True):
                connector_setup = BackendConnectorSetup.detect_setup()

        assert isinstance(connector_setup, BackendConnectorEVPProxySetup)

        connector = connector_setup.get_connector_for_subdomain(Subdomain.API)
        assert connector.base_url == "unix:///some/file/name.socket"
        assert connector.base_path == "/evp_proxy/v4"
        assert connector.use_gzip is True
        assert connector.default_headers["X-Datadog-EVP-Subdomain"] == "api"

    # --- detect_standard_setup: public env vars only ---

    def test_detect_standard_setup_agentless_reads_public_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_environ(monkeypatch, {"DD_CIVISIBILITY_AGENTLESS_ENABLED": "true", "DD_API_KEY": "public-key"})

        connector_setup = BackendConnectorSetup.detect_standard_setup()
        assert isinstance(connector_setup, BackendConnectorAgentlessSetup)

        connector = connector_setup.get_connector_for_subdomain(Subdomain.LOGS)
        assert connector.default_headers["dd-api-key"] == "public-key"

    def test_detect_standard_setup_agentless_ignores_internal_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """detect_standard_setup must not fall back to _CI_DD_API_KEY."""
        _set_environ(
            monkeypatch,
            {
                "DD_CIVISIBILITY_AGENTLESS_ENABLED": "true",
                "_CI_DD_API_KEY": "internal-key",
                "DD_API_KEY": "public-key",
            },
        )

        connector_setup = BackendConnectorSetup.detect_standard_setup()
        connector = connector_setup.get_connector_for_subdomain(Subdomain.LOGS)
        assert connector.default_headers["dd-api-key"] == "public-key"

    def test_detect_standard_setup_agentless_raises_when_only_internal_key_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """detect_standard_setup must raise if DD_API_KEY is absent, even if _CI_DD_API_KEY is set."""
        _set_environ(
            monkeypatch,
            {"DD_CIVISIBILITY_AGENTLESS_ENABLED": "true", "_CI_DD_API_KEY": "internal-key"},
        )

        with pytest.raises(SetupError, match="DD_API_KEY"):
            BackendConnectorSetup.detect_standard_setup()

    def test_detect_setup_still_reads_internal_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """detect_setup (internal path) must still honour _CI_DD_API_KEY."""
        _set_environ(
            monkeypatch,
            {"DD_CIVISIBILITY_AGENTLESS_ENABLED": "true", "_CI_DD_API_KEY": "internal-key"},
        )

        connector_setup = BackendConnectorSetup.detect_setup()
        connector = connector_setup.get_connector_for_subdomain(Subdomain.LOGS)
        assert connector.default_headers["dd-api-key"] == "internal-key"

    def test_detect_setup_still_reads_internal_agent_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """detect_setup (internal path) must still honour _CI_DD_AGENT_URL."""
        _set_environ(monkeypatch, {"_CI_DD_AGENT_URL": "http://internal-agent:8126"})

        backend_connector_mock = (
            mock_backend_connector().with_get_json_response("/info", {"endpoints": ["/evp_proxy/v4/"]}).build()
        )
        with patch("ddtrace.testing.internal.http.BackendConnector", return_value=backend_connector_mock):
            connector_setup = BackendConnectorSetup.detect_setup()

        assert isinstance(connector_setup, BackendConnectorEVPProxySetup)
        assert connector_setup.url == "http://internal-agent:8126"

    def test_detect_standard_setup_evp_proxy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_environ(monkeypatch, {"DD_TRACE_AGENT_URL": "http://my-agent:8126"})

        backend_connector_mock = (
            mock_backend_connector().with_get_json_response("/info", {"endpoints": ["/evp_proxy/v4/"]}).build()
        )
        with patch("ddtrace.testing.internal.http.BackendConnector", return_value=backend_connector_mock):
            connector_setup = BackendConnectorSetup.detect_standard_setup()

        assert isinstance(connector_setup, BackendConnectorEVPProxySetup)
        assert connector_setup.url == "http://my-agent:8126"
        assert connector_setup.base_path == "/evp_proxy/v4"


class TestRequestTimeout:
    """Each request uses the connector's timeout, which callers may change between requests."""

    @patch("ddtrace.testing.internal.http.HTTPClient")
    def test_requests_use_the_connector_timeout(self, mock_http_client: Mock) -> None:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.header.side_effect = _headers({})
        mock_response.body.return_value = b""
        mock_http_client.return_value.post.return_value = mock_response

        connector = BackendConnector(url="unix:///tmp/nonexistent.sock", timeout_seconds=3.5)
        connector.request("POST", "/api/v2/citestcycle", data=b"x", max_attempts=1)
        connector.timeout_seconds = 10.0
        connector.request("POST", "/api/v2/citestcycle", data=b"x", max_attempts=1)

        assert mock_http_client.call_args.kwargs["timeout_ms"] == 3500
        assert [c.kwargs["timeout_ms"] for c in mock_http_client.return_value.post.call_args_list] == [3500, 10000]


class TestBackendTimeoutEnvVar:
    """Tests for DD_CIVISIBILITY_BACKEND_API_TIMEOUT_MILLIS parsing via _parse_timeout_millis."""

    # Tests call _parse_timeout_millis() directly to avoid importlib.reload, which
    # mutates the module's __dict__ in place and breaks isinstance checks in other test classes
    # (old imported class objects see new reloaded classes via shared __globals__).

    def test_default_when_unset(self) -> None:
        assert _http_module._parse_timeout_millis(None) == 30.0

    def test_default_when_empty_string(self) -> None:
        assert _http_module._parse_timeout_millis("") == 30.0

    def test_integer_string_is_accepted(self) -> None:
        assert _http_module._parse_timeout_millis("60000") == 60.0

    def test_float_string_is_accepted(self) -> None:
        assert _http_module._parse_timeout_millis("30500") == 30.5

    def test_non_numeric_value_falls_back_to_default(self) -> None:
        http_logger = logging.getLogger("ddtrace.testing.internal.http")
        with patch.object(http_logger, "warning") as mock_warning:
            result = _http_module._parse_timeout_millis("fast")
        assert result == 30.0
        mock_warning.assert_called_once()
        fmt_string, bad_value = mock_warning.call_args[0][0], mock_warning.call_args[0][1]
        assert "DD_CIVISIBILITY_BACKEND_API_TIMEOUT_MILLIS" in fmt_string
        assert bad_value == "fast"

    def test_non_numeric_value_does_not_raise(self) -> None:
        assert _http_module._parse_timeout_millis("not-a-number") == 30.0

    @pytest.mark.parametrize("bad_value", ["0", "-5000", "inf", "-inf", "nan", "300001"])
    def test_non_positive_or_out_of_range_falls_back_to_default(self, bad_value: str) -> None:
        assert _http_module._parse_timeout_millis(bad_value) == 30.0


class TestRequestFailureWarning:
    """Regression test: request() must log a warning when giving up with an error."""

    @patch("ddtrace.testing.internal.http.HTTPClient")
    def test_non_retriable_error_logs_warning(self, mock_http_client: Mock) -> None:
        """A 4xx (non-retriable) error should log a warning immediately."""
        mock_response = Mock()
        mock_response.header.side_effect = _headers({"Content-Length": "0"})
        mock_response.body.return_value = b""
        mock_response.status_code = 403

        mock_client = Mock()
        mock_client.post.return_value = mock_response
        mock_http_client.return_value = mock_client

        connector = BackendConnector(url="https://api.example.com")

        with patch("ddtrace.testing.internal.http.log") as mock_log:
            result = connector.request("POST", "/api/v2/citestcycle", data=b"x")

        assert result.error_type == ErrorType.CODE_4XX
        mock_log.warning.assert_called_once()
        # Format: "Request %s %s failed after %d attempt(s): %s"
        assert "403 Forbidden" in mock_log.warning.call_args[0][4]

    @patch("ddtrace.testing.internal.http.HTTPClient")
    def test_successful_request_no_warning(self, mock_http_client: Mock) -> None:
        mock_response = Mock()
        mock_response.header.side_effect = _headers({"Content-Length": "2"})
        mock_response.body.return_value = b"ok"
        mock_response.status_code = 200

        mock_client = Mock()
        mock_client.get.return_value = mock_response
        mock_http_client.return_value = mock_client

        connector = BackendConnector(url="https://api.example.com")

        with patch("ddtrace.testing.internal.http.log") as mock_log:
            result = connector.request("GET", "/info", is_json_response=False)

        assert result.error_type is None
        mock_log.warning.assert_not_called()


@pytest.fixture
def local_servers():
    """An HTTP server on a local port and one on a Unix domain socket, in subprocesses, so that they keep working when
    a test replaces Python's socket functions.
    """
    tcp = subprocess.Popen(
        [sys.executable, "-u", "-m", "http.server", "--bind", "127.0.0.1", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    socket_dir = tempfile.mkdtemp(dir="/tmp")  # A socket's path is limited to about 104 bytes on macOS.
    unix_path = os.path.join(socket_dir, "agent.sock")
    unix = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-c",
            "import http.server, socketserver, sys\n"
            "class Server(socketserver.UnixStreamServer):\n"
            "    def get_request(self):\n"
            "        request, _ = super().get_request()\n"
            "        return request, ('localhost', 0)\n"
            "server = Server(sys.argv[1], http.server.SimpleHTTPRequestHandler)\n"
            "print('ready', flush=True)\n"
            "server.serve_forever()\n",
            unix_path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        port = int(tcp.stdout.readline().split(" port ")[1].split()[0])
        unix.stdout.readline()
        yield port, unix_path
    finally:
        tcp.kill()
        unix.kill()
        tcp.wait()
        unix.wait()
        if os.path.exists(unix_path):
            os.unlink(unix_path)
        os.rmdir(socket_dir)


@pytest.mark.skipif(sys.platform == "win32", reason="Unix domain sockets")
class TestConnectionsWhenTestsBlockSockets:
    """Libraries such as pytest-socket replace Python's socket functions while tests run, and the plugin sends its
    payloads while tests run. The native client doesn't use them.
    """

    def test_requests_reach_the_servers(self, local_servers, monkeypatch: pytest.MonkeyPatch) -> None:
        port, unix_path = local_servers

        def blocked(*args, **kwargs):
            raise RuntimeError("A test tried to use socket")

        for name in ("socket", "create_connection", "getaddrinfo"):
            monkeypatch.setattr(socket, name, blocked)

        for url in (f"http://127.0.0.1:{port}", f"unix://{unix_path}"):
            result = BackendConnector(url=url).request("GET", "/nothing-here", max_attempts=1)

            # The servers answer 404 for a path they don't serve.
            assert result.error_type == ErrorType.CODE_4XX, (url, result.error_description)
            assert result.error_description == "404 Not Found"


class _FakeGevent:
    """Stand-ins for the parts of gevent the connector uses: the hub of this thread, if any, its threadpool, the current
    greenlet, and Timeout, which like gevent's is a BaseException raised by the instance that expired.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, hub_exists: bool = True, in_hub: bool = False) -> None:
        self.spawned: list = []
        self.deadlines: list = []
        self.expire_while_spawning = False
        self.expire_while_waiting = False
        self.foreign_timeout: t.Optional[BaseException] = None
        fake = self

        class Timeout(BaseException):
            def __init__(self, seconds: t.Optional[float] = None) -> None:
                self.seconds = seconds
                self.cancelled = False

            def start(self) -> None:
                fake.deadlines.append(self)

            def cancel(self) -> None:
                self.cancelled = True

        class Pending:
            def __init__(self, call) -> None:
                self.call = call

            def get(self):
                if fake.foreign_timeout is not None:
                    raise fake.foreign_timeout
                if fake.expire_while_waiting:
                    raise fake.deadlines[-1]
                return self.call()

        class Threadpool:
            def spawn(self, call):
                if fake.expire_while_spawning:
                    raise fake.deadlines[-1]
                fake.spawned.append(call)
                return Pending(call)

        self.Timeout = Timeout
        self.hub = types.SimpleNamespace(threadpool=Threadpool())
        gevent = types.ModuleType("gevent")
        hub_local = types.ModuleType("gevent._hub_local")
        hub_local.get_hub_if_exists = lambda: self.hub if hub_exists else None  # type: ignore[attr-defined]
        gevent.Timeout = Timeout  # type: ignore[attr-defined]
        gevent.getcurrent = lambda: self.hub if in_hub else object()  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "gevent", gevent)
        monkeypatch.setitem(sys.modules, "gevent._hub_local", hub_local)


def _ok_client(mock_http_client: Mock) -> Mock:
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.header.side_effect = _headers({})
    mock_response.body.return_value = b""
    mock_http_client.return_value.post.return_value = mock_response
    return mock_http_client.return_value


class TestGevent:
    """Where a gevent hub runs, greenlets share the thread, and a native request runs on the hub's threadpool so that
    they keep running while it waits.
    """

    @patch("ddtrace.testing.internal.http.HTTPClient")
    def test_requests_run_on_the_threadpool_where_a_hub_runs(
        self, mock_http_client: Mock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gevent = _FakeGevent(monkeypatch)
        client = _ok_client(mock_http_client)

        connector = BackendConnector(url="http://localhost:8126", timeout_seconds=5.0)
        result = connector.request("POST", "/x", data=b"x", max_attempts=1)

        assert result.error_type is None
        assert len(gevent.spawned) == 1
        assert client.post.call_count == 1
        [deadline] = gevent.deadlines
        assert deadline.seconds == 5.0 + _http_module._THREADPOOL_GRACE_SECONDS
        assert deadline.cancelled

    @pytest.mark.parametrize("hub_exists,in_hub", [(False, False), (True, True)])
    @patch("ddtrace.testing.internal.http.HTTPClient")
    def test_requests_run_directly_without_a_hub_or_from_the_hub(
        self, mock_http_client: Mock, hub_exists: bool, in_hub: bool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gevent = _FakeGevent(monkeypatch, hub_exists=hub_exists, in_hub=in_hub)
        client = _ok_client(mock_http_client)

        result = BackendConnector(url="http://localhost:8126").request("POST", "/x", data=b"x", max_attempts=1)

        assert result.error_type is None
        assert gevent.spawned == []
        assert client.post.call_count == 1

    @patch("ddtrace.testing.internal.http.HTTPClient")
    def test_a_request_waiting_too_long_for_its_result_times_out_and_never_runs(
        self, mock_http_client: Mock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gevent = _FakeGevent(monkeypatch)
        gevent.expire_while_waiting = True
        client = _ok_client(mock_http_client)

        result = BackendConnector(url="http://localhost:8126", timeout_seconds=1.0).request(
            "POST", "/x", data=b"x", max_attempts=1
        )

        assert result.error_type == ErrorType.TIMEOUT
        # When a thread frees up later, the abandoned call gives up without sending.
        with pytest.raises(TimedOutError):
            gevent.spawned[0]()
        assert client.post.call_count == 0

    @patch("ddtrace.testing.internal.http.HTTPClient")
    def test_a_request_waiting_too_long_to_reach_the_threadpool_times_out(
        self, mock_http_client: Mock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gevent = _FakeGevent(monkeypatch)
        gevent.expire_while_spawning = True
        client = _ok_client(mock_http_client)

        result = BackendConnector(url="http://localhost:8126", timeout_seconds=1.0).request(
            "POST", "/x", data=b"x", max_attempts=2
        )

        assert result.error_type == ErrorType.TIMEOUT
        assert len(gevent.deadlines) == 2  # The connector retried a timeout.
        assert gevent.spawned == []
        assert client.post.call_count == 0

    @patch("ddtrace.testing.internal.http.HTTPClient")
    def test_someone_elses_timeout_propagates_and_the_request_never_runs(
        self, mock_http_client: Mock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gevent = _FakeGevent(monkeypatch)
        foreign_timeout = gevent.Timeout(0.1)
        gevent.foreign_timeout = foreign_timeout
        client = _ok_client(mock_http_client)

        with pytest.raises(gevent.Timeout) as raised:
            BackendConnector(url="http://localhost:8126").request("POST", "/x", data=b"x", max_attempts=1)

        assert raised.value is foreign_timeout
        assert gevent.deadlines[0].cancelled
        with pytest.raises(TimedOutError):
            gevent.spawned[0]()
        assert client.post.call_count == 0
