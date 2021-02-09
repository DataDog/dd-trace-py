from collections import defaultdict
import logging
import sys
import threading

import ddtrace
from ..api import Response
from .. import compat
from .. import _worker
from ..compat import httplib
from ..sampler import BasePrioritySampler
from ..encoding import Encoder, JSONEncoderV2
from ..utils.time import StopWatch
from .logger import get_logger
from .runtime import container
from .buffer import BufferFull, BufferItemTooLarge, TraceBuffer
from .uds import UDSHTTPConnection

log = get_logger(__name__)

DEFAULT_TIMEOUT = 5
LOG_ERR_INTERVAL = 60


def _human_size(nbytes):
    """Return a human-readable size."""
    i = 0
    suffixes = ["B", "KB", "MB", "GB", "TB"]
    while nbytes >= 1000 and i < len(suffixes) - 1:
        nbytes /= 1000.0
        i += 1
    f = ("%.2f" % nbytes).rstrip("0").rstrip(".")
    return "%s%s" % (f, suffixes[i])


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
    """Writer to the Datadog Agent.

    The Datadog Agent supports (at the time of writing this) receiving trace
    payloads up to 50MB. A trace payload is just a list of traces and the agent
    expects a trace to be complete. That is, all spans with the same trace_id
    should be in the same trace.
    """

    def __init__(
        self,
        hostname="localhost",
        port=8126,
        uds_path=None,
        https=False,
        shutdown_timeout=DEFAULT_TIMEOUT,
        sampler=None,
        priority_sampler=None,
        processing_interval=1,
        # Match the payload size since there is no functionality
        # to flush dynamically.
        buffer_size=8 * 1000000,
        max_payload_size=8 * 1000000,
        timeout=2,
        dogstatsd=None,
        report_metrics=False,
    ):
        super(AgentWriter, self).__init__(
            interval=processing_interval, exit_timeout=shutdown_timeout, name=self.__class__.__name__
        )
        self._buffer_size = buffer_size
        self._max_payload_size = max_payload_size
        self._buffer = TraceBuffer(max_size=self._buffer_size, max_item_size=self._max_payload_size)
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
        self.dogstatsd = dogstatsd
        self._report_metrics = report_metrics
        self._metrics_reset()

    def _metrics_dist(self, name, count=1, tags=None):
        if self._report_metrics:
            self._metrics[name]["count"] += count
            if tags:
                self._metrics[name]["tags"].extend(tags)

    def _metrics_reset(self):
        if self._report_metrics:
            self._metrics = defaultdict(lambda: {"count": 0, "tags": []})

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

        with StopWatch() as sw:
            try:
                conn.request("PUT", self._endpoint, data, headers)
                resp = compat.get_connection_response(conn)
                return Response.from_http_response(resp)
            finally:
                conn.close()
                t = sw.elapsed()
                if t >= self.interval:
                    log_level = logging.WARNING
                else:
                    log_level = logging.DEBUG
                log.log(log_level, "sent %s in %.5fs", _human_size(len(data)), t)

    @property
    def agent_url(self):
        if self._uds_path:
            return "unix://" + self._uds_path
        if self._https:
            scheme = "https://"
        else:
            scheme = "http://"
        return "%s%s:%s" % (scheme, self._hostname, self._port)

    def _downgrade(self, payload, response):
        if self._endpoint == "/v0.4/traces":
            self._endpoint = "/v0.3/traces"
            return payload
        raise ValueError

    def _send_payload(self, payload, count):
        headers = self._headers.copy()
        headers["X-Datadog-Trace-Count"] = str(count)

        self._metrics_dist("http.requests")

        try:
            response = self._put(payload, headers)
        except (httplib.HTTPException, OSError, IOError):
            log.error("failed to send traces to Datadog Agent at %s", self.agent_url, exc_info=True)
            if self._report_metrics:
                self._metrics_dist("http.errors", tags=["type:err"])
                self._metrics_dist("http.dropped.bytes", len(payload))
        else:
            if response.status >= 400:
                self._metrics_dist("http.errors", tags=["type:%s" % response.status])
            else:
                self._metrics_dist("http.sent.bytes", len(payload))

            if response.status in [404, 415]:
                log.debug("calling endpoint '%s' but received %s; downgrading API", self._endpoint, response.status)
                try:
                    payload = self._downgrade(payload, response)
                except ValueError:
                    log.error(
                        "unsupported endpoint '%s': received response %s from Datadog Agent",
                        self._endpoint,
                        response.status,
                    )
                else:
                    return self._send_payload(payload, count)
            elif response.status >= 400:
                log.error(
                    "failed to send traces to Datadog Agent at %s: HTTP error status %s, reason %s",
                    self.agent_url,
                    response.status,
                    response.reason,
                )
                self._metrics_dist("http.dropped.bytes", len(payload))
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

    def write(self, spans):
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

        try:
            encoded = self._encoder.encode_trace(spans)
        except Exception:
            log.error("failed to encode trace with encoder %r", self._encoder, exc_info=True)
        else:
            try:
                self._buffer.put(encoded)
            except BufferItemTooLarge:
                log.warning(
                    "trace (%db) larger than payload limit (%db), dropping",
                    len(encoded),
                    self._max_payload_size,
                )
                self._metrics_dist("buffer.dropped.traces", 1, tags=["reason:t_too_big"])
                self._metrics_dist("buffer.dropped.bytes", len(encoded), tags=["reason:t_too_big"])
            except BufferFull:
                log.warning(
                    "trace buffer (%s traces %db/%db) cannot fit trace of size %db, dropping",
                    len(self._buffer),
                    self._buffer.size,
                    self._buffer.max_size,
                    len(encoded),
                )
                self._metrics_dist("buffer.dropped.traces", 1, tags=["reason:full"])
                self._metrics_dist("buffer.dropped.bytes", len(encoded), tags=["reason:full"])
            else:
                self._metrics_dist("buffer.accepted.traces", 1)
                self._metrics_dist("buffer.accepted.spans", len(spans))

    def flush_queue(self):
        enc_traces = self._buffer.get()
        if not enc_traces:
            return

        encoded = self._encoder.join_encoded(enc_traces)
        self._send_payload(encoded, len(enc_traces))

        if self._report_metrics:
            # Note that we cannot use the batching functionality of dogstatsd because
            # it's not thread-safe.
            # https://github.com/DataDog/datadogpy/issues/439
            # This really isn't ideal as now we're going to do a ton of socket calls.
            try:
                self.dogstatsd.increment("datadog.tracer.http.requests")
                self.dogstatsd.distribution("datadog.tracer.http.sent.bytes", len(encoded))
                self.dogstatsd.distribution("datadog.tracer.http.sent.traces", len(enc_traces))
                for name, metric in self._metrics.items():
                    self.dogstatsd.distribution("datadog.tracer.%s" % name, metric["count"], tags=metric["tags"])
            finally:
                self._metrics_reset()

    def run_periodic(self):
        self.flush_queue()

    on_shutdown = run_periodic
