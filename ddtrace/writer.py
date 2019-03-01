import atexit
import os
import random
import threading
import time

from . import api
from .compat import Queue
from .internal.logger import get_logger

log = get_logger(__name__)


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

    def __init__(self, api, trace_queue, service_queue=None, shutdown_timeout=DEFAULT_TIMEOUT,
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

    def _filter_trace(self, trace):
        if not trace:
            return None

        if not self._filters:
            return trace

        for f in self._filters:
            trace = f.process_trace(trace)
            if trace is None:
                log.debug('filter %r filtered out trace %r', f, trace)
                return None

        return trace

    def _target(self):
        next_flush = time.time() + 1
        payload = self.api.new_payload()

        while True:
            # DEV: Returns `None` or a Trace
            trace = self._trace_queue.get()

            # Filter the trace
            # DEV: We set `trace` to `None` on an error because we want to continue the loop
            #      since this loop needs to execute in order to flush a payload if enough time
            #      has passed since the last flush.
            try:
                # DEV: Returns the trace or `None` if it was filtered
                trace = self._filter_trace(trace)
            except Exception as err:
                log.error('Error raised while trying to filter trace %r: %s', trace, err)
                trace = None

            if trace:
                log.debug('Popped %s', _get_trace_info(trace))
                payload.add_trace(trace)

            now = time.time()
            # If the payload is large enough or enough time has passed, then flush
            if payload.full or now >= next_flush:
                if not payload.empty:
                    log.debug('Attempting to flush %r', payload)
                    self._flush(payload)

                    # Get a fresh payload
                    # DEV: Payloads are not reusable
                    payload = self.api.new_payload()
                next_flush = now + 1

            # Wait up until the next flush for another trace to be added
            self._trace_queue.wait(max(0, next_flush - now))

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


class Q(Queue, object):
    """
    Q is a custom subclass of ``queue.Queue`` that:

      - Adds `.close()` and `.closed()` to support closing the queue
      - Support non-blocking, and non-error popping (return ``None`` instead of an error)
      - Support non-blocking, and non-error pushing (overwrite a random item in the queue when max size is reached)
      - Add `.wait()` helper to supporting sleeping until a new item is available to pop
      - Modify `.join()` to wait until all items are removed from the queue instead of all tasks being marked as done
    """
    def __init__(self, maxsize=1000):
        """
        Constructor for Q

        :param maxsize: The max number of items to put into the queue
        :type maxsize: int
        """
        super(Q, self).__init__(maxsize=maxsize)
        self._closed = False

    def close(self):
        """
        Mark this queue as closed
        """
        with self.mutex:
            self._closed = True

    def closed(self):
        """
        Return whether this queue is closed or not

        :returns: Whether this queue is closed or not
        :rtype: bool
        """
        with self.mutex:
            return self._closed

    def put(self, item, block=True, timeout=None):
        """
        Push an item into the queue

        This method overrides the base to be non-blocking and never raise an error.

        If the queue is full we will overwrite a random item in the queue instead of appending.

        :param item: The item to push into the queue
        :type item: any
        :param block: This param is not used, it is provided for compatibility with ``queue.Queue``
        :param timeout: This param is not used, it is provided for compatibility with ``queue.Queue``
        """
        # Append `item` onto the queue
        # We should never block or raise an exception from this function
        with self.mutex:
            # If we are closed, then skip appending more items
            # DEV: Do not use `closed()` here since we will get contention on `self.mutex`
            if self._closed:
                return

            # If we're at the max size, then overwrite a random trace in the queue
            # DEV: We cannot use `self.full()` since we will have contention around `with self.mutex:`
            if 0 < self.maxsize <= self._qsize():
                idx = random.randrange(0, self._qsize())
                self.queue[idx] = item
            else:
                self._put(item)

            # Notify any active calls to `get` that we have items to pop
            self.not_empty.notify()

    def get(self, block=True, timeout=None):
        """
        Get an item from the queue

        This method overrides the base to always be non-blocking and to never raise an error.

        :param block: This param is not used, it is provided for compatibility with ``queue.Queue``
        :param timeout: This param is not used, it is provided for compatibility with ``queue.Queue``
        :returns: The next item from the queue or ``None`` if the queue is empty
        :rtype: None | any
        """
        # DEV: `with self.not_empty` will acquire a lock on `self.mutex`
        with self.not_empty:
            item = None
            # DEV: `qsize()` acquires a lock, `_qsize()` does not
            if self._qsize():
                item = self._get()
            # Notify anyone waiting on `self.not_full` that we have removed an item
            self.not_full.notify()
            return item

    def wait(self, seconds):
        """
        Wait up to ``seconds`` seconds for new items to be available

        :param seconds: Number of seconds to wait for
        :type seconds: float | int
        """
        # DEV: `qsize()` aquires a lock, `_qsize()` does not
        if not self.qsize():
            # DEV: `with self.not_empty` will acquire a lock on `self.mutex`
            with self.not_empty:
                self.not_empty.wait(seconds)

    def join(self):
        """
        Block until all items are removed from the queue
        """
        # Wait until after all items have been removed from the queue
        with self.not_full:
            # DEV: Do not use `self.empty()` or `self.qsize()` here since we already have a lock on `self.mutex`
            while self._qsize():
                # DEV: `not_full` is notified when we remove items from the queue
                self.not_full.wait(0.05)
