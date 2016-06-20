"""
Report spans to the Agent API.

The asnyc HTTPReporter is taken from raven.transport.threaded.
"""

import atexit
import httplib
import logging
import threading
from time import sleep, time
import ujson as json
import os

from compat import Queue


DEFAULT_TIMEOUT = 10


log = logging.getLogger(__name__)


class AgentReporter(object):
    SERVICES_FLUSH_INTERVAL = 60

    def __init__(self, disabled=False, config=None):
        self.disabled = disabled
        self.config = config
        self.transport = ThreadedHTTPTransport()
        self.last_services_flush = 0

    def report(self, spans, services):
        if self.disabled:
            log.debug("Trace reporter disabled, skip flushing")
            return

        if spans:
            self.send_spans(spans)
        if services:
            now = time()
            if now - self.last_services_flush > self.SERVICES_FLUSH_INTERVAL:
                self.send_services(services)
                self.last_services_flush = now

    def send_spans(self, spans):
        log.debug("Reporting %d spans", len(spans))
        data = json.dumps([span.to_dict() for span in spans])
        headers = {}
        self.transport.send("PUT", "/spans", data, headers)

    def send_services(self, services):
        log.debug("Reporting %d services", len(services))
        data = json.dumps(services)
        headers = {}
        self.transport.send("PUT", "/services", data, headers)


class ThreadedHTTPTransport(object):

    # Async worker, to be defined at first run
    _worker = None

    def send(self, method, endpoint, data, headers):
        return self.async_send(
            method, endpoint, data, headers,
            self.success_callback, self.failure_callback
        )

    def async_send(self, method, endpoint, data, headers, success_cb, failure_cb):
        self.get_worker().queue(
            self.send_sync, method, endpoint, data, headers, success_cb, failure_cb)

    def send_sync(self, method, endpoint, data, headers, success_cb, failure_cb):
        try:
            conn = httplib.HTTPConnection('localhost', 7777)
            conn.request(method, endpoint, data, headers)
        except Exception as e:
            failure_cb(e)
        else:
            success_cb()

    def get_worker(self):
        if self._worker is None or not self._worker.is_alive():
            self._worker = AsyncWorker()
        return self._worker

    def failure_callback(self, error):
        log.error("Failed to report a trace, %s", error)

    def success_callback(self):
        pass


class AsyncWorker(object):
    _terminator = object()

    def __init__(self, shutdown_timeout=DEFAULT_TIMEOUT):
        self._queue = Queue(-1)
        self._lock = threading.Lock()
        self._thread = None
        self.options = {
            'shutdown_timeout': shutdown_timeout,
        }
        self.start()

    def is_alive(self):
        return self._thread.is_alive()

    def main_thread_terminated(self):
        self._lock.acquire()
        try:
            if not self._thread:
                # thread not started or already stopped - nothing to do
                return

            # wake the processing thread up
            self._queue.put_nowait(self._terminator)

            timeout = self.options['shutdown_timeout']

            # wait briefly, initially
            initial_timeout = 0.1
            if timeout < initial_timeout:
                initial_timeout = timeout

            if not self._timed_queue_join(initial_timeout):
                # if that didn't work, wait a bit longer
                # NB that size is an approximation, because other threads may
                # add or remove items
                size = self._queue.qsize()

                print("Sentry is attempting to send %i pending error messages"
                      % size)
                print("Waiting up to %s seconds" % timeout)

                if os.name == 'nt':
                    print("Press Ctrl-Break to quit")
                else:
                    print("Press Ctrl-C to quit")

                self._timed_queue_join(timeout - initial_timeout)

            self._thread = None

        finally:
            self._lock.release()

    def _timed_queue_join(self, timeout):
        """
        implementation of Queue.join which takes a 'timeout' argument

        returns true on success, false on timeout
        """
        deadline = time() + timeout
        queue = self._queue

        queue.all_tasks_done.acquire()
        try:
            while queue.unfinished_tasks:
                delay = deadline - time()
                if delay <= 0:
                    # timed out
                    return False

                queue.all_tasks_done.wait(timeout=delay)

            return True

        finally:
            queue.all_tasks_done.release()

    def start(self):
        """
        Starts the task thread.
        """
        self._lock.acquire()
        try:
            if not self._thread:
                self._thread = threading.Thread(target=self._target)
                self._thread.setDaemon(True)
                self._thread.start()
        finally:
            self._lock.release()
            atexit.register(self.main_thread_terminated)

    def stop(self, timeout=None):
        """
        Stops the task thread. Synchronous!
        """
        self._lock.acquire()
        try:
            if self._thread:
                self._queue.put_nowait(self._terminator)
                self._thread.join(timeout=timeout)
                self._thread = None
        finally:
            self._lock.release()

    def queue(self, callback, *args, **kwargs):
        self._queue.put_nowait((callback, args, kwargs))

    def _target(self):
        while True:
            record = self._queue.get()
            try:
                if record is self._terminator:
                    break
                callback, args, kwargs = record
                try:
                    callback(*args, **kwargs)
                except Exception:
                    log.error('Failed processing job', exc_info=True)
            finally:
                self._queue.task_done()

            sleep(0)
