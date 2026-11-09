from functools import partial
from types import TracebackType
from typing import Callable
from typing import Generator
from typing import Optional

import bm

# CI runs this harness against published baselines that still own spans in the iterable.
from ddtrace.contrib.internal.wsgi import wsgi as _wsgi
from ddtrace.internal.compat import ExcInfoType


_response_iterable = getattr(_wsgi, "_WSGIResponseIterable", None)


class _Span:
    """Keep legacy span work minimal to isolate response-wrapper overhead."""

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


def _complete(exc_info: ExcInfoType) -> None:
    """Keep completion work minimal to isolate response-wrapper overhead.

    The flask_streaming benchmark covers the full request path with real spans.
    """


# The isolated benchmark runner supplies bm, which is untyped in the repository lint environment.
class WSGITracedIterableScenario(bm.Scenario):  # type: ignore[misc]
    chunks: int

    def run(self) -> Generator[Callable[[int], None], None, None]:
        response = (b"chunk",) * self.chunks
        if _response_iterable is not None:
            wrap_response = partial(_response_iterable, on_complete=_complete)
        else:
            from ddtrace._trace import trace_handlers

            wrap_response = partial(getattr(trace_handlers, "_TracedIterable"), span=_Span(), parent_span=_Span())

        def response_iterable(loops: int) -> None:
            for _ in range(loops):
                for _ in wrap_response(response):
                    pass

        yield response_iterable
