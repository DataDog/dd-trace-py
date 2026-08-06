"""
Agentless Feature Flagging configuration source.

Polls the Datadog UFC CDN (or a configured custom endpoint) for Universal Flag
Configuration and feeds each accepted payload into the same apply function the
Agent Remote Config path uses. A failed poll never replaces last-known-good.

The poller runs on a background thread via :class:`PeriodicService`. That thread
already implements fixed-delay-after-completion scheduling, so polls never
overlap. The per-poll retry/backoff and per-request timeout live inside a single
:meth:`periodic` tick.
"""

from collections import namedtuple
import os
import random
import socket
import threading
import time
from typing import Any
from typing import Callable
from typing import Optional
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

from ddtrace.internal.constants import _HTTPLIB_NO_TRACE_REQUEST
from ddtrace.internal.logger import get_logger
from ddtrace.internal.openfeature._agentless import decode_response_body
from ddtrace.internal.openfeature._agentless import parse_ufc_configuration
from ddtrace.internal.periodic import PeriodicService
from ddtrace.internal.utils.http import get_connection
from ddtrace.internal.utils.retry import RetryError
from ddtrace.internal.utils.retry import retry
from ddtrace.internal.utils.version import _pep440_to_semver


log = get_logger(__name__)

# Polling / retry policy (mirrors the dd-trace-js reference implementation).
MAX_POLL_INTERVAL_SECONDS = 60 * 60
DEFAULT_POLL_INTERVAL_SECONDS = 30.0
DEFAULT_REQUEST_TIMEOUT_SECONDS = 5.0

MAX_ATTEMPTS = 3
FIRST_RETRY_MIN_S = 2.0
FIRST_RETRY_MAX_S = 10.0
SECOND_RETRY_MIN_S = 5.0
SECOND_RETRY_MAX_S = 30.0
RETRY_JITTER = 0.2

# Upper bound (seconds) on the randomized delay before the FIRST poll in a
# forked child, so pre-fork workers (gunicorn/uWSGI) don't hit the CDN in
# lockstep. The origin process is never delayed.
FIRST_POLL_JITTER_MAX_S = 5.0

# Granularity of in-tick waits (retry backoff, fork jitter). Waits are sliced at
# this interval so a requested shutdown is noticed without an event primitive.
SHUTDOWN_POLL_INTERVAL_S = 0.2
_WAIT_EPSILON_S = 1e-6


# A single poll outcome. ``status`` is None for a network error / timeout.
_PollResponse = namedtuple("_PollResponse", ["status", "etag", "content_encoding", "body", "error"])


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _is_retryable_status(status: Optional[int]) -> bool:
    if status is None:
        return True
    return status == 408 or status == 429 or (500 <= status <= 599)


