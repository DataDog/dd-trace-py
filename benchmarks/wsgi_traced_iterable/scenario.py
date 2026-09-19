from types import TracebackType
from typing import Callable
from typing import Generator
from typing import Optional

import bm

from ddtrace.contrib.internal.wsgi import wsgi as _wsgi  # noqa:F401


class _Span:
    __slots__ = ("finished",)

    def __init__(self) -> None:
        self.finished = False

    def finish(self) -> None:
        self.finished = True

    def set_exc_info(
        self,
        exc_type: type[BaseException],
        exc_val: BaseException,
        exc_tb: Optional[TracebackType],
    ) -> None:
        pass


class WSGITracedIterableScenario(bm.Scenario):
    chunks: int

    def run(self) -> Generator[Callable[[int], None], None, None]:
        from ddtrace._trace.trace_handlers import _TracedIterable

        response = (b"chunk",) * self.chunks
        span = _Span()
        parent_span = _Span()

        def traced_iterable(loops: int) -> None:
            for _ in range(loops):
                for _ in _TracedIterable(response, span, parent_span):
                    pass

        yield traced_iterable
