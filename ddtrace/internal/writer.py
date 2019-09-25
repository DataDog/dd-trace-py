# stdlib
import random
import os
import time

from .. import api
from .. import _worker
from ..internal.logger import get_logger
from ddtrace.vendor.six.moves.queue import Queue, Full, Empty

log = get_logger(__name__)


MAX_TRACES = 1000

DEFAULT_TIMEOUT = 5
LOG_ERR_INTERVAL = 60


class AgentWriter(_worker.PeriodicWorkerThread):

    QUEUE_PROCESSING_INTERVAL = 1

    def __init__(self, hostname='localhost', port=8126, uds_path=None,
                 shutdown_timeout=DEFAULT_TIMEOUT,
                 filters=None, priority_sampler=None):
        super(AgentWriter, self).__init__(interval=self.QUEUE_PROCESSING_INTERVAL,
                                          exit_timeout=shutdown_timeout,
                                          name=self.__class__.__name__)
        self._reset_queue()
        self._filters = filters
        self._priority_sampler = priority_sampler
        self._last_error_ts = 0
        self.api = api.API(hostname, port, uds_path=uds_path,
                           priority_sampling=priority_sampler is not None)
        self.start()

    def _reset_queue(self):
        self._pid = os.getpid()
        self._trace_queue = Q(maxsize=MAX_TRACES)

    def write(self, spans=None, services=None):
        # if this queue was created in a different process (i.e. this was
        # forked) reset everything so that we can safely work from it.
        pid = os.getpid()
        if self._pid != pid:
            log.debug('resetting queues. pids(old:%s new:%s)', self._pid, pid)
            self._reset_queue()

        if spans:
            self._trace_queue.put(spans)

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
            return

        if not traces:
            return

        # If we have data, let's try to send it.
        traces_responses = self.api.send_traces(traces)
        for response in traces_responses:
            if isinstance(response, Exception) or response.status >= 400:
                self._log_error_status(response)
            elif self._priority_sampler:
                result_traces_json = response.get_json()
                if result_traces_json and 'rate_by_service' in result_traces_json:
                    self._priority_sampler.set_sample_rate_by_service(result_traces_json['rate_by_service'])

    run_periodic = flush_queue
    on_shutdown = flush_queue

    def _log_error_status(self, response):
        log_level = log.debug
        now = time.time()
        if now > self._last_error_ts + LOG_ERR_INTERVAL:
            log_level = log.error
            self._last_error_ts = now
        prefix = 'Failed to send traces to Datadog Agent at %s: '
        if isinstance(response, api.Response):
            log_level(
                prefix + 'HTTP error status %s, reason %s, message %s',
                self.api,
                response.status,
                response.reason,
                response.msg,
            )
        else:
            log_level(
                prefix + '%s',
                self.api,
                response,
            )

    def _apply_filters(self, traces):
        """
        Here we make each trace go through the filters configured in the
        tracer. There is no need for a lock since the traces are owned by the
        AgentWriter at that point.
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

    This queue also exposes some statistics about its length, the number of items dropped, etc.
    """

    def __init__(self, maxsize=0):
        # Cannot use super() here because Queue in Python2 is old style class
        Queue.__init__(self, maxsize)
        # Number of item dropped (queue full)
        self.dropped = 0
        # Number of items enqueued
        self.enqueued = 0
        # Cumulative length of enqueued items
        self.enqueued_lengths = 0

    def put(self, item):
        try:
            # Cannot use super() here because Queue in Python2 is old style class
            Queue.put(self, item, block=False)
        except Full:
            # If the queue is full, replace a random item. We need to make sure
            # the queue is not emptied was emptied in the meantime, so we lock
            # check qsize value.
            with self.mutex:
                qsize = self._qsize()
                if qsize != 0:
                    idx = random.randrange(0, qsize)
                    self.queue[idx] = item
                    log.warning('Writer queue is full has more than %d traces, some traces will be lost', self.maxsize)
                    self.dropped += 1
                    self._update_stats(item)
                    return
            # The queue has been emptied, simply retry putting item
            return self.put(item)
        else:
            with self.mutex:
                self._update_stats(item)

    def _update_stats(self, item):
        # self.mutex needs to be locked to make sure we don't lose data when resetting
        self.enqueued += 1
        if hasattr(item, '__len__'):
            item_length = len(item)
        else:
            item_length = 1
        self.enqueued_lengths += item_length

    def reset_stats(self):
        """Reset the stats to 0.

        :return: The current value of dropped, enqueued and enqueued_lengths.
        """
        with self.mutex:
            dropped, enqueued, enqueued_lengths = self.dropped, self.enqueued, self.enqueued_lengths
            self.dropped, self.enqueued, self.enqueued_lengths = 0, 0, 0
        return dropped, enqueued, enqueued_lengths

    def _get(self):
        things = self.queue
        self._init(self.maxsize)
        return things
