import typing as t

class PeriodicThread:
    """A native periodic thread.

    The ``target`` callable is invoked repeatedly at ``interval`` seconds. If
    ``target`` returns the ``PERIODIC_STOP`` sentinel the loop exits cleanly
    after that iteration (``on_shutdown`` is still called). Returning any other
    value continues the loop; raising an exception prints the traceback and also
    stops the loop.
    """

    name: str
    ident: int
    interval: float

    def __init__(
        self,
        interval: float,
        target: t.Callable,
        name: t.Optional[str] = None,
        on_shutdown: t.Optional[t.Callable] = None,
        no_wait_at_start: bool = False,
    ) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def join(self, timeout: t.Optional[float] = None) -> None: ...
    def awake(self) -> None: ...
    def _after_fork(self, *, force: bool = False) -> None: ...
    def _before_fork(self) -> None: ...

periodic_threads: dict[int, PeriodicThread]
PERIODIC_STOP: object
