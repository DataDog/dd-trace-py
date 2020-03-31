# -*- encoding: utf-8 -*-
import sys
import threading

from ddtrace.profiling import _service
from ddtrace.vendor import attr


PERIODIC_THREAD_IDS = set()


class PeriodicThread(threading.Thread):
    """Periodic thread.

    This class can be used to instantiate a worker thread that will run its `run_periodic` function every `interval`
    seconds.

    """

    def __init__(self, interval, target, name=None, on_shutdown=None):
        """Create a periodic thread.

        :param interval: The interval in seconds to wait between execution of the periodic function.
        :param target: The periodic function to execute every interval.
        :param name: The name of the thread.
        :param on_shutdown: The function to call when the thread shuts down.
        """
        super(PeriodicThread, self).__init__(name=name)
        self._target = target
        self._on_shutdown = on_shutdown
        self.interval = interval
        self.quit = threading.Event()
        self.daemon = True

    def start(self):
        """Start the thread."""
        super(PeriodicThread, self).start()
        PERIODIC_THREAD_IDS.add(self.ident)

    def stop(self):
        """Stop the thread."""
        self.quit.set()

    def run(self):
        """Run the target function periodically."""
        try:
            while not self.quit.wait(self.interval):
                self._target()
            if self._on_shutdown is not None:
                self._on_shutdown()
        finally:
            PERIODIC_THREAD_IDS.remove(self.ident)


class _GeventPeriodicThread(PeriodicThread):
    """Periodic thread.

    This class can be used to instantiate a worker thread that will run its `run_periodic` function every `interval`
    seconds.

    """

    # That's the value Python 2 uses in its `threading` module
    SLEEP_INTERVAL = 0.005

    def __init__(self, interval, target, name=None, on_shutdown=None):
        """Create a periodic thread.

        :param interval: The interval in seconds to wait between execution of the periodic function.
        :param target: The periodic function to execute every interval.
        :param name: The name of the thread.
        :param on_shutdown: The function to call when the thread shuts down.
        """
        super(_GeventPeriodicThread, self).__init__(interval, target, name, on_shutdown)
        import gevent.monkey

        self._sleep = gevent.monkey.get_original("time", "sleep")
        self._tident = None

    @property
    def ident(self):
        return self._tident

    def start(self):
        """Start the thread."""
        from gevent._threading import start_new_thread

        self.quit = False
        self.has_quit = False
        threading._limbo[self] = self
        try:
            self._tident = start_new_thread(self.run, tuple())
        except Exception:
            del threading._limbo[self]
        PERIODIC_THREAD_IDS.add(self._tident)

    def join(self, timeout=None):
        # FIXME: handle the timeout argument
        while not self.has_quit:
            self._sleep(self.SLEEP_INTERVAL)

    def stop(self):
        """Stop the thread."""
        self.quit = True

    def run(self):
        """Run the target function periodically."""
        with threading._active_limbo_lock:
            threading._active[self._tident] = self
            del threading._limbo[self]
        try:
            while self.quit is False:
                self._target()
                slept = 0
                while self.quit is False and slept < self.interval:
                    self._sleep(self.SLEEP_INTERVAL)
                    slept += self.SLEEP_INTERVAL
            if self._on_shutdown is not None:
                self._on_shutdown()
        except Exception:
            # Exceptions might happen during interpreter shutdown.
            # We're mimicking what `threading.Thread` does in daemon mode, we ignore them.
            # See `threading.Thread._bootstrap` for details.
            if sys is not None:
                raise
        finally:
            try:
                self.has_quit = True
                del threading._active[self._tident]
                PERIODIC_THREAD_IDS.remove(self.ident)
            except Exception:
                # Exceptions might happen during interpreter shutdown.
                # We're mimicking what `threading.Thread` does in daemon mode, we ignore them.
                # See `threading.Thread._bootstrap` for details.
                if sys is not None:
                    raise


def PeriodicRealThread(*args, **kwargs):
    """Create a PeriodicRealThread based on the underlying thread implementation (native, gevent, etc).

    This is exactly like PeriodicThread, except that it runs on a *real* OS thread. Be aware that this might be tricky
    in e.g. the gevent case, where Lock object must not be shared with the MainThread (otherwise it'd dead lock).

    """
    if "gevent" in sys.modules:
        import gevent.monkey

        if gevent.monkey.is_module_patched("threading"):
            return _GeventPeriodicThread(*args, **kwargs)
    return PeriodicThread(*args, **kwargs)


@attr.s
class PeriodicService(_service.Service):
    """A service that runs periodically."""

    _interval = attr.ib()
    _worker = attr.ib(default=None, init=False, repr=False)

    _real_thread = False
    "Class variable to override if the service should run in a real OS thread."

    @property
    def interval(self):
        return self._interval

    @interval.setter
    def interval(self, value):
        self._interval = value
        # Update the interval of the PeriodicThread based on ours
        if self._worker:
            self._worker.interval = value

    def start(self):
        """Start collecting profiles."""
        super(PeriodicService, self).start()
        periodic_thread_class = PeriodicRealThread if self._real_thread else PeriodicThread
        self._worker = periodic_thread_class(
            self.interval,
            target=self.periodic,
            name="%s:%s" % (self.__class__.__module__, self.__class__.__name__),
            on_shutdown=self.on_shutdown,
        )
        self._worker.start()

    def join(self, timeout=None):
        if self._worker:
            self._worker.join(timeout)

    def stop(self):
        """Stop the periodic collector."""
        if self._worker:
            self._worker.stop()
        super(PeriodicService, self).stop()

    @staticmethod
    def on_shutdown():
        pass

    @staticmethod
    def periodic():
        pass
