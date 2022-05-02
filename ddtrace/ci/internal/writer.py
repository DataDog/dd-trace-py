import binascii
import logging
import os
import threading
from typing import Dict
from typing import List
from typing import Optional
from typing import TYPE_CHECKING

from _typeshed import SupportsKeysAndGetItem
import six
import tenacity

import ddtrace
from ddtrace.internal import agent
from ddtrace.internal import compat
from ddtrace.internal._encoding import BufferFull
from ddtrace.internal._encoding import BufferItemTooLarge
from ddtrace.internal.agent import get_connection
from ddtrace.internal.logger import get_logger
from ddtrace.internal.runtime import container
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.formats import parse_tags_str
from ddtrace.internal.utils.time import StopWatch
from ddtrace.internal.writer import Response
from ddtrace.internal.writer import TraceWriter
from ddtrace.internal.writer import _human_size
from ddtrace.internal.writer import get_writer_buffer_size
from ddtrace.internal.writer import get_writer_max_payload_size
from ddtrace.internal.writer import get_writer_reuse_connections

from .encoding import AgentlessEncoderV1


if TYPE_CHECKING:
    from ddtrace import Span
    from ddtrace.internal.agent import ConnectionType


log = get_logger(__name__)


class AgentlessWriter(TraceWriter):
    """Writer to the intake endpoint."""

    RETRY_ATTEMPTS = 5

    def __init__(
        self,
        intake_url,  # type: str
        api_key=None,  # type: Optional[str]
        # Match the payload size since there is no functionality
        # to flush dynamically.
        buffer_size=None,  # type: Optional[int]
        max_payload_size=None,  # type: Optional[int]
        timeout=agent.get_trace_agent_timeout(),  # type: float
        reuse_connections=None,  # type: Optional[bool]
        headers=None,  # type: Optional[Dict[str, str]]
        metadata=None,  # type: Optional[SupportsKeysAndGetItem[str, Dict[str, str]]]
    ):
        # type: (...) -> None
        # Pre-conditions:
        if buffer_size is not None and buffer_size <= 0:
            raise ValueError("Writer buffer size must be positive")
        if max_payload_size is not None and max_payload_size <= 0:
            raise ValueError("Max payload size must be positive")

        super(AgentlessWriter, self).__init__()
        self.intake_url = intake_url  # https://citestcycle-intake.{site}
        self.interval = 5  # seconds
        self._api_key = api_key or os.environ.get("DATADOG_API_KEY", os.environ.get("DD_API_KEY"))
        self._buffer_size = buffer_size or get_writer_buffer_size()
        self._max_payload_size = max_payload_size or get_writer_max_payload_size()

        self._headers = {
            # TODO os.environ.get("DD_API_KEY", os.environ.get("DATADOG_API_KEY"))
            "dd-api-key": api_key,
            "Datadog-Meta-Lang": "python",
            "Datadog-Meta-Lang-Version": compat.PYTHON_VERSION,
            "Datadog-Meta-Lang-Interpreter": compat.PYTHON_INTERPRETER,
            "Datadog-Meta-Tracer-Version": ddtrace.__version__,
        }
        if headers:
            self._headers.update(headers)
        self._container_info = container.get_container_info()
        if self._container_info and self._container_info.container_id:
            self._headers.update(
                {
                    "Datadog-Container-Id": self._container_info.container_id,
                }
            )

        self._metadata = metadata
        self._timeout = timeout

        self._endpoint = "/api/v2/citestcycle"

        self._encoder = AgentlessEncoderV1(
            max_size=self._buffer_size,
            max_item_size=self._max_payload_size,
        )
        if metadata:
            self._encoder.metadata.update(metadata)

        self._headers.update({"Content-Type": self._encoder.content_type})
        additional_header_str = os.environ.get("_DD_TRACE_WRITER_ADDITIONAL_HEADERS")
        if additional_header_str is not None:
            self._headers.update(parse_tags_str(additional_header_str))
        self._conn = None  # type: Optional[ConnectionType]
        # The connection has to be locked since there exists a race between
        # threads that might force a flush with `flush_queue()`.
        self._conn_lck = threading.RLock()  # type: threading.RLock
        self._retry_upload = tenacity.Retrying(
            # Retry RETRY_ATTEMPTS times within the first half of the processing
            # interval, using a Fibonacci policy with jitter
            wait=tenacity.wait_random_exponential(
                multiplier=0.618 * self.interval / (1.618 ** self.RETRY_ATTEMPTS) / 2, exp_base=1.618
            ),
            stop=tenacity.stop_after_attempt(self.RETRY_ATTEMPTS),
            retry=tenacity.retry_if_exception_type((compat.httplib.HTTPException, OSError, IOError)),
        )
        self._log_error_payloads = asbool(os.environ.get("_DD_TRACE_WRITER_LOG_ERROR_PAYLOADS", False))
        self._reuse_connections = get_writer_reuse_connections() if reuse_connections is None else reuse_connections

    @property
    def _intake_endpoint(self):
        return "{}/{}".format(self.intake_url, self._endpoint)

    def recreate(self):
        # type: () -> AgentlessWriter
        return self.__class__(
            intake_url=self.intake_url,
            api_key=self._api_key,
            buffer_size=self._buffer_size,
            max_payload_size=self._max_payload_size,
            timeout=self._timeout,
        )

    def _reset_connection(self):
        # type: () -> None
        with self._conn_lck:
            if self._conn:
                self._conn.close()
                self._conn = None

    def _post(self, data, headers):
        # type: (bytes, Dict[str, str]) -> Response
        sw = StopWatch()
        sw.start()
        with self._conn_lck:
            if self._conn is None:
                log.debug("creating new intake connection to %s with timeout %d", self.intake_url, self._timeout)
                self._conn = get_connection(self.intake_url, self._timeout)
            try:
                self._conn.request("POST", self._endpoint, data, headers)
                resp = compat.get_connection_response(self._conn)
                t = sw.elapsed()
                if t >= self.interval:
                    log_level = logging.WARNING
                else:
                    log_level = logging.DEBUG
                log.log(log_level, "sent %s in %.5fs to %s", _human_size(len(data)), t, self._intake_endpoint)
            except Exception:
                # Always reset the connection when an exception occurs
                self._reset_connection()
                raise
            else:
                return Response.from_http_response(resp)
            finally:
                # Reset the connection if reusing connections is disabled.
                if not self._reuse_connections:
                    self._reset_connection()

    def _send_payload(self, payload, count):
        headers = self._headers.copy()
        headers["X-Datadog-Trace-Count"] = str(count)

        response = self._post(payload, headers)

        if response.status in [404, 415]:
            log.debug("calling endpoint '%s' but received %s; downgrading API", self._endpoint, response.status)
            try:
                payload = self._downgrade(payload, response)
            except ValueError:
                log.error(
                    "unsupported endpoint '%s': received response %s from Datadog Agent (%s)",
                    self._endpoint,
                    response.status,
                    self.intake_url,
                )
            else:
                if payload is not None:
                    self._send_payload(payload, count)
        elif response.status >= 400:
            msg = "failed to send traces to Datadog Agent at %s: HTTP error status %s, reason %s"
            log_args = (
                self._intake_endpoint,
                response.status,
                response.reason,
            )
            # Append the payload if requested
            if self._log_error_payloads:
                msg += ", payload %s"
                # If the payload is bytes then hex encode the value before logging
                if isinstance(payload, six.binary_type):
                    log_args += (binascii.hexlify(payload).decode(),)
                else:
                    log_args += (payload,)

            log.error(msg, *log_args)

    def write(self, spans=None):
        # type: (Optional[List[Span]]) -> None
        if spans is None:
            return

        try:
            self._encoder.put(spans)
        except BufferItemTooLarge as e:
            payload_size = e.args[0]
            log.warning(
                "trace (%db) larger than payload buffer item limit (%db), dropping",
                payload_size,
                self._encoder.max_item_size,
            )
        except BufferFull as e:
            payload_size = e.args[0]
            log.warning(
                "trace buffer (%s traces %db/%db) cannot fit trace of size %db, dropping",
                len(self._encoder),
                self._encoder.size,
                self._encoder.max_size,
                payload_size,
            )
        else:
            self.flush_queue()

    def flush_queue(self, raise_exc=False):
        # type: (bool) -> None
        try:
            n_traces = len(self._encoder)
            encoded = self._encoder.encode()
            if encoded is None:
                return
        except Exception:
            log.error("failed to encode trace with encoder %r", self._encoder, exc_info=True)
            return

        try:
            self._retry_upload(self._send_payload, encoded, n_traces)
        except tenacity.RetryError as e:
            if raise_exc:
                e.reraise()
            else:
                log.error("failed to send traces to Datadog Agent at %s", self._intake_endpoint, exc_info=True)

    def stop(self, timeout=None):
        # type: (Optional[float]) -> None
        return
