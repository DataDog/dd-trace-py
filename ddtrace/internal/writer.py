# stdlib
import itertools
import os
import sys
import threading
import time

from .. import api
from .. import compat
from .. import _worker
from ..internal.logger import get_logger
from ..sampler import BasePrioritySampler
from ..settings import config
from ..encoding import JSONEncoderV2
from ..payload import PayloadFull
from ..constants import KEEP_SPANS_RATE_KEY
from . import _queue
from . import sma

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
        # We immediately flush all spans
        if not spans:
            return

        encoded = self.encoder.encode_traces([spans])
        self.out.write(encoded + "\n")
        self.out.flush()


class AgentWriter(_worker.PeriodicWorkerThread):

    QUEUE_PROCESSING_INTERVAL = 1
    QUEUE_MAX_TRACES_DEFAULT = 1000

    DROP_SPANS_RATE_SMA_WINDOW = 10

    def __init__(
        self,
        hostname="localhost",
        port=8126,
        uds_path=None,
        https=False,
        shutdown_timeout=DEFAULT_TIMEOUT,
        sampler=None,
        priority_sampler=None,
        dogstatsd=None,
    ):
        super(AgentWriter, self).__init__(
            interval=self.QUEUE_PROCESSING_INTERVAL, exit_timeout=shutdown_timeout, name=self.__class__.__name__
        )
        # DEV: provide a _temporary_ solution to allow users to specify a custom max
        maxsize = int(os.getenv("DD_TRACE_MAX_TPS", self.QUEUE_MAX_TRACES_DEFAULT))
        self._trace_queue = _queue.TraceQueue(maxsize=maxsize)
        self._sampler = sampler
        self._priority_sampler = priority_sampler
        self._last_error_ts = 0
        self._drop_sma = sma.SimpleMovingAverage(self.DROP_SPANS_RATE_SMA_WINDOW)
        self.dogstatsd = dogstatsd
        self.api = api.API(
            hostname, port, uds_path=uds_path, https=https, priority_sampling=priority_sampler is not None
        )
        if hasattr(time, "thread_time"):
            self._last_thread_time = time.thread_time()
        self._started = False
        self._started_lock = threading.Lock()

    def recreate(self):
        """Create a new instance of :class:`AgentWriter` using the same settings from this instance

        :rtype: :class:`AgentWriter`
        :returns: A new :class:`AgentWriter` instance
        """
        writer = self.__class__(
            hostname=self.api.hostname,
            port=self.api.port,
            uds_path=self.api.uds_path,
            https=self.api.https,
            shutdown_timeout=self.exit_timeout,
            priority_sampler=self._priority_sampler,
            dogstatsd=self.dogstatsd,
        )
        return writer

    @property
    def _send_stats(self):
        """Determine if we're sending stats or not."""
        return bool(config.health_metrics_enabled and self.dogstatsd)

    def write(self, spans=None, services=None):
        # Start the AgentWriter on first write.
        # Starting it earlier might be an issue with gevent, see:
        # https://github.com/DataDog/dd-trace-py/issues/1192
        if self._started is False:
            with self._started_lock:
                if self._started is False:
                    self.start()
                    self._started = True
        if spans:
            self._trace_queue.put(spans)

    def flush_queue(self):
        traces = self._trace_queue.get()

        if not traces:
            return 0

        if self._send_stats:
            traces_queue_length = len(traces)
            traces_queue_spans = sum(map(len, traces))

        self._set_keep_rate(traces)

        dropped_root_spans = 0

        # If we have data, let's try to send it.
        traces_responses = self.api.send_traces(traces)
        for response in traces_responses:
            if not isinstance(response, PayloadFull):
                if isinstance(response, Exception) or response.status >= 400:
                    dropped_root_spans += getattr(response, "traces", 0)
                    self._log_error_status(response)
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
            else:
                dropped_root_spans += getattr(response, "traces", 0)

        # Dump statistics
        # NOTE: Do not use the buffering of dogstatsd as it's not thread-safe
        # https://github.com/DataDog/datadogpy/issues/439
        if self._send_stats:
            # Statistics about the queue length, size and number of spans
            self.dogstatsd.increment("datadog.tracer.flushes")
            self._histogram_with_total("datadog.tracer.flush.traces", traces_queue_length)
            self._histogram_with_total("datadog.tracer.flush.spans", traces_queue_spans)

            # Statistics about API
            self._histogram_with_total("datadog.tracer.api.requests", len(traces_responses))

            self._histogram_with_total(
                "datadog.tracer.api.errors",
                len(list(t for t in traces_responses if isinstance(t, Exception) and not isinstance(t, PayloadFull))),
            )

            self._histogram_with_total(
                "datadog.tracer.api.traces_payloadfull",
                len(list(t for t in traces_responses if isinstance(t, PayloadFull))),
            )

            for status, grouped_responses in itertools.groupby(
                sorted((t for t in traces_responses if not isinstance(t, Exception)), key=lambda r: r.status),
                key=lambda r: r.status,
            ):
                self._histogram_with_total(
                    "datadog.tracer.api.responses", len(list(grouped_responses)), tags=["status:%d" % status]
                )

            # Statistics about the writer thread
            if hasattr(time, "thread_time"):
                new_thread_time = time.thread_time()
                diff = new_thread_time - self._last_thread_time
                self._last_thread_time = new_thread_time
                self.dogstatsd.histogram("datadog.tracer.writer.cpu_time", diff)

        return dropped_root_spans

    def _histogram_with_total(self, name, value, tags=None):
        """Helper to add metric as a histogram and with a `.total` counter"""
        self.dogstatsd.histogram(name, value, tags=tags)
        self.dogstatsd.increment("%s.total" % (name,), value, tags=tags)

    def run_periodic(self):
        if self._send_stats:
            self.dogstatsd.gauge("datadog.tracer.heartbeat", 1)

        try:
            dropped_root_spans = self.flush_queue()
        except Exception:
            dropped_root_spans = 0
        finally:
            dropped, enqueued, enqueued_lengths = self._trace_queue.pop_stats()

            self._drop_sma.set(dropped + dropped_root_spans, enqueued)

            if not self._send_stats:
                return

            # Statistics about the rate at which spans are inserted in the queue
            self.dogstatsd.gauge("datadog.tracer.queue.max_length", self._trace_queue.maxsize)
            self.dogstatsd.increment("datadog.tracer.queue.dropped.traces", dropped)
            self.dogstatsd.increment("datadog.tracer.queue.enqueued.traces", enqueued)
            self.dogstatsd.increment("datadog.tracer.queue.enqueued.spans", enqueued_lengths)

    def on_shutdown(self):
        try:
            self.run_periodic()
        finally:
            if not self._send_stats:
                return

            self.dogstatsd.increment("datadog.tracer.shutdown")

    def _set_keep_rate(self, traces):
        keep_rate = 1.0 - self._drop_sma.get()

        for trace in (t for t in traces if t):
            trace[0].set_metric(KEEP_SPANS_RATE_KEY, keep_rate)

    def _log_error_status(self, response):
        log_level = log.debug
        now = compat.monotonic()
        if now > self._last_error_ts + LOG_ERR_INTERVAL:
            log_level = log.error
            self._last_error_ts = now
        prefix = "Failed to send traces to Datadog Agent at %s: "
        if isinstance(response, api.Response):
            log_level(
                prefix + "HTTP error status %s, reason %s, message %s",
                self.api,
                response.status,
                response.reason,
                response.msg,
            )
        else:
            log_level(
                prefix + "%s",
                self.api,
                response,
            )
