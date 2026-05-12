# -*- encoding: utf-8 -*-
import typing

from ddtrace.internal import service
from ddtrace.internal.settings.profiling import config


class CaptureSampler:
    """Determine the events that should be captured based on a sampling percentage.

    At runtime, this is replaced by the Cython extension from ``_sampler.pyx``
    when compiled extensions are available (the default). This pure-Python
    definition serves as the fallback for ``DD_CYTHONIZE=0`` builds and as the
    canonical type for static type checkers.
    """

    def __init__(self, capture_pct: float = 100.0) -> None:
        if capture_pct < 0 or capture_pct > 100:
            raise ValueError("Capture percentage should be between 0 and 100 included")
        self.capture_pct: float = capture_pct
        self._counter: float = 0.0

    def __repr__(self) -> str:
        return f"CaptureSampler(capture_pct={self.capture_pct})"

    def capture(self) -> bool:
        self._counter += self.capture_pct
        if self._counter >= 100:
            self._counter -= 100
            return True
        return False


if not typing.TYPE_CHECKING:
    try:
        from ddtrace.profiling.collector._sampler import CaptureSampler  # noqa: F811
    except ImportError:
        pass


class CollectorError(Exception):
    pass


class CollectorUnavailable(CollectorError):
    pass


class Collector(service.Service):
    """A profile collector."""

    def __init__(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        super().__init__(*args, **kwargs)

    @staticmethod
    def snapshot() -> None:
        """Take a snapshot of collected data, to be exported."""


class CaptureSamplerCollector(Collector):
    def __init__(self, capture_pct: float = config.capture_pct, *args: typing.Any, **kwargs: typing.Any) -> None:
        super().__init__(*args, **kwargs)
        self.capture_pct = capture_pct
        self._capture_sampler = CaptureSampler(self.capture_pct)
