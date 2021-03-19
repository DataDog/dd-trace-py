import threading
from typing import Optional

from .internal.logger import get_logger


_LOG = get_logger(__name__)


class PeriodicWorkerThread(object):
    """Periodic worker thread.

    This class can be used to instantiate a worker thread that will run its `run_periodic` function every `interval`
    seconds.

    The method `on_shutdown` will be called on worker shutdown. The worker will be shutdown when the program exits and
    can be waited for with the `exit_timeout` parameter.

    """

    _DEFAULT_INTERVAL = 1.0

    def __init__(
        self,
        interval=_DEFAULT_INTERVAL,  # type: float
        name=None,  # type: Optional[str]
        daemon=True,  # type: bool
    ):
        # type: (...) -> None
        """Create a new worker thread that runs a function periodically.

        :param interval: The interval in seconds to wait between calls to `run_periodic`.
        :param name: Name of the worker.
        :param daemon: Whether the worker should be a daemon.
        """

        self._thread = threading.Thread(target=self._target, name=name)
        self._thread.daemon = daemon
        self._stop = threading.Event()
        self.started = False
        self.interval = interval

    def start(self):
        # type: () -> None
        """Start the periodic worker."""
        _LOG.debug("Starting %s thread", self._thread.name)
        self._thread.start()
        self.started = True

    def stop(self):
        # type: () -> None
        """Stop the worker."""
        _LOG.debug("Stopping %s thread", self._thread.name)
        self._stop.set()

    def is_alive(self):
        # type: () -> bool
        return self._thread.is_alive()

    def join(self, timeout=None):
        # type: (Optional[float]) -> None
        return self._thread.join(timeout)

    def _target(self):
        # type: () -> None
        while not self._stop.wait(self.interval):
            self.run_periodic()
        self._on_shutdown()

    @staticmethod
    def run_periodic():
        # type: () -> None
        """Method executed every interval."""
        pass

    def _on_shutdown(self):
        # type: () -> None
        _LOG.debug("Shutting down %s thread", self._thread.name)
        self.on_shutdown()

    @staticmethod
    def on_shutdown():
        # type: () -> None
        """Method ran on worker shutdown."""
        pass
