from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from enum import Enum
import gzip
import http.client
import io
import json
import logging
import os
import random
import socket
import threading
import time
import typing as t
from urllib.parse import ParseResult
from urllib.parse import urlparse
import uuid

from ddtrace.testing.internal.constants import DEFAULT_AGENT_HOSTNAME
from ddtrace.testing.internal.constants import DEFAULT_AGENT_PORT
from ddtrace.testing.internal.constants import DEFAULT_AGENT_SOCKET_FILE
from ddtrace.testing.internal.constants import DEFAULT_SITE
from ddtrace.testing.internal.errors import SetupError
from ddtrace.testing.internal.telemetry import ErrorType
from ddtrace.testing.internal.telemetry import TelemetryAPIRequestMetrics
from ddtrace.testing.internal.utils import asbool


DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_ATTEMPTS = 5

log = logging.getLogger(__name__)

T = t.TypeVar("T")


class BackendError(Exception):
    pass


@dataclass
class BackendResult:
    error_type: t.Optional[ErrorType] = None
    error_description: t.Optional[str] = None
    response: t.Optional[http.client.HTTPResponse] = None
    response_length: t.Optional[int] = None
    response_body: t.Optional[bytes] = None
    parsed_response: t.Any = None
    is_gzip_response: bool = False
    elapsed_seconds: float = 0.0

    def on_error_raise_exception(self) -> None:
        if self.error_type:
            raise BackendError(self.error_description)


class Subdomain(str, Enum):
    API = "api"
    CITESTCYCLE = "citestcycle-intake"
    CITESTCOV = "citestcov-intake"


RETRIABLE_ERRORS = {ErrorType.TIMEOUT, ErrorType.NETWORK, ErrorType.CODE_5XX, ErrorType.BAD_JSON}


class BackendConnectorSetup:
    """
    Logic for detecting the backend connection mode (agentless or EVP) and creating new connectors.
    """

    @abstractmethod
    def get_connector_for_subdomain(self, subdomain: Subdomain) -> BackendConnector:
        """
        Return a backend connector for the given subdomain (e.g., api, citestcov-intake, citestcycle-intake).

        This method must be implemented for each backend connection mode subclass.
        """
        pass

    @classmethod
    def detect_setup(cls) -> BackendConnectorSetup:
        """
        Detect which backend connection mode to use and return a configured instance of the corresponding subclass.
        """
        if asbool(os.environ.get("DD_CIVISIBILITY_AGENTLESS_ENABLED")):
            log.info("Connecting to backend in agentless mode")
            return cls._detect_agentless_setup()

        else:
            log.info("Connecting to backend through agent in EVP proxy mode")
            return cls._detect_evp_proxy_setup()

    @classmethod
    def _detect_agentless_setup(cls) -> BackendConnectorSetup:
        """
        Detect settings for agentless backend connection mode.
        """
        site = os.environ.get("DD_SITE") or DEFAULT_SITE
        api_key = os.environ.get("_CI_DD_API_KEY") or os.environ.get("DD_API_KEY")

        if not api_key:
            raise SetupError("DD_API_KEY environment variable is not set")

        return BackendConnectorAgentlessSetup(site=site, api_key=api_key)

    @classmethod
    def _detect_evp_proxy_setup(cls) -> BackendConnectorSetup:
        """
        Detect settings for EVP proxy mode backend connection mode.
        """
        agent_url = os.environ.get("_CI_DD_AGENT_URL") or os.environ.get("DD_TRACE_AGENT_URL")
        if not agent_url:
            user_provided_host = os.environ.get("DD_TRACE_AGENT_HOSTNAME") or os.environ.get("DD_AGENT_HOST")
            user_provided_port = os.environ.get("DD_TRACE_AGENT_PORT") or os.environ.get("DD_AGENT_PORT")

            if user_provided_host or user_provided_port:
                host = user_provided_host or DEFAULT_AGENT_HOSTNAME
                port = user_provided_port or DEFAULT_AGENT_PORT
                agent_url = f"http://{host}:{port}"

            elif os.path.exists(DEFAULT_AGENT_SOCKET_FILE):
                agent_url = f"unix://{DEFAULT_AGENT_SOCKET_FILE}"

            else:
                agent_url = f"http://{DEFAULT_AGENT_HOSTNAME}:{DEFAULT_AGENT_PORT}"

        # Get info from agent to check if the agent is there, and which EVP proxy version it supports.
        try:
            connector = BackendConnector(agent_url)
            result = connector.get_json("/info", max_attempts=2)
            connector.close()
        except Exception as e:
            raise SetupError(f"Error connecting to Datadog agent at {agent_url}: {e}")

        if result.error_type:
            raise SetupError(f"Error connecting to Datadog agent at {agent_url}: {result.error_description}")

        endpoints = result.parsed_response.get("endpoints", [])

        if "/evp_proxy/v4/" in endpoints:
            return BackendConnectorEVPProxySetup(url=agent_url, base_path="/evp_proxy/v4", use_gzip=True)

        if "/evp_proxy/v2/" in endpoints:
            return BackendConnectorEVPProxySetup(url=agent_url, base_path="/evp_proxy/v2", use_gzip=False)

        raise SetupError(f"Datadog agent at {agent_url} does not support EVP proxy mode")


