"""Tests for ddtrace.testing.internal.http module."""

import http.client
import os
from unittest.mock import Mock
from unittest.mock import call
from unittest.mock import patch

import pytest

from ddtrace.testing.internal.errors import SetupError
from ddtrace.testing.internal.http import DEFAULT_TIMEOUT_SECONDS
from ddtrace.testing.internal.http import BackendConnector
from ddtrace.testing.internal.http import BackendConnectorAgentlessSetup
from ddtrace.testing.internal.http import BackendConnectorEVPProxySetup
from ddtrace.testing.internal.http import BackendConnectorSetup
from ddtrace.testing.internal.http import FileAttachment
from ddtrace.testing.internal.http import Subdomain
from ddtrace.testing.internal.http import UnixDomainSocketHTTPConnection
from ddtrace.testing.internal.telemetry import ErrorType
from tests.testing.mocks import mock_backend_connector


class TestBackendConnector:
    """Tests for BackendConnector class."""

    def test_constants(self) -> None:
        """Test module constants."""
        assert DEFAULT_TIMEOUT_SECONDS == 15.0

    @patch("http.client.HTTPSConnection")
    def test_init_default_parameters(self, mock_https_connection: Mock) -> None:
        """Test BackendConnector initialization with default parameters."""
        connector = BackendConnector(url="https://api.example.com", use_gzip=True)

        mock_https_connection.assert_called_once_with(host="api.example.com", port=443, timeout=DEFAULT_TIMEOUT_SECONDS)
        assert connector.default_headers == {"Accept-Encoding": "gzip"}
        assert connector.base_path == ""

    @patch("http.client.HTTPSConnection")
    def test_init_custom_parameters(self, mock_https_connection: Mock) -> None:
        """Test BackendConnector initialization with custom parameters."""
        connector = BackendConnector(url="https://api.example.com/some-path", use_gzip=False)

        mock_https_connection.assert_called_once_with(host="api.example.com", port=443, timeout=DEFAULT_TIMEOUT_SECONDS)
        assert connector.default_headers == {}
        assert connector.base_path == "/some-path"

    @patch("ddtrace.testing.internal.http.UnixDomainSocketHTTPConnection")
    def test_init_unix_domain_socket(self, mock_unix_connection: Mock) -> None:
        """Test BackendConnector initialization with Unix domain socket URL."""
        connector = BackendConnector(url="unix:///some/path/name", base_path="/evp_proxy/over9000", use_gzip=False)

        mock_unix_connection.assert_called_once_with(
            host="localhost", port=80, timeout=DEFAULT_TIMEOUT_SECONDS, path="/some/path/name"
        )
        assert connector.default_headers == {}
        assert connector.base_path == "/evp_proxy/over9000"

    @patch("http.client.HTTPSConnection")
    @patch("time.perf_counter", return_value=0.0)
    def test_post_json_ok(self, mock_time: Mock, mock_https_connection: Mock) -> None:
        mock_response = Mock()
        mock_response.headers = {"Content-Length": 14}
        mock_response.read.return_value = b'{"answer": 42}'
        mock_response.status = 200

        mock_conn = Mock()
        mock_conn.getresponse.return_value = mock_response
        mock_https_connection.return_value = mock_conn

        mock_telemetry = Mock()

        connector = BackendConnector(url="https://api.example.com")

        result = connector.post_json("/v1/some-endpoint", data={"question": 1}, telemetry=mock_telemetry)

        assert mock_conn.request.call_args_list == [
            call("POST", "/v1/some-endpoint", body=b'{"question": 1}', headers={"Content-Type": "application/json"})
        ]
        assert result.error_type is None
        assert result.error_description is None
        assert result.parsed_response == {"answer": 42}
        assert result.is_gzip_response is False
        assert result.response_length == 14
        assert isinstance(result.elapsed_seconds, float)

        assert mock_telemetry.record_request.call_args_list == [
            call(seconds=0.0, response_bytes=14, compressed_response=False, error=None)
        ]

    @patch("http.client.HTTPSConnection")
    @patch("time.sleep")
    @patch("time.perf_counter", return_value=0.0)
    def test_post_json_retry_then_ok(self, mock_time: Mock, mock_sleep: Mock, mock_https_connection: Mock) -> None:
        mock_response_error = Mock()
        mock_response_error.headers = {}
        mock_response_error.read.return_value = b"Internal Server Error :("
        mock_response_error.status = 500

        mock_response_ok = Mock()
        mock_response_ok.headers = {"Content-Length": 14}
        mock_response_ok.read.return_value = b'{"answer": 42}'
        mock_response_ok.status = 200

        mock_conn = Mock()
        mock_conn.getresponse.side_effect = [mock_response_error, mock_response_ok]
        mock_https_connection.return_value = mock_conn

        mock_telemetry = Mock()

        connector = BackendConnector(url="https://api.example.com")

        result = connector.post_json("/v1/some-endpoint", data={"question": 1}, telemetry=mock_telemetry)

        assert mock_conn.request.call_args_list == [
            call("POST", "/v1/some-endpoint", body=b'{"question": 1}', headers={"Content-Type": "application/json"}),
            call("POST", "/v1/some-endpoint", body=b'{"question": 1}', headers={"Content-Type": "application/json"}),
        ]
        assert len(mock_sleep.call_args_list) == 1

        assert result.error_type is None
        assert result.error_description is None
        assert result.parsed_response == {"answer": 42}
        assert result.is_gzip_response is False
        assert result.response_length == 14
        assert isinstance(result.elapsed_seconds, float)

        assert mock_telemetry.record_request.call_args_list == [
            call(seconds=0.0, response_bytes=0, compressed_response=False, error=ErrorType.CODE_5XX),
            call(seconds=0.0, response_bytes=14, compressed_response=False, error=None),
        ]

    @patch("http.client.HTTPSConnection")
    @patch("time.sleep")
    @patch("time.perf_counter", return_value=0.0)
    def test_post_json_retry_limit(self, mock_time: Mock, mock_sleep: Mock, mock_https_connection: Mock) -> None:
        mock_response_error = Mock()
        mock_response_error.headers = {}
        mock_response_error.read.return_value = b"Internal Server Error :("
        mock_response_error.status = 500
        mock_response_error.reason = "Internal Server Error"

        mock_conn = Mock()
        mock_conn.getresponse.return_value = mock_response_error
        mock_https_connection.return_value = mock_conn

        mock_telemetry = Mock()

        connector = BackendConnector(url="https://api.example.com")

        result = connector.post_json("/v1/some-endpoint", data={"question": 1}, telemetry=mock_telemetry)

        assert mock_conn.request.call_args_list == [
            call("POST", "/v1/some-endpoint", body=b'{"question": 1}', headers={"Content-Type": "application/json"}),
            call("POST", "/v1/some-endpoint", body=b'{"question": 1}', headers={"Content-Type": "application/json"}),
            call("POST", "/v1/some-endpoint", body=b'{"question": 1}', headers={"Content-Type": "application/json"}),
            call("POST", "/v1/some-endpoint", body=b'{"question": 1}', headers={"Content-Type": "application/json"}),
            call("POST", "/v1/some-endpoint", body=b'{"question": 1}', headers={"Content-Type": "application/json"}),
        ]

        assert len(mock_sleep.call_args_list) == 4

        assert result.error_type is ErrorType.CODE_5XX
        assert result.error_description == "500 Internal Server Error"
        assert result.parsed_response is None
        assert result.is_gzip_response is False
        assert result.response_length == 0
        assert isinstance(result.elapsed_seconds, float)

        assert mock_telemetry.record_request.call_args_list == [
            call(seconds=0.0, response_bytes=0, compressed_response=False, error=ErrorType.CODE_5XX),
            call(seconds=0.0, response_bytes=0, compressed_response=False, error=ErrorType.CODE_5XX),
            call(seconds=0.0, response_bytes=0, compressed_response=False, error=ErrorType.CODE_5XX),
            call(seconds=0.0, response_bytes=0, compressed_response=False, error=ErrorType.CODE_5XX),
            call(seconds=0.0, response_bytes=0, compressed_response=False, error=ErrorType.CODE_5XX),
        ]

    @patch("http.client.HTTPSConnection")
    @patch("time.sleep")
    @patch("time.perf_counter", return_value=0.0)
    def test_post_json_bad_json(self, mock_time: Mock, mock_sleep: Mock, mock_https_connection: Mock) -> None:
        mock_response = Mock()
        mock_response.headers = {"Content-Length": 14}
        mock_response.read.return_value = b'{"answer": ???'
        mock_response.status = 200

        mock_conn = Mock()
        mock_conn.getresponse.return_value = mock_response
        mock_https_connection.return_value = mock_conn

        mock_telemetry = Mock()

        connector = BackendConnector(url="https://api.example.com")

        result = connector.post_json("/v1/some-endpoint", data={"question": 1}, telemetry=mock_telemetry)

        assert mock_conn.request.call_args_list == [
            call("POST", "/v1/some-endpoint", body=b'{"question": 1}', headers={"Content-Type": "application/json"}),
            call("POST", "/v1/some-endpoint", body=b'{"question": 1}', headers={"Content-Type": "application/json"}),
            call("POST", "/v1/some-endpoint", body=b'{"question": 1}', headers={"Content-Type": "application/json"}),
            call("POST", "/v1/some-endpoint", body=b'{"question": 1}', headers={"Content-Type": "application/json"}),
            call("POST", "/v1/some-endpoint", body=b'{"question": 1}', headers={"Content-Type": "application/json"}),
        ]
        assert result.error_type is ErrorType.BAD_JSON
        assert result.error_description == "Expecting value: line 1 column 12 (char 11)"
        assert result.parsed_response is None
        assert result.is_gzip_response is False
        assert result.response_length == 14
        assert isinstance(result.elapsed_seconds, float)

        assert mock_telemetry.record_request.call_args_list == [
            call(seconds=0.0, response_bytes=14, compressed_response=False, error=ErrorType.BAD_JSON),
            call(seconds=0.0, response_bytes=14, compressed_response=False, error=ErrorType.BAD_JSON),
            call(seconds=0.0, response_bytes=14, compressed_response=False, error=ErrorType.BAD_JSON),
            call(seconds=0.0, response_bytes=14, compressed_response=False, error=ErrorType.BAD_JSON),
            call(seconds=0.0, response_bytes=14, compressed_response=False, error=ErrorType.BAD_JSON),
        ]

    @patch("http.client.HTTPSConnection")
    @patch("time.sleep")
    @patch("time.perf_counter", return_value=0.0)
    def test_post_json_unretriable_error(self, mock_time: Mock, mock_sleep: Mock, mock_https_connection: Mock) -> None:
        mock_response_error = Mock()
        mock_response_error.headers = {}
        mock_response_error.read.return_value = b"No bueno"
        mock_response_error.status = 400
        mock_response_error.reason = "Bad Request"

        mock_conn = Mock()
        mock_conn.getresponse.return_value = mock_response_error
        mock_https_connection.return_value = mock_conn

        mock_telemetry = Mock()

        connector = BackendConnector(url="https://api.example.com")

        result = connector.post_json("/v1/some-endpoint", data={"question": 1}, telemetry=mock_telemetry)

        assert mock_conn.request.call_args_list == [
            call("POST", "/v1/some-endpoint", body=b'{"question": 1}', headers={"Content-Type": "application/json"}),
        ]
        assert len(mock_sleep.call_args_list) == 0

        assert result.error_type is ErrorType.CODE_4XX
        assert result.error_description == "400 Bad Request"
        assert result.parsed_response is None
        assert result.is_gzip_response is False
        assert result.response_length == 0
        assert isinstance(result.elapsed_seconds, float)

        assert mock_telemetry.record_request.call_args_list == [
            call(seconds=0.0, response_bytes=0, compressed_response=False, error=ErrorType.CODE_4XX),
        ]

    @patch("http.client.HTTPSConnection")
    @patch("time.sleep")
    @patch("time.perf_counter", return_value=0.0)
    def test_post_json_connection_refused(self, mock_time: Mock, mock_sleep: Mock, mock_https_connection: Mock) -> None:
        mock_conn = Mock()
        mock_conn.getresponse.side_effect = ConnectionRefusedError("No connection for you")
        mock_https_connection.return_value = mock_conn

        mock_telemetry = Mock()

        connector = BackendConnector(url="https://api.example.com")

        result = connector.post_json("/v1/some-endpoint", data={"question": 1}, telemetry=mock_telemetry)

        assert mock_conn.request.call_args_list == [
            call("POST", "/v1/some-endpoint", body=b'{"question": 1}', headers={"Content-Type": "application/json"}),
            call("POST", "/v1/some-endpoint", body=b'{"question": 1}', headers={"Content-Type": "application/json"}),
            call("POST", "/v1/some-endpoint", body=b'{"question": 1}', headers={"Content-Type": "application/json"}),
            call("POST", "/v1/some-endpoint", body=b'{"question": 1}', headers={"Content-Type": "application/json"}),
            call("POST", "/v1/some-endpoint", body=b'{"question": 1}', headers={"Content-Type": "application/json"}),
        ]
        assert len(mock_sleep.call_args_list) == 4

        assert result.error_type == ErrorType.NETWORK
        assert result.error_description == "No connection for you"
        assert result.parsed_response is None
        assert result.is_gzip_response is False
        assert result.response_length is None
        assert isinstance(result.elapsed_seconds, float)

        assert mock_telemetry.record_request.call_args_list == [
            call(seconds=0.0, response_bytes=None, compressed_response=False, error=ErrorType.NETWORK),
            call(seconds=0.0, response_bytes=None, compressed_response=False, error=ErrorType.NETWORK),
            call(seconds=0.0, response_bytes=None, compressed_response=False, error=ErrorType.NETWORK),
            call(seconds=0.0, response_bytes=None, compressed_response=False, error=ErrorType.NETWORK),
            call(seconds=0.0, response_bytes=None, compressed_response=False, error=ErrorType.NETWORK),
        ]

    @patch("http.client.HTTPSConnection")
    @patch("time.sleep")
    @patch("time.perf_counter", return_value=0.0)
    def test_post_json_timeout(self, mock_time: Mock, mock_sleep: Mock, mock_https_connection: Mock) -> None:
        mock_conn = Mock()
        mock_conn.getresponse.side_effect = TimeoutError("ars longa, vita brevis")
        mock_https_connection.return_value = mock_conn

        mock_telemetry = Mock()

        connector = BackendConnector(url="https://api.example.com")

        result = connector.post_json("/v1/some-endpoint", data={"question": 1}, telemetry=mock_telemetry)

        assert mock_conn.request.call_args_list == [
            call("POST", "/v1/some-endpoint", body=b'{"question": 1}', headers={"Content-Type": "application/json"}),
            call("POST", "/v1/some-endpoint", body=b'{"question": 1}', headers={"Content-Type": "application/json"}),
            call("POST", "/v1/some-endpoint", body=b'{"question": 1}', headers={"Content-Type": "application/json"}),
            call("POST", "/v1/some-endpoint", body=b'{"question": 1}', headers={"Content-Type": "application/json"}),
            call("POST", "/v1/some-endpoint", body=b'{"question": 1}', headers={"Content-Type": "application/json"}),
        ]
        assert len(mock_sleep.call_args_list) == 4

        assert result.error_type == ErrorType.TIMEOUT
        assert result.error_description == "ars longa, vita brevis"
        assert result.parsed_response is None
        assert result.is_gzip_response is False
        assert result.response_length is None
        assert isinstance(result.elapsed_seconds, float)

        assert mock_telemetry.record_request.call_args_list == [
            call(seconds=0.0, response_bytes=None, compressed_response=False, error=ErrorType.TIMEOUT),
            call(seconds=0.0, response_bytes=None, compressed_response=False, error=ErrorType.TIMEOUT),
            call(seconds=0.0, response_bytes=None, compressed_response=False, error=ErrorType.TIMEOUT),
            call(seconds=0.0, response_bytes=None, compressed_response=False, error=ErrorType.TIMEOUT),
            call(seconds=0.0, response_bytes=None, compressed_response=False, error=ErrorType.TIMEOUT),
        ]

    @patch("http.client.HTTPSConnection")
    @patch("time.sleep")
    @patch("time.perf_counter", return_value=0.0)
    def test_post_json_unknown_error(self, mock_time: Mock, mock_sleep: Mock, mock_https_connection: Mock) -> None:
        mock_conn = Mock()
        mock_conn.getresponse.side_effect = ValueError("some internal error")
        mock_https_connection.return_value = mock_conn

        mock_telemetry = Mock()

        connector = BackendConnector(url="https://api.example.com")

        result = connector.post_json("/v1/some-endpoint", data={"question": 1}, telemetry=mock_telemetry)

        assert mock_conn.request.call_args_list == [
            call("POST", "/v1/some-endpoint", body=b'{"question": 1}', headers={"Content-Type": "application/json"}),
        ]
        assert len(mock_sleep.call_args_list) == 0

        assert result.error_type == ErrorType.UNKNOWN
        assert result.error_description == "some internal error"
        assert result.parsed_response is None
        assert result.is_gzip_response is False
        assert result.response_length is None
        assert isinstance(result.elapsed_seconds, float)

        assert mock_telemetry.record_request.call_args_list == [
            call(seconds=0.0, response_bytes=None, compressed_response=False, error=ErrorType.UNKNOWN),
        ]

    @patch("http.client.HTTPSConnection")
    @patch("uuid.uuid4")
    def test_post_files_multiple_files(self, mock_uuid: Mock, mock_https_connection: Mock) -> None:
        """Test post_files method with multiple files."""
        # Setup mocks
        mock_uuid_obj = Mock()
        mock_uuid_obj.hex = "boundary123"
        mock_uuid.return_value = mock_uuid_obj

        mock_response = Mock()
        mock_response.headers = {}
        mock_response.read.return_value = b"upload success"

        mock_conn = Mock()
        mock_conn.getresponse.return_value = mock_response
        mock_https_connection.return_value = mock_conn

        # Test post_files with multiple files
        connector = BackendConnector(url="https://api.example.com")
        files = [
            FileAttachment("file1", "doc1.txt", "text/plain", b"content1"),
            FileAttachment("file2", "doc2.json", "application/json", b"content2"),
        ]

        connector.post_files("/upload", files)

        # Verify both files are in the body
        call_args = mock_conn.request.call_args
        body = call_args[1]["body"]

        # Check for both files
        assert b'name="file1"' in body
        assert b'name="file2"' in body
        assert b"content1" in body
        assert b"content2" in body
        assert body.count(b"--boundary123") == 3  # 2 file separators + 1 end


