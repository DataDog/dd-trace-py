# -*- encoding: utf-8 -*-
import typing  # noqa:F401

from ddtrace.internal import forksafe
from ddtrace.internal import service
from ddtrace.internal.threads import PeriodicThread


class PeriodicService(service.Service):
    """A service that runs periodically.

    It automatically resumes its execution after a fork.
    """

    def __init__(self, interval: float = 0.0, no_wait_at_start: bool = False) -> None:
        super().__init__()
        self._interval = interval
        self._worker: typing.Optional[PeriodicThread] = None
        self._no_wait_at_start = no_wait_at_start

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
        self._worker = PeriodicThread(
            self.interval,
            target=self.periodic,
            name="%s:%s" % (self.__class__.__module__, self.__class__.__name__),
            on_shutdown=self.on_shutdown,
            no_wait_at_start=self._no_wait_at_start,
        )
        self._worker.start()

    def _stop_service(self, *args, **kwargs):
        # type: (typing.Any, typing.Any) -> None
        """Stop the periodic collector."""
        if self._worker:
            self._worker.stop()
        super(PeriodicService, self)._stop_service(*args, **kwargs)  # type: ignore[safe-super]

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

    def awake(self):
        # type: (...) -> None
        if self._worker:
            self._worker.awake()


class ForksafeAwakeablePeriodicService(AwakeablePeriodicService):
    """An awakeable periodic service that auto-resets on fork."""

    def reset(self) -> None:
        """Reset the service on fork.

        Implement this to clear the service state before restarting the thread
        in the child process.
        """
        pass

    def _restart(self) -> None:
        self.reset()

    def _start_service(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        super()._start_service(*args, **kwargs)
        forksafe.register(self._restart)

    def _stop_service(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        forksafe.unregister(self._restart)
        super()._stop_service(*args, **kwargs)
