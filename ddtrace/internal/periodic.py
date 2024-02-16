# -*- encoding: utf-8 -*-
import threading
import typing  # noqa:F401

import attr

from ddtrace.internal import service

from . import forksafe


class PeriodicThread(threading.Thread):
    """Periodic thread.

    This class can be used to instantiate a worker thread that will run its `run_periodic` function every `interval`
    seconds.

    """

    _ddtrace_profiling_ignore = True

    def __init__(
        self,
        interval,  # type: float
        target,  # type: typing.Callable[[], typing.Any]
        name=None,  # type: typing.Optional[str]
        on_shutdown=None,  # type: typing.Optional[typing.Callable[[], typing.Any]]
    ):
        # type: (...) -> None
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
        self.quit = forksafe.Event()
        self.daemon = True

    def stop(self):
        """Stop the thread."""
        # NOTE: make sure the thread is alive before using self.quit:
        # 1. self.quit is Lock-based
        # 2. if we're a child trying to stop a Thread,
        #    the Lock might have been locked in a parent process while forking so that'd block forever
        if self.is_alive():
            self.quit.set()

    def run(self):
        """Run the target function periodically."""
        while not self.quit.wait(self.interval):
            self._target()
        if self._on_shutdown is not None:
            self._on_shutdown()


class AwakeablePeriodicThread(PeriodicThread):
    """Periodic thread that can be awakened on demand.

    This class can be used to instantiate a worker thread that will run its
    `run_periodic` function every `interval` seconds, or upon request.
    """

    def __init__(
        self,
        interval,  # type: float
        target,  # type: typing.Callable[[], typing.Any]
        name=None,  # type: typing.Optional[str]
        on_shutdown=None,  # type: typing.Optional[typing.Callable[[], typing.Any]]
    ):
        # type: (...) -> None
        """Create a periodic thread that can be awakened on demand."""
        super(AwakeablePeriodicThread, self).__init__(interval, target, name, on_shutdown)
        self.request = forksafe.Event()
        self.served = forksafe.Event()
        self.awake_lock = forksafe.Lock()

    def awake(self):
        """Awake the thread."""
        with self.awake_lock:
            self.served.clear()
            self.request.set()
            self.served.wait()

    def stop(self):
        super().stop()
        self.request.set()

    def run(self):
        """Run the target function periodically or on demand."""
        while not self.quit.is_set():
            self._target()

            if self.request.wait(self.interval):
                if self.quit.is_set():
                    break
                self.request.clear()
                self.served.set()

        if self._on_shutdown is not None:
            self._on_shutdown()


@attr.s(eq=False)
class PeriodicService(service.Service):
    """A service that runs periodically."""

    _interval = attr.ib(type=float)
    _worker = attr.ib(default=None, init=False, repr=False)

    __thread_class__ = PeriodicThread

    @property
    def interval(self):
        # type: (...) -> float
        return self._interval

    @interval.setter
    def interval(
        self,
        value,  # type: float
    ):
        # type: (...) -> None
        self._interval = value
        # Update the interval of the PeriodicThread based on ours
        if self._worker:
            self._worker.interval = value

    def _start_service(self, *args, **kwargs):
        # type: (typing.Any, typing.Any) -> None
        """Start the periodic service."""
        self._worker = self.__thread_class__(
            self.interval,
            target=self.periodic,
            name="%s:%s" % (self.__class__.__module__, self.__class__.__name__),
            on_shutdown=self.on_shutdown,
        )
        self._worker.start()

    def _stop_service(self, *args, **kwargs):
        # type: (typing.Any, typing.Any) -> None
        """Stop the periodic collector."""
        self._worker.stop()
        super(PeriodicService, self)._stop_service(*args, **kwargs)

    def join(
        self,
        timeout=None,  # type: typing.Optional[float]
    ):
        # type: (...) -> None
        if self._worker:
            self._worker.join(timeout)

    @staticmethod
    def on_shutdown():
        pass

    def periodic(self):
        # type: (...) -> None
        pass


class AwakeablePeriodicService(PeriodicService):
    """A service that runs periodically but that can also be awakened on demand."""

    __thread_class__ = AwakeablePeriodicThread

    def awake(self):
        # type: (...) -> None
        self._worker.awake()