class BackendConnectorAgentlessSetup(BackendConnectorSetup):
    def __init__(self, site: str, api_key: str) -> None:
        self.site = site
        self.api_key = api_key

    def get_connector_for_subdomain(self, subdomain: Subdomain) -> BackendConnector:
        if subdomain == Subdomain.CITESTCYCLE and (agentless_url := os.environ.get("DD_CIVISIBILITY_AGENTLESS_URL")):
            url = agentless_url
        else:
            url = f"https://{subdomain.value}.{self.site}"

        return BackendConnector(
            url=url,
            default_headers={"dd-api-key": self.api_key},
            use_gzip=True,
        )


class BackendConnectorEVPProxySetup(BackendConnectorSetup):
    def __init__(self, url: str, base_path: str, use_gzip: bool) -> None:
        self.url = url
        self.base_path = base_path
        self.use_gzip = use_gzip

    def get_connector_for_subdomain(self, subdomain: Subdomain) -> BackendConnector:
        return BackendConnector(
            url=self.url,
            base_path=self.base_path,
            default_headers={"X-Datadog-EVP-Subdomain": subdomain.value},
            use_gzip=self.use_gzip,
        )


class BackendConnector(threading.local):
    def __init__(
        self,
        url: str,
        default_headers: t.Optional[t.Dict[str, str]] = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        base_path: t.Optional[str] = None,
        use_gzip: bool = False,
    ):
        parsed_url = urlparse(url)
        self.conn = self._make_connection(parsed_url, timeout_seconds)
        self.default_headers = default_headers or {}
        self.base_path = base_path if base_path is not None else parsed_url.path.rstrip("/")
        self.use_gzip = use_gzip
        if self.use_gzip:
            self.default_headers["Accept-Encoding"] = "gzip"

    def close(self) -> None:
        self.conn.close()

    def _make_connection(self, parsed_url: ParseResult, timeout_seconds: float) -> http.client.HTTPConnection:
        if parsed_url.scheme == "http":
            if not parsed_url.hostname:
                raise SetupError(f"No hostname provided in {parsed_url.geturl()}")

            return http.client.HTTPConnection(
                host=parsed_url.hostname, port=parsed_url.port or 80, timeout=timeout_seconds
            )

        if parsed_url.scheme == "https":
            if not parsed_url.hostname:
                raise SetupError(f"No hostname provided in {parsed_url.geturl()}")

            return http.client.HTTPSConnection(
                host=parsed_url.hostname, port=parsed_url.port or 443, timeout=timeout_seconds
            )

        if parsed_url.scheme == "unix":
            # These URLs usually have an empty hostname component, e.g., unix:///var/run/datadog/apm.socket, that is,
            # unix:// + empty hostname + /var/run/datadog/apm.socket (pathname of the socket). We need to ensure some
            # hostname is used so the `Host` header will be passed correctly in requests to the agent, so we use the
            # default hostname (localhost) if none is provided.
            return UnixDomainSocketHTTPConnection(
                host=parsed_url.hostname or DEFAULT_AGENT_HOSTNAME,
                port=parsed_url.port or 80,
                timeout=timeout_seconds,
                path=parsed_url.path,
            )

        raise SetupError(f"Unknown scheme {parsed_url.scheme} in {parsed_url.geturl()}")

    def _do_single_request(
        self,
        method: str,
        path: str,
        data: t.Optional[bytes] = None,
        headers: t.Optional[t.Dict[str, str]] = None,
        send_gzip: bool = False,
        is_json_response: bool = False,
    ) -> BackendResult:
        full_headers = self.default_headers | (headers or {})

        if send_gzip and self.use_gzip and data is not None:
            data = gzip.compress(data, compresslevel=6)
            full_headers["Content-Encoding"] = "gzip"

        result = BackendResult()
        start_time = time.perf_counter()

        try:
            self.conn.request(method, self.base_path + path, body=data, headers=full_headers)
            result.response = self.conn.getresponse()
            result.response_length = int(result.response.headers.get("Content-Length") or "0")
            result.is_gzip_response = result.response.headers.get("Content-Encoding") == "gzip"
            if result.is_gzip_response:
                result.response_body = response_body = gzip.open(result.response).read()
            else:
                result.response_body = response_body = result.response.read()

            if not (200 <= result.response.status <= 299):
                result.error_description = f"{result.response.status} {result.response.reason}"
                if result.response.status >= 500:
                    result.error_type = ErrorType.CODE_5XX
                elif result.response.status >= 400:
                    result.error_type = ErrorType.CODE_4XX
                else:
                    result.error_type = ErrorType.NETWORK
        except (TimeoutError, socket.timeout) as e:
            result.error_type = ErrorType.TIMEOUT
            result.error_description = str(e)
        except (ConnectionRefusedError, http.client.HTTPException) as e:
            result.error_type = ErrorType.NETWORK
            result.error_description = str(e)
        except Exception as e:
            result.error_type = ErrorType.UNKNOWN
            result.error_description = str(e)
            log.exception("Error requesting %s %s", method, path)
        finally:
            result.elapsed_seconds = time.perf_counter() - start_time

        if not result.error_type and is_json_response:
            try:
                result.parsed_response = json.loads(response_body)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                result.error_type = ErrorType.BAD_JSON
                result.error_description = str(e)
            except Exception as e:
                log.exception("Error parsing respose for %s %s", method, path)
                result.error_type = ErrorType.UNKNOWN
                result.error_description = str(e)

        if result.error_type:
            self.conn.close()  # Clean up bad state, ensure subsequent requests start with a fresh connection.

        return result

    def request(
        self,
        method: str,
        path: str,
        data: t.Optional[bytes] = None,
        headers: t.Optional[t.Dict[str, str]] = None,
        send_gzip: bool = False,
        is_json_response: bool = False,
        telemetry: t.Optional[TelemetryAPIRequestMetrics] = None,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> BackendResult:
        attempts_so_far = 0

        while True:
            attempts_so_far += 1
            result = self._do_single_request(
                method=method,
                path=path,
                data=data,
                headers=headers,
                send_gzip=send_gzip,
                is_json_response=is_json_response,
            )

            if telemetry:
                telemetry.record_request(
                    seconds=result.elapsed_seconds,
                    response_bytes=result.response_length,
                    compressed_response=result.is_gzip_response,
                    error=result.error_type,
                )

            if result.error_type and result.error_type in RETRIABLE_ERRORS and attempts_so_far < max_attempts:
                delay_seconds = random.uniform(0, (1.618 ** (attempts_so_far - 1)))  # nosec: B311
                log.debug(
                    "Retrying %s %s in %.3f seconds (%d attempts so far)", method, path, delay_seconds, attempts_so_far
                )
                time.sleep(delay_seconds)
            else:
                break

        return result

    def get_json(
        self,
        path: str,
        headers: t.Optional[t.Dict[str, str]] = None,
        send_gzip: bool = False,
        telemetry: t.Optional[TelemetryAPIRequestMetrics] = None,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> BackendResult:
        headers = {"Content-Type": "application/json"} | (headers or {})
        return self.request(
            "GET",
            path=path,
            headers=headers,
            send_gzip=send_gzip,
            is_json_response=True,
            telemetry=telemetry,
            max_attempts=max_attempts,
        )

    def post_json(
        self,
        path: str,
        data: t.Any,
        headers: t.Optional[t.Dict[str, str]] = None,
        send_gzip: bool = False,
        telemetry: t.Optional[TelemetryAPIRequestMetrics] = None,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> BackendResult:
        headers = {"Content-Type": "application/json"} | (headers or {})
        encoded_data = json.dumps(data).encode("utf-8")
        return self.request(
            "POST",
            path=path,
            data=encoded_data,
            headers=headers,
            send_gzip=send_gzip,
            is_json_response=True,
            telemetry=telemetry,
            max_attempts=max_attempts,
        )

    def post_files(
        self,
        path: str,
        files: t.List[FileAttachment],
        headers: t.Optional[t.Dict[str, str]] = None,
        send_gzip: bool = False,
        telemetry: t.Optional[TelemetryAPIRequestMetrics] = None,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> BackendResult:
        boundary = uuid.uuid4().hex
        boundary_bytes = boundary.encode("utf-8")
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"} | (headers or {})
        body = io.BytesIO()

        for attachment in files:
            body.write(b"--%s\r\n" % boundary_bytes)
            body.write(b'Content-Disposition: form-data; name="%s"' % attachment.name.encode("utf-8"))
            if attachment.filename:
                body.write(b'; filename="%s"' % attachment.filename.encode("utf-8"))
            body.write(b"\r\n")
            body.write(b"Content-Type: %s\r\n" % attachment.content_type.encode("utf-8"))
            body.write(b"\r\n")
            body.write(attachment.data)
            body.write(b"\r\n")

        body.write(b"--%s--\r\n" % boundary_bytes)

        return self.request(
            "POST",
            path=path,
            data=body.getvalue(),
            headers=headers,
            send_gzip=send_gzip,
            telemetry=telemetry,
            max_attempts=max_attempts,
        )


@dataclass
class FileAttachment:
    name: str
    filename: t.Optional[str]
    content_type: str
    data: bytes


class UnixDomainSocketHTTPConnection(http.client.HTTPConnection):
    """An HTTP connection established over a Unix Domain Socket."""

    # It's important to keep the hostname and port arguments here; while there are not used by the connection
    # mechanism, they are actually used as HTTP headers such as `Host`.
    def __init__(self, path: str, *args: t.Any, **kwargs: t.Any) -> None:
        super().__init__(*args, **kwargs)
        self.path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self.path)
        self.sock = sock
