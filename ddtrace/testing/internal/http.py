from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from enum import Enum
import gzip
from http import HTTPStatus
import io
import json
import logging
import os
import random
import sys
import threading
import time
import typing as t
from urllib.parse import ParseResult
from urllib.parse import urlparse
import uuid

from ddtrace.internal.http_client import HTTPClient
from ddtrace.internal.native import ConnectionFailedError
from ddtrace.internal.native import HttpIoError
from ddtrace.internal.native import HttpResponse
from ddtrace.internal.native import TimedOutError
from ddtrace.internal.settings import env
from ddtrace.internal.settings._agentless import config as agentless_config
from ddtrace.testing.internal._protocols import BackendConnectorSetupProtocol
from ddtrace.testing.internal.constants import DEFAULT_AGENT_HOSTNAME
from ddtrace.testing.internal.constants import DEFAULT_AGENT_PORT
from ddtrace.testing.internal.constants import DEFAULT_AGENT_SOCKET_FILE
from ddtrace.testing.internal.constants import DEFAULT_ENV_NAME
from ddtrace.testing.internal.constants import DEFAULT_SITE
from ddtrace.testing.internal.errors import SetupError
from ddtrace.testing.internal.telemetry import ErrorType
from ddtrace.testing.internal.telemetry import TelemetryAPIRequestMetrics


log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 30.0
_MAX_TIMEOUT_SECONDS = 300.0  # 5 minutes


def _parse_timeout_millis(raw: t.Optional[str]) -> float:
    if raw:
        try:
            _parsed = float(raw) / 1000.0
            if not (0 < _parsed <= _MAX_TIMEOUT_SECONDS):
                raise ValueError("must be between 1 ms and 300 000 ms")
            return _parsed
        except ValueError:
            log.warning(
                "Invalid value for DD_CIVISIBILITY_BACKEND_API_TIMEOUT_MILLIS: %r; using default %.1fs",
                raw,
                _DEFAULT_TIMEOUT_SECONDS,
            )
    return _DEFAULT_TIMEOUT_SECONDS


DEFAULT_TIMEOUT_SECONDS = _parse_timeout_millis(env.get("DD_CIVISIBILITY_BACKEND_API_TIMEOUT_MILLIS"))

MAX_ATTEMPTS = 5
MAX_RETRY_AFTER_SECONDS = 120.0

T = t.TypeVar("T")


def _timeout_ms(timeout_seconds: float) -> int:
    # The native client rejects a timeout of 0.
    return max(1, int(timeout_seconds * 1000))


