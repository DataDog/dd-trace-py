# -*- encoding: utf-8 -*-
import threading
import typing

from ddtrace.internal import service

class PeriodicThread(threading.Thread):
    _ddtrace_profiling_ignore: bool
    def __init__(
        self,
        interval: float,
        target: typing.Callable[[], typing.Any],
        name: typing.Optional[str],
        on_shutdown: typing.Optional[typing.Callable[[], typing.Any]],
    ) -> None: ...
    def stop(self) -> None: ...
    def run(self) -> None: ...

class AwakeablePeriodicThread(PeriodicThread):
    def awake(self) -> None: ...

class PeriodicService(service.Service):
    """A service that runs periodically."""

    __thread_class__: typing.Type
    _interval: float
    interval: float
    _worker: typing.Optional[PeriodicThread]
    def __init__(self, interval: float, _worker: typing.Optional[PeriodicThread] = None) -> None: ...
    def _start_service(self, *args: typing.Any, **kwargs: typing.Any) -> None: ...
    def _stop_service(self, *args: typing.Any, **kwargs: typing.Any) -> None: ...
    def join(self, timeout: typing.Optional[float] = None) -> None: ...
    def on_shutdown(self) -> None: ...
    def periodic(self) -> None: ...

class AwakeablePeriodicService(PeriodicService):
    def awake(self) -> None: ...
