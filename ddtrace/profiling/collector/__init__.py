# -*- encoding: utf-8 -*-
import typing

from ddtrace.internal import service
from ddtrace.internal.settings.profiling import config
from ddtrace.profiling.collector._sampler import CaptureSampler  # noqa: F401


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
