from types import TracebackType
from typing import Optional
from typing import Type

from ddtrace.internal import compat


class StopWatch(object):
    """A simple timer/stopwatch helper class.

    Not thread-safe (when a single watch is mutated by multiple threads at
    the same time). Thread-safe when used by a single thread (not shared) or
    when operations are performed in a thread-safe manner on these objects by
    wrapping those operations with locks.

    It will use the `monotonic`_ pypi library to find an appropriate
    monotonically increasing time providing function (which typically varies
    depending on operating system and Python version).

    .. _monotonic: https://pypi.python.org/pypi/monotonic/
    """

    def __init__(self):
        # type: () -> None
        self._started_at = None  # type: Optional[float]
        self._stopped_at = None  # type: Optional[float]

    def start(self):
        # type: () -> StopWatch
        """Starts the watch."""
        self._started_at = compat.monotonic()
        return self

    def elapsed(self):
        # type: () -> float
        """Get how many seconds have elapsed.

        :return: Number of seconds elapsed
        :rtype: float
        """
        # NOTE: datetime.timedelta does not support nanoseconds, so keep a float here
        if self._started_at is None:
            raise RuntimeError("Can not get the elapsed time of a stopwatch" " if it has not been started/stopped")
        if self._stopped_at is None:
            now = compat.monotonic()
        else:
            now = self._stopped_at
        return now - self._started_at

    def __enter__(self):
        # type: () -> StopWatch
        """Starts the watch."""
        self.start()
        return self

    def __exit__(self, tp, value, traceback):
        # type: (Optional[Type[BaseException]], Optional[BaseException], Optional[TracebackType]) -> None
        """Stops the watch."""
        self.stop()

    def stop(self):
        # type: () -> StopWatch
        """Stops the watch."""
        if self._started_at is None:
            raise RuntimeError("Can not stop a stopwatch that has not been" " started")
        self._stopped_at = compat.monotonic()
        return self
