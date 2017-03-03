
# stdlib
import atexit
import logging
import threading
import random
import os
import time

from ddtrace import api


log = logging.getLogger(__name__)


MAX_TRACES = 1000
MAX_SERVICES = 1000

DEFAULT_TIMEOUT = 5


class AgentWriter(object):

    def __init__(self, hostname='localhost', port=8126):
        self._pid = None
        self._traces = None
        self._services = None
        self._worker = None
        self.api = api.API(hostname, port)

    def write(self, spans=None, services=None):
        # if the worker needs to be reset, do it.
        self._reset_worker()

        if spans:
            self._traces.add(spans)

        if services:
            self._services.add(services)

    def _reset_worker(self):
        # if this queue was created in a different process (i.e. this was
        # forked) reset everything so that we can safely work from it.
        pid = os.getpid()
        if self._pid != pid:
            log.debug("resetting queues. pids(old:%s new:%s)", self._pid, pid)
            self._traces = Q(max_size=MAX_TRACES)
            self._services = Q(max_size=MAX_SERVICES)
            self._worker = None
            self._pid = pid

        # ensure we have an active thread working on this queue
        if not self._worker or not self._worker.is_alive():
            self._worker = AsyncWorker(self.api, self._traces, self._services)


class AsyncWorker(object):

    def __init__(self, api, trace_queue, service_queue, shutdown_timeout=DEFAULT_TIMEOUT):
        self._trace_queue = trace_queue
        self._service_queue = service_queue
        self._lock = threading.Lock()
        self._thread = None
        self._shutdown_timeout = shutdown_timeout
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

            size = self._trace_queue.size()
            if size:
                key = "ctrl-break" if os.name == 'nt' else 'ctrl-c'
                log.debug("Waiting %ss for traces to be sent. Hit %s to quit.",
                        self._shutdown_timeout, key)
                timeout = time.time() + self._shutdown_timeout
                while time.time() < timeout and self._trace_queue.size():
                    # FIXME[matt] replace with a queue join
                    time.sleep(0.05)

    def _target(self):
        while True:
            traces = self._trace_queue.pop()
            if traces:
                # If we have data, let's try to send it.
                try:
                    self.api.send_traces(traces)
                except Exception as err:
                    log.error("cannot send spans: {0}".format(err))

            services = self._service_queue.pop()
            if services:
                try:
                    self.api.send_services(services)
                except Exception as err:
                    log.error("cannot send services: {0}".format(err))

            elif self._trace_queue.closed():
                # no traces and the queue is closed. our work is done.
                return

            time.sleep(1) # replace with a blocking pop.


class Q(object):
    """
    Q is a threadsafe queue that let's you pop everything at once and
    will randomly overwrite elements when it's over the max size.
    """
    def __init__(self, max_size=1000):
        self._things = []
        self._lock = threading.Lock()
        self._max_size = max_size
        self._closed = False

    def size(self):
        with self._lock:
            return len(self._things)

    def close(self):
        with self._lock:
            self._closed = True

    def closed(self):
        with self._lock:
            return self._closed

    def add(self, thing):
        with self._lock:
            if self._closed:
                return False

            if len(self._things) < self._max_size or self._max_size <= 0:
                self._things.append(thing)
                return True
            else:
                idx = random.randrange(0, len(self._things))
                self._things[idx] = thing

    def pop(self):
        with self._lock:
            if not self._things:
                return None
            things = self._things
            self._things = []
            return things
