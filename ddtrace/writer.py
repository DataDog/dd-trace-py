# stdlib
import atexit
import logging
import os
import random
import threading
import time

from . import api
from .compat import Queue
from .payload import Payload

log = logging.getLogger(__name__)


MAX_TRACES = 1000

DEFAULT_TIMEOUT = 5
LOG_ERR_INTERVAL = 60


def _get_trace_info(trace):
    if not trace and not len(trace):
        return '<{0!r}>'.format(trace)

    root = trace[0]
    return 'Trace(id={0!r}, name={1!r}, service={2!r}, resource={3!r}, spans={4!r})'.format(
        getattr(root, 'trace_id', None),
        getattr(root, 'name', None),
        getattr(root, 'service', None),
        getattr(root, 'resource', None),
        len(trace),
    )


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
            log.debug('Queuing %s', _get_trace_info(spans))
            self._traces.put(spans)

    def _reset_worker(self):
        # if this queue was created in a different process (i.e. this was
        # forked) reset everything so that we can safely work from it.
        pid = os.getpid()
        if self._pid != pid:
            log.debug('Resetting queues. pids(old:%s new:%s)', self._pid, pid)
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


class AsyncWorker(object):

    def __init__(self, api, trace_queue, shutdown_timeout=DEFAULT_TIMEOUT,
                 filters=None, priority_sampler=None):
        self._trace_queue = trace_queue
        self._lock = threading.Lock()
        self._thread = None
        self._shutdown_timeout = shutdown_timeout
        self._filters = filters
        self._priority_sampler = priority_sampler
        self._last_error_ts = 0
        self.api = api
        self.start()

    def is_alive(self):
        return self._thread.is_alive()

    def start(self):
        with self._lock:
            if not self._thread:
                log.debug("starting flush thread")
                self._thread = threading.Thread(target=self._target)
                self._thread.setDaemon(True)
                self._thread.start()
                atexit.register(self._on_shutdown)

    def stop(self):
        """
        Close the trace queue so that the worker will stop the execution
        """
        with self._lock:
            if self._thread and self.is_alive():
                self._trace_queue.close()

    def join(self, timeout=2):
        """
        Wait for the AsyncWorker execution. This call doesn't block the execution
        and it has a 2 seconds of timeout by default.
        """
        self._thread.join(timeout)

    def _on_shutdown(self):
        with self._lock:
            if not self._thread:
                return

            # wait for in-flight queues to get traced.
            time.sleep(0.1)
            self._trace_queue.close()

            size = self._trace_queue.qsize()
            if size:
                key = "ctrl-break" if os.name == 'nt' else 'ctrl-c'
                log.debug(
                    "Waiting %ss for traces to be sent. Hit %s to quit.",
                    self._shutdown_timeout,
                    key,
                )
                # Block until all items have been removed from the queue
                self._trace_queue.join()

    def _target(self):
        next_flush = time.time() + 1
        payload = Payload(filters=self._filters)

        while True:
            # DEV: Returns `None` or a Trace
            trace = self._trace_queue.get()

            if trace:
                log.debug('Popped %s', _get_trace_info(trace))
                payload.add_trace(trace)

            now = time.time()
            # If the payload is large enough or enough time has passed, then flush
            if payload.full or now >= next_flush:
                if not payload.empty:
                    log.debug('Attempting to flush %r', payload)
                    self._flush(payload)
                    payload.reset()
                next_flush = now + 1
            else:
                remaining = max(0, next_flush - now)
                self._trace_queue.wait(remaining)

            # Wait up until the next flush for another trace to be added
            self._trace_queue.wait(max(0, next_flush - time.time()))

            # no traces and the queue is closed. our work is done
            if self._trace_queue.closed() and self._trace_queue.qsize() == 0:
                if not payload.empty:
                    self._flush(payload)
                    payload.reset()
                log.debug('Trace queue closed and empty, exiting')
                return

    def _flush(self, payload):
        # Nothing to do, return early
        if payload.empty:
            log.debug('Payload empty, not flushing %r', payload)
            return

        log.debug('Flushing payload %r', payload)
        # If we have data, let's try to send it.
        try:
            response = self.api.send_traces(payload)

            # Update priority sampling rates from the agent
            if self._priority_sampler and response:
                response_json = response.get_json()
                if response_json and 'rate_by_service' in response_json:
                    self._priority_sampler.set_sample_rate_by_service(response_json['rate_by_service'])

            # Log any errors from the API response
            self._log_error_status(response, 'traces')
        except Exception as err:
            log.error('cannot send spans to {1}:{2}: {0}'.format(err, self.api.hostname, self.api.port))

    def _log_error_status(self, result, result_name):
        log_level = log.debug
        if result and getattr(result, 'status', None) >= 400:
            now = time.time()
            if now > self._last_error_ts + LOG_ERR_INTERVAL:
                log_level = log.error
                self._last_error_ts = now
            log_level('failed_to_send %s to Agent: HTTP error status %s, reason %s, message %s', result_name,
                      getattr(result, 'status', None), getattr(result, 'reason', None),
                      getattr(result, 'msg', None))


class Q(Queue, object):
    """
    Q is a threadsafe queue that let's you pop everything at once and
    will randomly overwrite elements when it's over the max size.
    """
    def __init__(self, maxsize=1000):
        super(Q, self).__init__(maxsize=maxsize)
        self._closed = False

    def close(self):
        with self.mutex:
            self._closed = True

    def closed(self):
        with self.mutex:
            return self._closed

    def put(self, item, block=True, timeout=None):
        # Append `item` onto the queue
        # We should never block or raise an exception from this function
        with self.mutex:
            # If we are closed, then skip appending more items
            # DEV: Do not use `closed()` here since we will get contention on `self.mutex`
            if self._closed:
                return

            # If we at the max size, then overwrite a random trace in the queue
            # DEV: We cannot use `self.full()` since we will have contention around `wtih self.mutex:`
            if 0 < self.maxsize <= self._qsize():
                idx = random.randrange(0, self._qsize())
                self.queue[idx] = item
            else:
                self._put(item)

            # Notify any active calls to `get` that we have items to pop
            self.not_empty.notify()

    def get(self, block=True, timeout=None):
        # DEV: `with self.not_empty` will acquire a lock on `self.mutex`
        with self.not_empty:
            item = None
            # DEV: `qsize()` aquires a lock, `_qsize()` does not
            if self._qsize():
                item = self._get()
            # Notify anyone waiting on `self.not_full` that we have removed an item
            self.not_full.notify()
            return item

    def wait(self, seconds):
        # DEV: `qsize()` aquires a lock, `_qsize()` does not
        if not self.qsize():
            # DEV: `with self.not_empty` will acquire a lock on `self.mutex`
            with self.not_empty:
                self.not_empty.wait(seconds)

    def join(self):
        # Wait until after all items have been removed from the queue
        with self.not_full:
            # DEV: Do not use `self.empty()` or `self.qsize()` here sine we already have a lock on `self.mutex`
            while self._qsize():
                # DEV: `not_full` is notified when we remove items from the queue
                self.not_full.wait(0.05)
