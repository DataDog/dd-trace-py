"""
Python-owned CDN source delivery for Feature Flagging and Experimentation.
"""

import dataclasses
import threading
import time
import typing as t
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.request import Request
from urllib.request import urlopen as default_urlopen

from ddtrace.internal.logger import get_logger
from ddtrace.internal.openfeature._native import process_ffe_configuration
from ddtrace.internal.openfeature._source import FeatureFlagCdnConfig


log = get_logger(__name__)


class CdnSourceError(Exception):
    def __init__(self, message: str, status_code: t.Optional[int] = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


@dataclasses.dataclass(frozen=True)
class CdnPollResult:
    status_code: t.Optional[int] = None
    applied: bool = False
    unchanged: bool = False
    skipped: bool = False
    attempts: int = 0
    error: t.Optional[CdnSourceError] = None


class PythonCdnSource:
    def __init__(
        self,
        config: FeatureFlagCdnConfig,
        urlopen: t.Callable[..., t.Any] = default_urlopen,
        sleep: t.Callable[[float], None] = time.sleep,
    ):
        self._config = config
        self._urlopen = urlopen
        self._sleep = sleep
        self._poll_lock = threading.Lock()
        self._shutdown = threading.Event()
        self._thread: t.Optional[threading.Thread] = None
        self._last_etag: t.Optional[str] = None
        self._last_successful_status_code: t.Optional[int] = None
        self._last_error: t.Optional[CdnSourceError] = None

    @property
    def last_etag(self) -> t.Optional[str]:
        return self._last_etag

    @property
    def last_error(self) -> t.Optional[CdnSourceError]:
        return self._last_error

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def poll_once(self) -> CdnPollResult:
        if not self._poll_lock.acquire(blocking=False):
            error = CdnSourceError("Feature flag CDN poll already in flight")
            return CdnPollResult(skipped=True, error=error)

        try:
            return self._poll_once_locked()
        finally:
            self._poll_lock.release()

    def start(self) -> None:
        if self.is_running:
            return
        self._shutdown.clear()
        self._thread = threading.Thread(target=self._run, name="ddtrace-openfeature-cdn-source", daemon=True)
        self._thread.start()

    def shutdown(self, timeout: t.Optional[float] = None) -> bool:
        self._shutdown.set()
        if self._thread is None:
            return True
        self._thread.join(timeout=timeout)
        return not self._thread.is_alive()

    def _run(self) -> None:
        while not self._shutdown.wait(self._config.poll_interval_seconds):
            result = self.poll_once()
            if result.error is not None:
                log.debug("Feature flag CDN poll failed: %s", result.error)

    def _poll_once_locked(self) -> CdnPollResult:
        attempts = 0
        while True:
            attempts += 1
            try:
                return self._send_once(attempts)
            except CdnSourceError as error:
                self._last_error = error
                if not error.retryable or attempts > self._config.max_retries:
                    return CdnPollResult(status_code=error.status_code, attempts=attempts, error=error)
                self._sleep(self._config.backoff_base_seconds * (2 ** (attempts - 1)))

    def _send_once(self, attempts: int) -> CdnPollResult:
        request = Request(self._config.base_url, method="GET")
        if self._config.api_key:
            request.add_header("DD-API-KEY", self._config.api_key)
        if self._last_etag:
            request.add_header("If-None-Match", self._last_etag)

        response = None
        try:
            response = self._urlopen(request, timeout=self._config.request_timeout_seconds)
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            status_code = int(status)
            headers = getattr(response, "headers", {})
            body = response.read()
        except HTTPError as error:
            status_code = int(error.code)
            headers = getattr(error, "headers", {})
            body = error.read() if error.fp is not None else b""
        except URLError as error:
            raise CdnSourceError("Feature flag CDN request failed: %s" % error, retryable=False)
        finally:
            if response is not None:
                close = getattr(response, "close", None)
                if close is not None:
                    close()

        if status_code == 304:
            self._last_successful_status_code = status_code
            self._last_error = None
            return CdnPollResult(status_code=status_code, unchanged=True, attempts=attempts)

        if status_code == 200:
            if not process_ffe_configuration(body):
                raise CdnSourceError("Feature flag CDN returned malformed UFC payload", status_code=status_code)
            self._last_etag = _header_get(headers, "ETag")
            self._last_successful_status_code = status_code
            self._last_error = None
            return CdnPollResult(status_code=status_code, applied=True, attempts=attempts)

        retryable = status_code == 429 or status_code >= 500
        raise CdnSourceError(
            "Feature flag CDN request failed with status %d" % status_code,
            status_code=status_code,
            retryable=retryable,
        )


def _header_get(headers: t.Any, name: str) -> t.Optional[str]:
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    return getter(name) or getter(name.lower()) or getter(name.upper())
