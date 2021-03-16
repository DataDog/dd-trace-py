import enum
import threading

from ddtrace.vendor import attr


class ServiceStatus(enum.Enum):
    """A Service status."""

    STOPPED = "stopped"
    RUNNING = "running"


class ServiceAlreadyRunning(RuntimeError):
    pass


@attr.s
class Service(object):
    """A service that can be started or stopped."""

    status = attr.ib(default=ServiceStatus.STOPPED, type=ServiceStatus, init=False, eq=False)
    _service_lock = attr.ib(factory=threading.Lock, repr=False, init=False, eq=False)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()
        self.join()

    def start(self):
        """Start the service."""
        # Use a lock so we're sure that if 2 threads try to start the service at the same time, one of them will raise
        # an error.
        with self._service_lock:
            if self.status == ServiceStatus.RUNNING:
                raise ServiceAlreadyRunning("%s is already running" % self.__class__.__name__)
            self.status = ServiceStatus.RUNNING
            self._start()

    def _start(self):
        # type: () -> None
        """Start the service for real.

        This method uses the internal lock to be sure there's no race conditions.
        """

    def stop(self):
        """Stop the service."""
        self.status = ServiceStatus.STOPPED

    @staticmethod
    def join(timeout=None):
        """Join the service once stopped."""
