import enum

from ddtrace.vendor import attr


class ServiceStatus(enum.Enum):
    """A Service status."""

    STOPPED = "stopped"
    RUNNING = "running"


@attr.s
class Service(object):
    """A service that can be started or stopped."""

    status = attr.ib(default=ServiceStatus.STOPPED, type=ServiceStatus, init=False)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self.stop()

    def start(self):
        """Start the service."""
        if self.status == ServiceStatus.RUNNING:
            raise RuntimeError("%s is already running" % self.__class__.__name__)
        self.status = ServiceStatus.RUNNING

    def stop(self):
        """Stop the service."""
        self.status = ServiceStatus.STOPPED

    @staticmethod
    def join(timeout=None):
        """Join the service once stopped."""