def _reason(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return ""


def _call_without_blocking_gevent(function: t.Callable[..., T], *args: t.Any, **kwargs: t.Any) -> T:
    """Call function, a native call that blocks its thread, without blocking gevent's other greenlets.

    Once gevent has patched threading, the plugin's threads are greenlets on the thread of the tests, and a native call
    would stop the tests until it returns, where Python's sockets let them run. Run it on one of gevent's own threads
    then, as gevent does for blocking calls.
    """
    gevent_monkey = sys.modules.get("gevent.monkey")
    if gevent_monkey is None or not gevent_monkey.is_module_patched("threading"):
        return function(*args, **kwargs)

    import gevent

    return gevent.get_hub().threadpool.apply(function, args, kwargs)


class BackendError(Exception):
    pass


@dataclass
class BackendResult:
    error_type: t.Optional[ErrorType] = None
    error_description: t.Optional[str] = None
    response: t.Optional[HttpResponse] = None
    request_length: t.Optional[int] = None
    response_length: t.Optional[int] = None
    response_body: t.Optional[bytes] = None
    parsed_response: t.Any = None
    is_gzip_response: bool = False
    elapsed_seconds: float = 0.0
    retry_after_seconds: t.Optional[float] = None

    def on_error_raise_exception(self) -> None:
        if self.error_type:
            raise BackendError(self.error_description)


class Subdomain(str, Enum):
    API = "api"
    CITESTCYCLE = "citestcycle-intake"
    CITESTCOV = "citestcov-intake"
    CICOVREPRT = "ci-intake"
    LOGS = "http-intake.logs"


RETRIABLE_ERRORS = {
    ErrorType.TIMEOUT,
    ErrorType.NETWORK,
    ErrorType.CODE_5XX,
    ErrorType.BAD_JSON,
    ErrorType.RATE_LIMITED,
}


class BackendConnectorSetup(BackendConnectorSetupProtocol):
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

    def default_env(self) -> str:
        return DEFAULT_ENV_NAME

    @classmethod
    def detect_setup(cls) -> BackendConnectorSetup:
        """
        Detect which backend connection mode to use and return a configured instance of the corresponding subclass.
        """
        if agentless_config.ci_visibility:
            log.debug("Connecting to backend in agentless mode")
            return cls._detect_agentless_setup(api_key_override=env.get("_CI_DD_API_KEY"))

        else:
            log.debug("Connecting to backend through agent in EVP proxy mode")
            return cls._detect_evp_proxy_setup(agent_url_override=env.get("_CI_DD_AGENT_URL"))

    @classmethod
    def detect_standard_setup(cls) -> BackendConnectorSetup:
        """
        Detect backend connection mode using standard public environment variables only.

        Unlike :meth:`detect_setup`, this method does not consult any dd-trace-py-internal
        environment variables, making it suitable for use in customer-facing code.
        """
        if agentless_config.ci_visibility:
            log.debug("Connecting to backend in agentless mode")
            return cls._detect_agentless_setup()

        else:
            log.debug("Connecting to backend through agent in EVP proxy mode")
            return cls._detect_evp_proxy_setup()

    @classmethod
    def _detect_agentless_setup(cls, *, api_key_override: t.Optional[str] = None) -> BackendConnectorSetup:
        """
        Detect settings for agentless backend connection mode.
        """
        site = env.get("DD_SITE") or DEFAULT_SITE
        api_key = api_key_override or env.get("DD_API_KEY")

        if not api_key:
            raise SetupError("DD_API_KEY environment variable is not set")

        return BackendConnectorAgentlessSetup(site=site, api_key=api_key)

    @classmethod
    def _detect_evp_proxy_setup(cls, *, agent_url_override: t.Optional[str] = None) -> BackendConnectorSetup:
        """
        Detect settings for EVP proxy mode backend connection mode.
        """
        agent_url = agent_url_override or env.get("DD_TRACE_AGENT_URL")
        if not agent_url:
            user_provided_host = env.get("DD_TRACE_AGENT_HOSTNAME") or env.get("DD_AGENT_HOST")
            user_provided_port = env.get("DD_TRACE_AGENT_PORT") or env.get("DD_AGENT_PORT")

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
        if agentless_url := env.get("DD_CIVISIBILITY_AGENTLESS_URL"):
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

    def default_env(self) -> str:
        try:
            connector = BackendConnector(self.url)
            result = connector.get_json("/info", max_attempts=2)
            connector.close()
        except Exception as e:
            raise SetupError(f"Error connecting to Datadog agent at {self.url}: {e}")

        if result.error_type:
            raise SetupError(f"Error connecting to Datadog agent at {self.url}: {result.error_description}")

        if result:
            default_env = result.parsed_response.get("config", {}).get("default_env", DEFAULT_ENV_NAME)
            return default_env

        return DEFAULT_ENV_NAME

    def get_connector_for_subdomain(self, subdomain: Subdomain) -> BackendConnector:
        return BackendConnector(
            url=self.url,
            base_path=self.base_path,
            default_headers={"X-Datadog-EVP-Subdomain": subdomain.value},
            use_gzip=self.use_gzip,
        )


class BackendConnector(threading.local):
    """Sends requests to Datadog or to the agent with ddtrace's native HTTP client.

    The native client opens its connections outside Python's socket module, so the libraries tests use to block or
    record network access, such as pytest-socket or vcrpy, don't reach them.
    """

    def __init__(
        self,
        url: str,
        default_headers: t.Optional[dict[str, str]] = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        base_path: t.Optional[str] = None,
        use_gzip: bool = False,
    ):
        parsed_url = urlparse(url)
        self.base_url = self._base_url(parsed_url)
        self.timeout_seconds = timeout_seconds
        self.client = HTTPClient(
            self.base_url, timeout_ms=_timeout_ms(timeout_seconds), treat_http_errors_as_errors=False
        )
        self.default_headers = default_headers or {}
        if base_path is None:
            # The path of a unix:// URL is the socket's, not a prefix of the request paths.
            base_path = "" if parsed_url.scheme == "unix" else parsed_url.path.rstrip("/")
        self.base_path = base_path
        self.use_gzip = use_gzip
        if self.use_gzip:
            self.default_headers["Accept-Encoding"] = "gzip"

    def close(self) -> None:
        # The native client pools and closes its connections itself.
        pass

    @staticmethod
    def _base_url(parsed_url: ParseResult) -> str:
        if parsed_url.scheme in ("http", "https"):
            if not parsed_url.hostname:
                raise SetupError(f"No hostname provided in {parsed_url.geturl()}")
            # Without the path, which goes into base_path, or any user info.
            return f"{parsed_url.scheme}://{parsed_url.netloc.rpartition('@')[2]}"
        if parsed_url.scheme == "unix":
            # The native client sends requests over the socket with localhost as the Host header.
            return f"unix://{parsed_url.path}"
        raise SetupError(f"Unknown scheme {parsed_url.scheme} in {parsed_url.geturl()}")

    def _send(self, method: str, path: str, data: t.Optional[bytes], headers: dict[str, str]) -> HttpResponse:
        kwargs: dict[str, t.Any] = {"headers": list(headers.items()), "timeout_ms": _timeout_ms(self.timeout_seconds)}
        if data is not None:
            kwargs["body"] = data
        return _call_without_blocking_gevent(getattr(self.client, method.lower()), path, **kwargs)

    def _do_single_request(
        self,
        method: str,
        path: str,
        data: t.Optional[bytes] = None,
        headers: t.Optional[dict[str, str]] = None,
        send_gzip: bool = False,
        is_json_response: bool = False,
    ) -> BackendResult:
        full_headers = self.default_headers | (headers or {})

        if send_gzip and self.use_gzip and data is not None:
            data = gzip.compress(data, compresslevel=6)
            full_headers["Content-Encoding"] = "gzip"

        result = BackendResult()
        result.request_length = len(data) if data is not None else 0
        start_time = time.perf_counter()

        try:
            result.response = response = self._send(method, self.base_path + path, data, full_headers)
            status = response.status_code
            result.response_length = int(response.header("Content-Length") or "0")
            result.is_gzip_response = response.header("Content-Encoding") == "gzip"
            if result.is_gzip_response:
                result.response_body = response_body = gzip.decompress(response.body())
            else:
                result.response_body = response_body = response.body()

            if not (200 <= status <= 299):
                result.error_description = f"{status} {_reason(status)}"
                if status >= 500:
                    result.error_type = ErrorType.CODE_5XX
                elif status == 429:
                    result.error_type = ErrorType.RATE_LIMITED
                    reset_header = response.header("X-RateLimit-Reset")
                    if reset_header is not None:
                        try:
                            reset_value = int(reset_header)
                            now = int(time.time())
                            if reset_value > now:
                                # Unix timestamp: wait until that point in time
                                delay = float(reset_value - now)
                            else:
                                # Duration in seconds
                                delay = float(reset_value)
                            # Cap to avoid unreasonable waits (e.g. expired timestamp misread as duration)
                            result.retry_after_seconds = min(delay, MAX_RETRY_AFTER_SECONDS)
                        except ValueError:
                            pass  # Fall back to exponential backoff in the retry loop
                elif status >= 400:
                    result.error_type = ErrorType.CODE_4XX
                else:
                    result.error_type = ErrorType.NETWORK
        except TimedOutError as e:
            result.error_type = ErrorType.TIMEOUT
            result.error_description = str(e)
        except (ConnectionFailedError, HttpIoError) as e:
            result.error_type = ErrorType.NETWORK
            result.error_description = str(e)
        except Exception as e:
            result.error_type = ErrorType.UNKNOWN
            result.error_description = str(e)
            log.warning("Error requesting %s %s", method, path)
        finally:
            result.elapsed_seconds = time.perf_counter() - start_time

        if not result.error_type and is_json_response:
            try:
                result.parsed_response = json.loads(response_body)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                result.error_type = ErrorType.BAD_JSON
                result.error_description = str(e)
            except Exception as e:
                log.warning("Error parsing response for %s %s", method, path)
                result.error_type = ErrorType.UNKNOWN
                result.error_description = str(e)

        return result

    def request(
        self,
        method: str,
        path: str,
        data: t.Optional[bytes] = None,
        headers: t.Optional[dict[str, str]] = None,
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
                    request_bytes=result.request_length,
                )

            if result.error_type and result.error_type in RETRIABLE_ERRORS and attempts_so_far < max_attempts:
                if result.retry_after_seconds is not None:
                    delay_seconds = result.retry_after_seconds
                else:
                    delay_seconds = random.uniform(0, (1.618 ** (attempts_so_far - 1)))  # nosec: B311
                log.debug(
                    "Retrying %s %s in %.3f seconds (%d attempts so far)", method, path, delay_seconds, attempts_so_far
                )
                time.sleep(delay_seconds)
            else:
                break

        if result.error_type:
            log.warning(
                "Request %s %s failed after %d attempt(s): %s", method, path, attempts_so_far, result.error_description
            )

        return result

    def get_json(
        self,
        path: str,
        headers: t.Optional[dict[str, str]] = None,
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
        headers: t.Optional[dict[str, str]] = None,
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
        files: list[FileAttachment],
        headers: t.Optional[dict[str, str]] = None,
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


class NoOpBackendConnector:
    """
    A connector that makes no network requests.

    Used when the plugin is running in Bazel's hermetic sandbox (manifest mode
    active), where network access is unavailable. Any call to ``request()`` or
    its helpers is silently discarded and an empty ``BackendResult`` is returned.
    Writers and the telemetry API receive this connector but their event
    delivery is handled via the payload-files code path instead.
    """

    def close(self) -> None:
        pass

    def request(
        self,
        method: str,
        path: str,
        data: t.Optional[bytes] = None,
        headers: t.Optional[dict[str, str]] = None,
        send_gzip: bool = False,
        is_json_response: bool = False,
        telemetry: t.Any = None,
        max_attempts: int = 1,
    ) -> BackendResult:
        log.debug("NoOp connector: skipping %s %s in offline mode", method, path)
        return BackendResult()

    def get_json(self, path: str, **kwargs: t.Any) -> BackendResult:
        return BackendResult()

    def post_json(self, path: str, data: t.Any, **kwargs: t.Any) -> BackendResult:
        return BackendResult()

    def post_files(self, path: str, files: t.Any, **kwargs: t.Any) -> BackendResult:
        return BackendResult()


class NoOpBackendConnectorSetup(BackendConnectorSetup):
    """
    A connector setup for fully offline (Bazel sandbox) mode.

    Returns ``NoOpBackendConnector`` instances for all subdomains so that no
    network requests are attempted. ``default_env`` falls back to the standard
    default because there is no agent to query.
    """

    def get_connector_for_subdomain(self, subdomain: Subdomain) -> NoOpBackendConnector:  # type: ignore[override]
        return NoOpBackendConnector()
