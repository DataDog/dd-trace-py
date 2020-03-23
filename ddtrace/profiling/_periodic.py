# -*- encoding: utf-8 -*-
import sys
import threading

PERIODIC_THREAD_IDS = set()


class PeriodicThread(threading.Thread):
    """Periodic thread.

    This class can be used to instantiate a worker thread that will run its `run_periodic` function every `interval`
    seconds.

    """

    def __init__(self, interval, target, name=None):
        """Create the periodic thread."""
        super(PeriodicThread, self).__init__(name=name)
        self._target = target
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
        while not self.quit.wait(self.interval):
            self._target()
        PERIODIC_THREAD_IDS.remove(self.ident)


class _GeventPeriodicThread(PeriodicThread):
    """Periodic thread.

    This class can be used to instantiate a worker thread that will run its `run_periodic` function every `interval`
    seconds.

    """

    # That's the value Python 2 uses in its `threading` module
    SLEEP_INTERVAL = 0.005

    def __init__(self, interval, target, name=None):
        """Create the periodic thread."""
        super(_GeventPeriodicThread, self).__init__(interval, target, name)
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

    def join(self):
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
        except Exception:
            # Exceptions might happen during interpreter shutdown.
            # We're mimicking what `threading.Thread` does in daemon mode, we ignore them.
            # See `threading.Thread._bootstrap` for details.
            if sys is not None:
                raise
        finally:
            self.has_quit = True
            del threading._active[self._tident]
            PERIODIC_THREAD_IDS.remove(self.ident)


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