class AgentlessConfigurationSource(PeriodicService):
    """Background poller that loads UFC from the agentless endpoint."""

    def __init__(
        self,
        endpoint: str,
        apply_configuration: Callable[["dict[str, Any]"], None],
        api_key: Optional[str] = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        # A non-positive interval would make PeriodicThread schedule the next run
        # immediately, turning polling into a tight loop against the CDN, so fall
        # back to the documented default.
        if poll_interval <= 0:
            log.warning(
                "Feature Flagging agentless poll interval must be positive; using the default %.0fs",
                DEFAULT_POLL_INTERVAL_SECONDS,
            )
            poll_interval = DEFAULT_POLL_INTERVAL_SECONDS
        elif poll_interval > MAX_POLL_INTERVAL_SECONDS:
            log.warning(
                "Feature Flagging agentless poll interval %.0fs exceeds the %ds maximum; clamping",
                poll_interval,
                MAX_POLL_INTERVAL_SECONDS,
            )
            poll_interval = MAX_POLL_INTERVAL_SECONDS

        if request_timeout <= 0:
            log.warning(
                "Feature Flagging agentless request timeout must be positive; using the default %.0fs",
                DEFAULT_REQUEST_TIMEOUT_SECONDS,
            )
            request_timeout = DEFAULT_REQUEST_TIMEOUT_SECONDS

        super().__init__(interval=poll_interval, no_wait_at_start=True)

        self._apply_configuration = apply_configuration
        self._api_key = api_key
        self._request_timeout = request_timeout

        # Split the endpoint into an origin (for the connection) and a request
        # target (path + query). get_connection drops the query, so the target
        # must carry it.
        parts = urlsplit(endpoint)
        self._conn_url = urlunsplit((parts.scheme, parts.netloc, "/", "", ""))
        self._request_target = urlunsplit(("", "", parts.path, parts.query, "")) or "/"

        self._etag: Optional[str] = None
        self._failure_warnings: "set[str]" = set()
        self._malformed_payload_logged = False
        self._application_failure_logged = False

        # Fork staggering: the process that created the source polls immediately;
        # a forked child jitters its first poll (see periodic()). Tracked by PID so
        # it is race-free with the automatic post-fork thread restart.
        self._origin_pid = os.getpid()
        self._jittered_pid: Optional[int] = None

        # Set on shutdown so in-tick waits (retry backoff, fork jitter) return
        # early instead of holding the worker thread for their full delay.
        self._stopping = False

        # The connection of the poll currently in flight, if any. Shutdown uses it
        # to unblock the worker thread mid-request; see _cancel_in_flight_request.
        # The lock keeps the shutdown thread from touching a connection the worker
        # is concurrently replacing or closing.
        self._conn_lock = threading.Lock()
        self._in_flight_conn: Optional[Any] = None

    # -- scheduling ---------------------------------------------------------

    def _start_service(self, *args: Any, **kwargs: Any) -> None:
        self._stopping = False
        super()._start_service(*args, **kwargs)

    def _stop_service(self, *args: Any, **kwargs: Any) -> None:
        # Request the stop first so any in-tick wait ends at its next slice and
        # the worker thread can finish promptly.
        self._stopping = True
        # Then tear down any request already on the wire. Without this, a caller
        # joining the worker waits out the per-request timeout on every blocking
        # socket call still to come (connect, then each read).
        self._cancel_in_flight_request()
        super()._stop_service(*args, **kwargs)

    def _cancel_in_flight_request(self) -> None:
        """Unblock a poll waiting on the network so shutdown does not wait for it.

        Half-closing the socket from this thread makes the worker's pending
        connect/recv return or raise at once; close() alone would not, since the
        worker is already blocked inside a syscall on that file descriptor. The
        worker owns cleanup either way -- _request closes the connection in its
        finally block, and the resulting error is reported as a failed poll, which
        keeps last-known-good.

        One window stays uncancellable: http.client connects lazily inside
        request(), so a stop that lands before the socket exists has nothing to
        half-close and the worker blocks in connect() for up to the request
        timeout. Requests already past connect -- the long pole, since a poll
        spends its time reading the UFC body -- are cancelled immediately.
        """
        with self._conn_lock:
            conn = self._in_flight_conn
            sock = getattr(conn, "sock", None) if conn is not None else None
            if sock is None:
                return
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                # Already closed, never connected, or shut down by the worker first.
                log.debug("Feature Flagging agentless request was not cancellable", exc_info=True)

    def _wait(self, delay: float) -> bool:
        """Wait up to ``delay`` seconds. Returns True if a shutdown was requested.

        The wait is sliced rather than done with an event because the library runs
        on native threads and has no Python-visible event primitive; polling a
        plain flag keeps shutdown responsive without one.
        """
        remaining = delay
        # The epsilon keeps floating-point drift from adding a final zero-length
        # slice (0.6 - 0.2 * 3 does not land exactly on zero).
        while remaining > _WAIT_EPSILON_S and not self._stopping:
            this_slice = min(remaining, SHUTDOWN_POLL_INTERVAL_S)
            time.sleep(this_slice)
            remaining -= this_slice
        return self._stopping

    def periodic(self) -> None:
        """Run one poll with in-tick retries; never let an error escape."""
        if self._stagger_first_poll_after_fork():
            return

        after = [self._retry_delay(1), self._retry_delay(2)]
        poll = retry(
            after=after,
            # Stop retrying on a decisive response, or as soon as a shutdown is
            # requested (the backoff wait below returns early in that case).
            until=lambda r: self._stopping or not _is_retryable_status(r.status),
            sleep_func=self._wait,
        )(self._request)
        try:
            response = poll()
        except RetryError as e:
            # All attempts failed with retryable outcomes; keep last-known-good.
            self._warn_failure(e.args[0], MAX_ATTEMPTS)
            return
        except Exception:
            log.debug("Feature Flagging agentless poll failed unexpectedly", exc_info=True)
            return

        # A shutdown mid-poll leaves the response unusable for state transitions;
        # keep last-known-good and the current ETag.
        if self._stopping:
            return

        self._apply(response)

    def _stagger_first_poll_after_fork(self) -> bool:
        """Delay the first poll in a forked child so workers don't poll in lockstep.

        The process that created the source (or any single-process run) is never
        delayed, so behavior matches dd-trace-js and leaves tests unaffected. A
        forked worker waits a random bounded delay before its first poll only.

        Returns True if a shutdown was requested while waiting, in which case the
        caller should skip the poll.
        """
        pid = os.getpid()
        if pid == self._origin_pid or self._jittered_pid == pid:
            return self._stopping
        self._jittered_pid = pid
        delay = random.uniform(0, min(self.interval, FIRST_POLL_JITTER_MAX_S))  # nosec B311
        return self._wait(delay)

    def _retry_delay(self, attempt: int) -> float:
        if attempt == 1:
            base = _clamp(self.interval / 6, FIRST_RETRY_MIN_S, FIRST_RETRY_MAX_S)
        else:
            base = _clamp(self.interval / 3, SECOND_RETRY_MIN_S, SECOND_RETRY_MAX_S)
        jitter = 1 - RETRY_JITTER + random.random() * RETRY_JITTER * 2  # nosec B311
        return max(1.0, base * jitter)

    # -- request ------------------------------------------------------------

    def _headers(self) -> "dict[str, str]":
        headers = {
            "Accept-Encoding": "gzip",
            "DD-Client-Library-Language": "python",
            "DD-Client-Library-Version": _pep440_to_semver(),
        }
        if self._api_key:
            headers["DD-API-KEY"] = self._api_key
        if self._etag:
            headers["If-None-Match"] = self._etag
        return headers

    def _request(self) -> _PollResponse:
        conn = None
        try:
            conn = get_connection(self._conn_url, timeout=self._request_timeout)
            # Publish the connection so a concurrent shutdown can half-close it
            # instead of waiting out the request timeout. Claiming the slot and
            # re-reading the stop flag under one lock keeps a poll from starting
            # after _stop_service has already looked for something to cancel.
            with self._conn_lock:
                if self._stopping:
                    return _PollResponse(status=None, etag=None, content_encoding=None, body=None, error=None)
                self._in_flight_conn = conn
            # Suppress self-tracing: no HTTP span, no trace-header injection.
            setattr(conn, _HTTPLIB_NO_TRACE_REQUEST, True)
            conn.request("GET", self._request_target, None, self._headers())  # type: ignore[no-untyped-call]
            resp = conn.getresponse()
            body = resp.read()
            return _PollResponse(
                status=resp.status,
                etag=resp.getheader("ETag"),
                content_encoding=resp.getheader("Content-Encoding"),
                body=body,
                error=None,
            )
        except Exception as e:
            return _PollResponse(status=None, etag=None, content_encoding=None, body=None, error=e)
        finally:
            with self._conn_lock:
                self._in_flight_conn = None
            if conn is not None:
                conn.close()

    # -- response handling --------------------------------------------------

    def _apply(self, response: _PollResponse) -> None:
        status = response.status

        if status == 304:
            return
        if status in (401, 403):
            self._warn_failure(response, 1)
            return
        if status != 200:
            # Non-2xx bodies are not decoded as config.
            return

        try:
            body = decode_response_body(response.body, response.content_encoding)
            attributes = parse_ufc_configuration(body)
        except Exception:
            if not self._malformed_payload_logged:
                self._malformed_payload_logged = True
                log.error("Feature Flagging agentless endpoint returned malformed UFC payload")
            return

        try:
            self._apply_configuration(attributes)
        except Exception as e:
            if not self._application_failure_logged:
                self._application_failure_logged = True
                log.warning("Feature Flagging agentless UFC payload could not be applied: %s", e)
            return

        # Advance the ETag only after parse AND apply both succeed. A blank or
        # absent ETag clears the previous one.
        etag = (response.etag or "").strip()
        self._etag = etag or None

    def _warn_failure(self, response: _PollResponse, attempts: int) -> None:
        status = response.status
        if status in (401, 403):
            category = "authentication"
        elif status:
            category = "http"
        else:
            category = "request"

        if category in self._failure_warnings:
            return
        self._failure_warnings.add(category)

        if status in (401, 403):
            log.warning("Feature Flagging agentless endpoint returned HTTP %d; verify endpoint authentication", status)
        elif status:
            log.warning("Feature Flagging agentless endpoint returned HTTP %d after %d attempts", status, attempts)
        elif attempts > 1:
            log.warning("Feature Flagging agentless request failed after %d attempts: %s", attempts, response.error)
        else:
            log.warning("Feature Flagging agentless request failed: %s", response.error)
