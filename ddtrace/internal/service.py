# -*- encoding: utf-8 -*-
import abc
import enum
import threading
import typing

import attr
import six

from ddtrace.internal import atexit
from ddtrace.internal import forksafe
from ddtrace.internal import uwsgi

from .logger import get_logger


log = get_logger(__name__)


class ServiceStatus(enum.Enum):
    """A Service status."""

    STOPPED = "stopped"
    RUNNING = "running"


class ServiceStatusError(RuntimeError):
    def __init__(
        self,
        service_cls,  # type: typing.Type[Service]
        current_status,  # type: ServiceStatus
    ):
        # type: (...) -> None
        self.current_status = current_status
        super(ServiceStatusError, self).__init__(
            "%s is already in status %s" % (service_cls.__name__, current_status.value)
        )


@attr.s(eq=False)
class Service(six.with_metaclass(abc.ABCMeta)):
    """A service that can be started or stopped."""

    status = attr.ib(default=ServiceStatus.STOPPED, type=ServiceStatus, init=False, eq=False)
    _service_lock = attr.ib(factory=threading.Lock, repr=False, init=False, eq=False, type=threading.Lock)

    def __enter__(self):
        # type: (...) -> Service
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()
        self.join()

    def start(
        self,
        *args,  # type: typing.Any
        **kwargs  # type: typing.Any
    ):
        # type: (...) -> None
        """Start the service."""
        # Use a lock so we're sure that if 2 threads try to start the service at the same time, one of them will raise
        # an error.
        with self._service_lock:
            if self.status == ServiceStatus.RUNNING:
                raise ServiceStatusError(self.__class__, self.status)
            self._start_service(*args, **kwargs)
            self.status = ServiceStatus.RUNNING

    @abc.abstractmethod
    def _start_service(
        self,
        *args,  # type: typing.Any
        **kwargs  # type: typing.Any
    ):
        # type: (...) -> None
        """Start the service for real.

        This method uses the internal lock to be sure there's no race conditions and that the service is really started
        once start() returns.

        """

    def stop(
        self,
        *args,  # type: typing.Any
        **kwargs  # type: typing.Any
    ):
        # type: (...) -> None
        """Stop the service."""
        with self._service_lock:
            if self.status == ServiceStatus.STOPPED:
                raise ServiceStatusError(self.__class__, self.status)
            self._stop_service(*args, **kwargs)
            self.status = ServiceStatus.STOPPED

    @abc.abstractmethod
    def _stop_service(
        self,
        *args,  # type: typing.Any
        **kwargs  # type: typing.Any
    ):
        # type: (...) -> None
        """Stop the service for real.

        This method uses the internal lock to be sure there's no race conditions and that the service is really stopped
        once start() returns.

        """

    def join(
        self, timeout=None  # type: typing.Optional[float]
    ):
        # type: (...) -> None
        """Join the service once stopped."""

    def copy(self):
        # type: (...) -> Service
        return attr.evolve(self)


class ForksafeService(object):
    """A manager that can handle the lifecycle of a Service in a process.

    This allows to handle a service that should keep running when, e.g., the Python process forks.

    This class is not thread-safe.
    """

    SERVICE_CLASS = None  # type: typing.ClassVar[typing.Type[Service]]

    def __init__(
        self,
        *args,  # type: typing.Any
        **kwargs  # type: typing.Any
    ):
        # type: (...) -> None
        super(ForksafeService, self).__init__()
        self._service = self.SERVICE_CLASS(*args, **kwargs)  # type: ignore[call-arg]

    def start(self, *args, **kwargs):
        # type: (...) -> None
        """Start the service."""
        log.debug("Starting service %r", self._service)
        stop_on_exit = kwargs.pop("stop_on_exit", True)
        self.start_in_children = kwargs.pop("start_in_children", True)
        try:
            uwsgi.check_uwsgi(self._start_in_child, atexit=self.stop if stop_on_exit else None)
        except uwsgi.uWSGIMasterProcess:
            # Do nothing in the uWSGI master process, the start() method will be called in each worker subprocess if
            # asked for it via `start_in_children`.
            return

        self._service.start(*args, **kwargs)

        if stop_on_exit:
            atexit.register(self.stop)

        forksafe.register(self._restart_on_fork)

    def stop(self):
        # type: (...) -> None
        """Stop the service."""
        log.debug("Stopping service %r", self._service)
        atexit.unregister(self.stop)

        try:
            forksafe.unregister(self._restart_on_fork)
        except ValueError:
            pass

        self._service.stop()

    def __getattr__(
        self,
        k,  # type: typing.Any
    ):
        # type: (...) -> typing.Any
        return getattr(self._service, k)

    def _start_in_child(self):
        # type: (...) -> None
        if self.start_in_children:
            log.debug("Starting %r in child process", self._service)
            self._service = self._service.copy()
            self.start()

    def _restart_on_fork(self):
        # type: (...) -> None
        # Be sure to stop the parent first:
        # Parent might have to "undo" some work before the child can start.
        atexit.unregister(self.stop)
        try:
            self._service.stop()
        except ServiceStatusError:
            log.error("Unable to stop service %r", self._service, exc_info=True)
        self._start_in_child()
