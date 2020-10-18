# stdlib
import os
import sys
import threading
import time

from .. import api
from .. import compat
from .. import _worker
from ..internal.logger import get_logger
from ..sampler import BasePrioritySampler
from ..encoding import JSONEncoderV2
from ..payload import PayloadFull
from . import _queue

log = get_logger(__name__)


DEFAULT_TIMEOUT = 5
LOG_ERR_INTERVAL = 60


def _apply_filters(filters, traces):
    """
    Here we make each trace go through the filters configured in the
    tracer. There is no need for a lock since the traces are owned by the
    AgentWriter at that point.
    """
    if filters is not None:
        filtered_traces = []
        for trace in traces:
            for filtr in filters:
                trace = filtr.process_trace(trace)
                if trace is None:
                    break
            if trace is not None:
                filtered_traces.append(trace)
        return filtered_traces
    return traces


class LogWriter:
    def __init__(self, out=sys.stdout, filters=None, sampler=None, priority_sampler=None):
        self._filters = filters
        self._sampler = sampler
        self._priority_sampler = priority_sampler
        self.encoder = JSONEncoderV2()
        self.out = out

    def recreate(self):
        """Create a new instance of :class:`LogWriter` using the same settings from this instance

        :rtype: :class:`LogWriter`
        :returns: A new :class:`LogWriter` instance
        """
        writer = self.__class__(
            out=self.out, filters=self._filters, sampler=self._sampler, priority_sampler=self._priority_sampler
        )
        return writer

    def write(self, spans=None, services=None):
        # We immediately flush all spans
        if not spans:
            return

        # Before logging the traces, make them go through the
        # filters
        try:
            traces = _apply_filters(self._filters, [spans])
        except Exception:
            log.error("error while filtering traces", exc_info=True)
            return
        if len(traces) == 0:
            return
        encoded = self.encoder.encode_traces(traces)
        self.out.write(encoded + "\n")
        self.out.flush()


class AgentWriter(_worker.PeriodicWorkerThread):

    QUEUE_PROCESSING_INTERVAL = 1
    QUEUE_MAX_TRACES_DEFAULT = 1000

    def __init__(
        self,
        hostname="localhost",
        port=8126,
        uds_path=None,
        https=False,
        shutdown_timeout=DEFAULT_TIMEOUT,
        filters=None,
        sampler=None,
        priority_sampler=None,
    ):
        super(AgentWriter, self).__init__(
            interval=self.QUEUE_PROCESSING_INTERVAL, exit_timeout=shutdown_timeout, name=self.__class__.__name__
        )
        # DEV: provide a _temporary_ solution to allow users to specify a custom max
        maxsize = int(os.getenv("DD_TRACE_MAX_TPS", self.QUEUE_MAX_TRACES_DEFAULT))
        self._trace_queue = _queue.TraceQueue(maxsize=maxsize)
        self._filters = filters
        self._sampler = sampler
        self._priority_sampler = priority_sampler
        self._last_error_ts = 0
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
            filters=self._filters,
            priority_sampler=self._priority_sampler,
        )
        return writer

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
            return

        # Before sending the traces, make them go through the
        # filters
        try:
            traces = _apply_filters(self._filters, traces)
        except Exception:
            log.error("error while filtering traces", exc_info=True)
            return

        # If we have data, let's try to send it.
        traces_responses = self.api.send_traces(traces)
        for response in traces_responses:
            if not isinstance(response, PayloadFull):
                if isinstance(response, Exception) or response.status >= 400:
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

    def run_periodic(self):
        self.flush_queue()

    def on_shutdown(self):
        self.run_periodic()

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
