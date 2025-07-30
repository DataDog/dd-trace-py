import time as builtin_time
from types import TracebackType
from typing import Optional
from typing import Type  # noqa:F401

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class Time:
    """
    References to the standard Python time functions that won't be clobbered by `freezegun`.

    `freezegun`_ scans all loaded modules to check for imported functions from the `time` module, but it does not look
    inside classes or other objects, so these references are safe to use in the tracer.

    .. _freezegun: https://github.com/spulec/freezegun/blob/1.5.3/freezegun/api.py#L817
    """

    time = builtin_time.time
    time_ns = builtin_time.time_ns
    monotonic = builtin_time.monotonic
    monotonic_ns = builtin_time.monotonic_ns


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

    def __init__(self) -> None:
        self._started_at: Optional[float] = None
        self._stopped_at: Optional[float] = None

    def start(self):
        # type: () -> StopWatch
        """Starts the watch."""
        self._started_at = Time.monotonic()
        return self

    def elapsed(self) -> float:
        """Get how many seconds have elapsed.

        :return: Number of seconds elapsed
        :rtype: float
        """
        # NOTE: datetime.timedelta does not support nanoseconds, so keep a float here
        if self._started_at is None:
            raise RuntimeError("Can not get the elapsed time of a stopwatch if it has not been started/stopped")
        if self._stopped_at is None:
            now = Time.monotonic()
        else:
            now = self._stopped_at
        return now - self._started_at

    def __enter__(self):
        # type: () -> StopWatch
        """Starts the watch."""
        self.start()
        return self

    def __exit__(
        self, tp: Optional[Type[BaseException]], value: Optional[BaseException], traceback: Optional[TracebackType]
    ) -> None:
        """Stops the watch."""
        self.stop()

    def stop(self):
        # type: () -> StopWatch
        """Stops the watch."""
        if self._started_at is None:
            raise RuntimeError("Can not stop a stopwatch that has not been started")
        self._stopped_at = Time.monotonic()
        return self


class HourGlass(object):
    """An implementation of an hourglass."""

    def __init__(self, duration: float) -> None:
        t = Time.monotonic()

        self._duration = duration
        self._started_at = t - duration
        self._end_at = t

        self.trickling = self._trickled  # type: ignore[method-assign]

    def turn(self) -> None:
        """Turn the hourglass."""
        t = Time.monotonic()
        top_0 = self._end_at - self._started_at
        bottom = self._duration - top_0 + min(t - self._started_at, top_0)

        self._started_at = t
        self._end_at = t + bottom

        self.trickling = self._trickling  # type: ignore[method-assign]

    def trickling(self):
        # type: () -> bool
        """Check if sand is still trickling."""
        return False

    def _trickled(self):
        # type: () -> bool
        return False

    def _trickling(self):
        # type: () -> bool
        if Time.monotonic() < self._end_at:
            return True

        # No longer trickling, so we change state
        self.trickling = self._trickled  # type: ignore[method-assign]

        return False

    def __enter__(self):
        # type: () -> HourGlass
        self.turn()
        return self

    def __exit__(self, tp, value, traceback):
        pass

    def __lt__(self, other) -> bool:
        return self._end_at < other._end_at

    def __eq__(self, other) -> bool:
        return self._end_at == other._end_at
