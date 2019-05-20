# stdlib
import random
import os
import time

from . import api
from . import _worker
from .internal.logger import get_logger
from ddtrace.vendor.six.moves.queue import Queue, Full, Empty

log = get_logger(__name__)


MAX_TRACES = 1000

DEFAULT_TIMEOUT = 5
LOG_ERR_INTERVAL = 60


class AgentWriter(object):

    def __init__(self, hostname='localhost', port=8126, filters=None, priority_sampler=None):
        self._pid = None
        self._traces = None
        self._worker = None
        self._filters = filters
        self._priority_sampler = priority_sampler
        priority_sampling = priority_sampler is not None
        self.api = api.API(hostname, port, priority_sampling=priority_sampling)

    def write(self, spans=None, services=None):
        # if the worker needs to be reset, do it.
        self._reset_worker()

        if spans:
            self._traces.put(spans)

    def _reset_worker(self):
        # if this queue was created in a different process (i.e. this was
        # forked) reset everything so that we can safely work from it.
        pid = os.getpid()
        if self._pid != pid:
            log.debug('resetting queues. pids(old:%s new:%s)', self._pid, pid)
            self._traces = Q(maxsize=MAX_TRACES)
            self._worker = None
            self._pid = pid

        # ensure we have an active thread working on this queue
        if not self._worker or not self._worker.is_alive():
            self._worker = AsyncWorker(
                self.api,
                self._traces,
                filters=self._filters,
                priority_sampler=self._priority_sampler,
            )


class AsyncWorker(_worker.PeriodicWorkerThread):

    QUEUE_PROCESSING_INTERVAL = 1

    def __init__(self, api, trace_queue, service_queue=None, shutdown_timeout=DEFAULT_TIMEOUT,
                 filters=None, priority_sampler=None):
        super(AsyncWorker, self).__init__(interval=self.QUEUE_PROCESSING_INTERVAL,
                                          exit_timeout=shutdown_timeout,
                                          name=self.__class__.__name__)
        self._trace_queue = trace_queue
        self._filters = filters
        self._priority_sampler = priority_sampler
        self._last_error_ts = 0
        self.api = api
        self.start()

    def flush_queue(self):
        try:
            traces = self._trace_queue.get(block=False)
        except Empty:
            return

        # Before sending the traces, make them go through the
        # filters
        try:
            traces = self._apply_filters(traces)
        except Exception as err:
            log.error('error while filtering traces: {0}'.format(err))

        traces_response = None

        if traces:
            # If we have data, let's try to send it.
            try:
                traces_response = self.api.send_traces(traces)
            except Exception as err:
                log.error('cannot send spans to {1}:{2}: {0}'.format(
                    err, self.api.hostname, self.api.port))

        if self._priority_sampler and traces_response:
            result_traces_json = traces_response.get_json()
            if result_traces_json and 'rate_by_service' in result_traces_json:
                self._priority_sampler.set_sample_rate_by_service(result_traces_json['rate_by_service'])

        self._log_error_status(traces_response, 'traces')

    run_periodic = flush_queue
    on_shutdown = flush_queue

    def _log_error_status(self, response, response_name):
        if not isinstance(response, api.Response):
            return

        log_level = log.debug
        if response.status >= 400:
            now = time.time()
            if now > self._last_error_ts + LOG_ERR_INTERVAL:
                log_level = log.error
                self._last_error_ts = now
            log_level(
                'failed_to_send %s to Datadog Agent: HTTP error status %s, reason %s, message %s',
                response_name,
                response.status,
                response.reason,
                response.msg,
            )

    def _apply_filters(self, traces):
        """
        Here we make each trace go through the filters configured in the
        tracer. There is no need for a lock since the traces are owned by the
        AsyncWorker at that point.
        """
        if self._filters is not None:
            filtered_traces = []
            for trace in traces:
                for filtr in self._filters:
                    trace = filtr.process_trace(trace)
                    if trace is None:
                        break
                if trace is not None:
                    filtered_traces.append(trace)
            return filtered_traces
        return traces


class Q(Queue):
    """
    Q is a threadsafe queue that let's you pop everything at once and
    will randomly overwrite elements when it's over the max size.
    """
    def put(self, item):
        try:
            # Cannot use super() here because Queue in Python2 is old style class
            return Queue.put(self, item, block=False)
        except Full:
            # If the queue is full, replace a random item. We need to make sure
            # the queue is not emptied was emptied in the meantime, so we lock
            # check qsize value.
            with self.mutex:
                qsize = self._qsize()
                if qsize != 0:
                    idx = random.randrange(0, qsize)
                    self.queue[idx] = item
                    log.warn('Writer queue is full has more than %d traces, some traces will be lost', self.maxsize)
                    return
            # The queue has been emptied, simply retry putting item
            return self.put(item)

    def _get(self):
        things = self.queue
        self._init(self.maxsize)
        return things