class TestBackendConnectorSetup:
    def test_detect_agentless_setup_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "environ", {"DD_CIVISIBILITY_AGENTLESS_ENABLED": "true", "DD_API_KEY": "the-key"})

        connector_setup = BackendConnectorSetup.detect_setup()
        assert isinstance(connector_setup, BackendConnectorAgentlessSetup)

        connector = connector_setup.get_connector_for_subdomain(Subdomain.API)
        assert isinstance(connector.conn, http.client.HTTPSConnection)
        assert connector.conn.host == "api.datadoghq.com"
        assert connector.conn.port == 443
        assert connector.use_gzip is True
        assert connector.default_headers["dd-api-key"] == "the-key"

        connector = connector_setup.get_connector_for_subdomain(Subdomain.CITESTCYCLE)
        assert isinstance(connector.conn, http.client.HTTPSConnection)
        assert connector.conn.host == "citestcycle-intake.datadoghq.com"
        assert connector.conn.port == 443
        assert connector.use_gzip is True
        assert connector.default_headers["dd-api-key"] == "the-key"

    def test_detect_agentless_setup_with_citestcycle_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            os,
            "environ",
            {
                "DD_CIVISIBILITY_AGENTLESS_ENABLED": "true",
                "DD_API_KEY": "the-key",
                "DD_CIVISIBILITY_AGENTLESS_URL": "https://localhost:33333",
            },
        )

        connector_setup = BackendConnectorSetup.detect_setup()
        assert isinstance(connector_setup, BackendConnectorAgentlessSetup)

        connector = connector_setup.get_connector_for_subdomain(Subdomain.API)
        assert isinstance(connector.conn, http.client.HTTPSConnection)
        assert connector.conn.host == "api.datadoghq.com"
        assert connector.conn.port == 443
        assert connector.use_gzip is True
        assert connector.default_headers["dd-api-key"] == "the-key"

        connector = connector_setup.get_connector_for_subdomain(Subdomain.CITESTCYCLE)
        assert isinstance(connector.conn, http.client.HTTPSConnection)
        assert connector.conn.host == "localhost"
        assert connector.conn.port == 33333
        assert connector.use_gzip is True
        assert connector.default_headers["dd-api-key"] == "the-key"

    def test_detect_agentless_setup_ok_with_site(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            os,
            "environ",
            {"DD_CIVISIBILITY_AGENTLESS_ENABLED": "true", "DD_API_KEY": "the-key", "DD_SITE": "datadoghq.eu"},
        )

        connector_setup = BackendConnectorSetup.detect_setup()
        assert isinstance(connector_setup, BackendConnectorAgentlessSetup)

        connector = connector_setup.get_connector_for_subdomain(Subdomain.API)
        assert isinstance(connector.conn, http.client.HTTPSConnection)
        assert connector.conn.host == "api.datadoghq.eu"
        assert connector.conn.port == 443
        assert connector.use_gzip is True
        assert connector.default_headers["dd-api-key"] == "the-key"

    def test_detect_agentless_setup_no_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            os,
            "environ",
            {"DD_CIVISIBILITY_AGENTLESS_ENABLED": "true"},
        )

        with pytest.raises(SetupError, match="DD_API_KEY environment variable is not set"):
            BackendConnectorSetup.detect_setup()

    def test_detect_evp_proxy_mode_v4_via_unix_domain_socket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "environ", {})

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
        assert isinstance(connector.conn, UnixDomainSocketHTTPConnection)
        assert connector.conn.host == "localhost"
        assert connector.conn.port == 80
        assert connector.conn.path == "/var/run/datadog/apm.socket"
        assert connector.base_path == "/evp_proxy/v4"
        assert connector.use_gzip is True
        assert connector.default_headers["X-Datadog-EVP-Subdomain"] == "api"

    def test_detect_evp_proxy_mode_v4_via_http(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "environ", {})

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
        assert isinstance(connector.conn, http.client.HTTPConnection)
        assert not isinstance(connector.conn, UnixDomainSocketHTTPConnection)
        assert connector.conn.host == "localhost"
        assert connector.conn.port == 8126
        assert connector.base_path == "/evp_proxy/v4"
        assert connector.use_gzip is True
        assert connector.default_headers["X-Datadog-EVP-Subdomain"] == "api"

    def test_detect_evp_proxy_mode_v2_via_http(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "environ", {})

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
        assert isinstance(connector.conn, http.client.HTTPConnection)
        assert not isinstance(connector.conn, UnixDomainSocketHTTPConnection)
        assert connector.conn.host == "localhost"
        assert connector.conn.port == 8126
        assert connector.base_path == "/evp_proxy/v2"
        assert connector.use_gzip is False
        assert connector.default_headers["X-Datadog-EVP-Subdomain"] == "api"

    def test_detect_evp_proxy_mode_no_evp_support(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "environ", {})

        backend_connector_mock = mock_backend_connector().with_get_json_response("/info", {"endpoints": []}).build()
        with patch("ddtrace.testing.internal.http.BackendConnector", return_value=backend_connector_mock):
            with pytest.raises(SetupError, match="Datadog agent .* does not support EVP proxy mode"):
                BackendConnectorSetup.detect_setup()

    def test_detect_evp_proxy_mode_no_agent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "environ", {})

        with patch(
            "ddtrace.testing.internal.http.BackendConnector.get_json", side_effect=ConnectionRefusedError("no bueno")
        ):
            with pytest.raises(SetupError, match="Error connecting to Datadog agent.*no bueno"):
                BackendConnectorSetup.detect_setup()

    def test_detect_evp_proxy_mode_v4_custom_dd_trace_agent_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "environ", {"DD_TRACE_AGENT_URL": "http://somehost:1234"})

        backend_connector_mock = (
            mock_backend_connector().with_get_json_response("/info", {"endpoints": ["/evp_proxy/v4/"]}).build()
        )
        with patch("ddtrace.testing.internal.http.BackendConnector", return_value=backend_connector_mock):
            connector_setup = BackendConnectorSetup.detect_setup()

        assert isinstance(connector_setup, BackendConnectorEVPProxySetup)

        connector = connector_setup.get_connector_for_subdomain(Subdomain.API)
        assert isinstance(connector.conn, http.client.HTTPConnection)
        assert connector.conn.host == "somehost"
        assert connector.conn.port == 1234
        assert connector.base_path == "/evp_proxy/v4"
        assert connector.use_gzip is True
        assert connector.default_headers["X-Datadog-EVP-Subdomain"] == "api"

    def test_detect_evp_proxy_mode_v4_custom_dd_trace_agent_hostname(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "environ", {"DD_TRACE_AGENT_HOSTNAME": "somehost", "DD_TRACE_AGENT_PORT": "5678"})

        backend_connector_mock = (
            mock_backend_connector().with_get_json_response("/info", {"endpoints": ["/evp_proxy/v4/"]}).build()
        )
        with patch("ddtrace.testing.internal.http.BackendConnector", return_value=backend_connector_mock):
            connector_setup = BackendConnectorSetup.detect_setup()

        assert isinstance(connector_setup, BackendConnectorEVPProxySetup)

        connector = connector_setup.get_connector_for_subdomain(Subdomain.API)
        assert isinstance(connector.conn, http.client.HTTPConnection)
        assert connector.conn.host == "somehost"
        assert connector.conn.port == 5678
        assert connector.base_path == "/evp_proxy/v4"
        assert connector.use_gzip is True
        assert connector.default_headers["X-Datadog-EVP-Subdomain"] == "api"

    def test_detect_evp_proxy_mode_v4_custom_dd_agent_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "environ", {"DD_AGENT_HOST": "somehost", "DD_AGENT_PORT": "5678"})

        backend_connector_mock = (
            mock_backend_connector().with_get_json_response("/info", {"endpoints": ["/evp_proxy/v4/"]}).build()
        )
        with patch("ddtrace.testing.internal.http.BackendConnector", return_value=backend_connector_mock):
            connector_setup = BackendConnectorSetup.detect_setup()

        assert isinstance(connector_setup, BackendConnectorEVPProxySetup)

        connector = connector_setup.get_connector_for_subdomain(Subdomain.API)
        assert isinstance(connector.conn, http.client.HTTPConnection)
        assert connector.conn.host == "somehost"
        assert connector.conn.port == 5678
        assert connector.base_path == "/evp_proxy/v4"
        assert connector.use_gzip is True
        assert connector.default_headers["X-Datadog-EVP-Subdomain"] == "api"

    def test_detect_evp_proxy_mode_v4_custom_dd_trace_agent_unix_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "environ", {"DD_TRACE_AGENT_URL": "unix:///some/file/name.socket"})

        backend_connector_mock = (
            mock_backend_connector().with_get_json_response("/info", {"endpoints": ["/evp_proxy/v4/"]}).build()
        )
        with patch("ddtrace.testing.internal.http.BackendConnector", return_value=backend_connector_mock):
            with patch("os.path.exists", return_value=True):
                connector_setup = BackendConnectorSetup.detect_setup()

        assert isinstance(connector_setup, BackendConnectorEVPProxySetup)

        connector = connector_setup.get_connector_for_subdomain(Subdomain.API)
        assert isinstance(connector.conn, UnixDomainSocketHTTPConnection)
        assert connector.conn.host == "localhost"
        assert connector.conn.port == 80
        assert connector.conn.path == "/some/file/name.socket"
        assert connector.base_path == "/evp_proxy/v4"
        assert connector.use_gzip is True
        assert connector.default_headers["X-Datadog-EVP-Subdomain"] == "api"
