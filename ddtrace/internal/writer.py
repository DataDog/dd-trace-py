import sys
import threading

import ddtrace
from ..api import Response, UDSHTTPConnection
from .. import compat
from .. import _worker
from ..compat import httplib
from ..sampler import BasePrioritySampler
from ..encoding import Encoder, JSONEncoder, JSONEncoderV2
from ..utils.time import StopWatch
from .logger import get_logger
from .runtime import container
from . import _buffer

log = get_logger(__name__)

DEFAULT_TIMEOUT = 5
LOG_ERR_INTERVAL = 60


class LogWriter:
    def __init__(self, out=sys.stdout, sampler=None, priority_sampler=None):
        self._sampler = sampler
        self._priority_sampler = priority_sampler
        self.encoder = JSONEncoderV2()
        self.out = out

    def recreate(self):
        """Create a new instance of :class:`LogWriter` using the same settings from this instance

        :rtype: :class:`LogWriter`
        :returns: A new :class:`LogWriter` instance
        """
        writer = self.__class__(out=self.out, sampler=self._sampler, priority_sampler=self._priority_sampler)
        return writer

    def write(self, spans=None, services=None):
        if not spans:
            return

        encoded = self.encoder.encode_traces([spans])
        self.out.write(encoded + "\n")
        self.out.flush()


class AgentWriter(_worker.PeriodicWorkerThread):
    """Writer to the Datadog Agent."""

    QUEUE_PROCESSING_INTERVAL = 1

    def __init__(
        self,
        hostname="localhost",
        port=8126,
        uds_path=None,
        https=False,
        shutdown_timeout=DEFAULT_TIMEOUT,
        sampler=None,
        priority_sampler=None,
        buffer_size=20 * 1000000,
        max_payload_size=5 * 1000000,
        timeout=2,
    ):
        super(AgentWriter, self).__init__(
            interval=self.QUEUE_PROCESSING_INTERVAL, exit_timeout=shutdown_timeout, name=self.__class__.__name__
        )
        assert max_payload_size < buffer_size
        self._buffer_size = buffer_size
        self._max_payload_size = max_payload_size
        self._buffer = _buffer.TraceBuffer(maxsize=self._buffer_size)
        self._sampler = sampler
        self._priority_sampler = priority_sampler
        self._hostname = hostname
        self._port = int(port)
        self._uds_path = uds_path
        self._https = https
        self._headers = {
            "Datadog-Meta-Lang": "python",
            "Datadog-Meta-Lang-Version": compat.PYTHON_VERSION,
            "Datadog-Meta-Lang-Interpreter": compat.PYTHON_INTERPRETER,
            "Datadog-Meta-Tracer-Version": ddtrace.__version__,
        }
        self._timeout = timeout

        if priority_sampler is not None:
            self._endpoint = "/v0.4/traces"
        else:
            self._endpoint = "/v0.3/traces"

        self._container_info = container.get_container_info()
        if self._container_info and self._container_info.container_id:
            self._headers.update(
                {
                    "Datadog-Container-Id": self._container_info.container_id,
                }
            )

        self._encoder = Encoder()
        self._headers.update({"Content-Type": self._encoder.content_type})

        self._started = False
        self._started_lock = threading.Lock()

    def recreate(self):
        writer = self.__class__(
            hostname=self._hostname,
            port=self._port,
            uds_path=self._uds_path,
            https=self._https,
            shutdown_timeout=self.exit_timeout,
            priority_sampler=self._priority_sampler,
        )
        writer._encoder = self._encoder
        writer._headers = self._headers
        writer._endpoint = self._endpoint
        return writer

    def _put(self, data, headers):
        if self._uds_path is None:
            if self._https:
                conn = httplib.HTTPSConnection(self._hostname, self._port, timeout=self._timeout)
            else:
                conn = httplib.HTTPConnection(self._hostname, self._port, timeout=self._timeout)
        else:
            conn = UDSHTTPConnection(self._uds_path, self._https, self._hostname, self._port, timeout=self._timeout)

        try:
            conn.request("PUT", self._endpoint, data, headers)
            resp = compat.get_connection_response(conn)
            return Response.from_http_response(resp)
        finally:
            conn.close()

    @property
    def agent_url(self):
        if self._uds_path:
            return "unix://" + self._uds_path
        if self._https:
            scheme = "https://"
        else:
            scheme = "http://"
        return "%s%s:%s" % (scheme, self._hostname, self._port)

    def _downgrade(self, payload):
        if self._endpoint == "/v0.4/traces":
            self._endpoint = "/v0.3/traces"
            return payload
        elif self._endpoint == "/v0.3/traces":
            self._endpoint = "/v0.2/traces"
            self._encoder = JSONEncoder()
            # FIXME: have to maintain a reference to the original traces
            # in order to reencode :|
            # Also have to reencode everything in self._buffer.
            self._buffer.clear()
            return payload
        else:
            log.error("could not downgrade tracing connection %s", self._endpoint)

    def _send_payload(self, payload, count):
        headers = self._headers.copy()
        headers["X-Datadog-Trace-Count"] = str(count)

        with StopWatch() as sw:
            try:
                response = self._put(payload, headers)
            except (httplib.HTTPException, OSError, IOError):
                log.error("Failed to send traces to Datadog Agent at %s", self.agent_url, exc_info=True)
            else:
                if response.status in [404, 415]:
                    log.debug("calling endpoint '%s' but received %s; downgrading API", self._endpoint, response.status)
                    payload = self._downgrade(payload)
                    if payload:
                        return self._send_payload(payload, count)
                elif response.status >= 400:
                    log.error(
                        "Failed to send traces to Datadog Agent at %s: HTTP error status %s, reason %s, message %s",
                        self.agent_url,
                        response.status,
                        response.reason,
                        response.msg,
                    )
                elif self._priority_sampler or isinstance(self._sampler, BasePrioritySampler):
                    result_traces_json = response.get_json()
                    if result_traces_json and "rate_by_service" in result_traces_json:
                        if self._priority_sampler:
                            self._priority_sampler.update_rate_by_service_sample_rates(
                                result_traces_json["rate_by_service"],
                            )
                        if isinstance(self._sampler, BasePrioritySampler):
                            self._sampler.update_rate_by_service_sample_rates(
                                result_traces_json["rate_by_service"],
                            )

        log.debug("reported %d traces in %.5fs", count, sw.elapsed())

    def write(self, spans=None, services=None):
        # Start the AgentWriter on first write.
        # Starting it earlier might be an issue with gevent, see:
        # https://github.com/DataDog/dd-trace-py/issues/1192
        if self._started is False:
            with self._started_lock:
                if self._started is False:
                    self.start()
                    self._started = True
        if not spans:
            return

        encoded = self._encoder.encode_trace(spans)
        if len(encoded) > self._max_payload_size:
            # Trace is bigger than the payload limit, nothing we can do.
            log.warning("trace larger than maximum payload limit, dropping")
            return

        self._buffer.put(encoded)

    def flush_queue(self):
        enc_traces = self._buffer.get(self._max_payload_size)
        if not enc_traces:
            return

        encoded = self._encoder.join_encoded(enc_traces)
        self._send_payload(encoded, len(enc_traces))

    def run_periodic(self):
        self.flush_queue()

    def on_shutdown(self):
        self.run_periodic()
